"""The audit log: immutable events, one per state change, in a hash chain.

Every state change of a request (received, decided, held, approved, executed and
so on) is one event linked by ``request_id``. Each event's hash covers its own
content and the previous event's hash, so editing or deleting any past event
breaks the chain from that point on, and ``verify()`` says where.

Defences, from outermost to innermost:

1. The gateway writes through a database role that can only INSERT and SELECT
   (see ``sql/audit_schema.sql``).
2. Triggers reject UPDATE, DELETE and TRUNCATE on the table, even for its owner.
3. The hash chain detects any change that gets past both, for example by someone
   with superuser access. Copy the chain head (``derva audit anchor``) to a
   retention-locked storage bucket on a schedule, so even a rewritten chain can
   be caught against an anchor the database admin cannot alter.

Payloads hold masked text and decision data, never raw personal data. Masking is
imperfect, so a retention period applies to this table too.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

GENESIS = "0" * 64
_ADVISORY_LOCK_KEY = 7_300_421  # serialises chain appends across gateway instances


class EventType(StrEnum):
    RECEIVED = "received"
    RESOLVED = "resolved"
    DECIDED = "decided"
    ROUTED = "routed"
    BLOCKED = "blocked"
    HELD = "held"
    CLARIFY = "clarify"
    TOOL_PROPOSED = "tool_proposed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


class AuditEvent(BaseModel):
    seq: int
    event_id: str
    request_id: str
    ts: str
    type: EventType
    actor: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class ChainCheck(BaseModel):
    ok: bool
    events_checked: int
    first_bad_seq: int | None = None
    reason: str | None = None


def canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()


def event_hash(fields: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(fields)).hexdigest()


metadata = MetaData()
audit_events = Table(
    "audit_events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("event_id", String(32), nullable=False, unique=True),
    Column("request_id", String(64), nullable=False, index=True),
    Column("ts", String(40), nullable=False),
    Column("type", String(32), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("payload", Text, nullable=False),
    Column("prev_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
)

_SQLITE_GUARDS = [
    """CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
]


class AuditStore:
    """Append-only, hash-chained event store on SQLAlchemy (Postgres or SQLite)."""

    def __init__(self, url: str = "sqlite://", *, create_schema: bool = True) -> None:
        if url in ("sqlite://", "sqlite:///:memory:"):
            self.engine: Engine = create_engine(
                "sqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
        else:
            self.engine = create_engine(url)
        self._lock = threading.Lock()
        if create_schema and self.engine.dialect.name == "sqlite":
            metadata.create_all(self.engine)
            with self.engine.begin() as conn:
                for ddl in _SQLITE_GUARDS:
                    conn.execute(text(ddl))
        # Postgres schema, triggers and roles come from sql/audit_schema.sql,
        # applied by the deploy, so the gateway's own role cannot alter them.

    def append(
        self,
        request_id: str,
        type: EventType,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        payload = payload or {}
        with self._lock, self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
            last = conn.execute(
                select(audit_events.c.seq, audit_events.c.hash)
                .order_by(audit_events.c.seq.desc())
                .limit(1)
            ).first()
            seq = (last.seq + 1) if last else 1
            prev = last.hash if last else GENESIS
            fields = {
                "seq": seq,
                "event_id": uuid.uuid4().hex,
                "request_id": request_id,
                "ts": datetime.now(UTC).isoformat(),
                "type": type.value,
                "actor": actor,
                "payload": payload,
                "prev_hash": prev,
            }
            digest = event_hash(fields)
            conn.execute(
                audit_events.insert().values(
                    **{**fields, "payload": json.dumps(payload, sort_keys=True, default=str)},
                    hash=digest,
                )
            )
        return AuditEvent.model_validate({**fields, "hash": digest})

    def events(self, request_id: str | None = None) -> list[AuditEvent]:
        query = select(audit_events).order_by(audit_events.c.seq)
        if request_id is not None:
            query = query.where(audit_events.c.request_id == request_id)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [AuditEvent(**{**row, "payload": json.loads(row["payload"])}) for row in rows]

    def request_state(self, request_id: str) -> EventType | None:
        events = self.events(request_id)
        return events[-1].type if events else None

    def head(self) -> dict[str, Any]:
        """The chain head, to copy to a retention-locked bucket as an anchor."""
        with self.engine.connect() as conn:
            last = conn.execute(
                select(audit_events.c.seq, audit_events.c.hash)
                .order_by(audit_events.c.seq.desc())
                .limit(1)
            ).first()
        return {
            "seq": last.seq if last else 0,
            "hash": last.hash if last else GENESIS,
            "anchored_at": datetime.now(UTC).isoformat(),
        }

    def verify(self, anchor: dict[str, Any] | None = None) -> ChainCheck:
        """Recompute every hash and link. With an anchor, also check the chain still ends in it."""
        prev = GENESIS
        expected_seq = 1
        checked = 0
        for ev in self.events():
            fields = ev.model_dump(exclude={"hash"})
            fields["type"] = ev.type.value
            if ev.seq != expected_seq:
                return ChainCheck(
                    ok=False,
                    events_checked=checked,
                    first_bad_seq=ev.seq,
                    reason=f"sequence gap: expected {expected_seq}",
                )
            if ev.prev_hash != prev:
                return ChainCheck(
                    ok=False,
                    events_checked=checked,
                    first_bad_seq=ev.seq,
                    reason="previous-hash link broken",
                )
            if event_hash(fields) != ev.hash:
                return ChainCheck(
                    ok=False,
                    events_checked=checked,
                    first_bad_seq=ev.seq,
                    reason="event content does not match its hash",
                )
            if anchor and ev.seq == anchor["seq"] and ev.hash != anchor["hash"]:
                return ChainCheck(
                    ok=False,
                    events_checked=checked,
                    first_bad_seq=ev.seq,
                    reason="chain differs from the stored anchor",
                )
            prev = ev.hash
            expected_seq += 1
            checked += 1
        if anchor and anchor["seq"] > checked:
            return ChainCheck(
                ok=False,
                events_checked=checked,
                first_bad_seq=checked + 1,
                reason="events the anchor vouches for are missing",
            )
        return ChainCheck(ok=True, events_checked=checked)
