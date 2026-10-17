from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from derva.audit import AuditStore, EventType


def fill(store: AuditStore, n: int = 5) -> None:
    for i in range(n):
        store.append(f"req-{i % 2}", EventType.RECEIVED, "key-1", {"i": i})


def test_chain_verifies(audit: AuditStore) -> None:
    fill(audit)
    check = audit.verify()
    assert check.ok and check.events_checked == 5


def test_events_are_linked(audit: AuditStore) -> None:
    fill(audit, 3)
    events = audit.events()
    assert events[0].prev_hash == "0" * 64
    assert events[1].prev_hash == events[0].hash
    assert [e.seq for e in events] == [1, 2, 3]


def test_updates_and_deletes_are_rejected(audit: AuditStore) -> None:
    fill(audit, 2)
    with pytest.raises(DatabaseError, match="append-only"), audit.engine.begin() as conn:
        conn.execute(text("UPDATE audit_events SET actor = 'mallory' WHERE seq = 1"))
    with pytest.raises(DatabaseError, match="append-only"), audit.engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_events WHERE seq = 2"))


def test_tampering_that_bypasses_the_triggers_is_detected(audit: AuditStore) -> None:
    fill(audit)
    with audit.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER audit_events_no_update"))
        conn.execute(text("""UPDATE audit_events SET payload = '{"i": 99}' WHERE seq = 3"""))
    check = audit.verify()
    assert not check.ok
    assert check.first_bad_seq == 3
    assert check.reason == "event content does not match its hash"


def test_deletion_at_the_end_is_caught_by_the_anchor(audit: AuditStore) -> None:
    fill(audit, 4)
    anchor = audit.head()
    with audit.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER audit_events_no_delete"))
        conn.execute(text("DELETE FROM audit_events WHERE seq = 4"))
    assert audit.verify().ok  # the chain alone cannot see a truncated tail...
    check = audit.verify(anchor)
    assert not check.ok  # ...but the external anchor can
    assert check.reason == "events the anchor vouches for are missing"


def test_request_state_is_the_latest_event(audit: AuditStore) -> None:
    audit.append("r1", EventType.RECEIVED, "k")
    audit.append("r1", EventType.HELD, "gateway")
    assert audit.request_state("r1") is EventType.HELD
    assert audit.request_state("nope") is None
