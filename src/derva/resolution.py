"""Client resolution: turn client names into stable, caller-scoped references.

This runs before masking. Each agent step is a separate gateway request, so a
name-to-placeholder mapping that lives for one request is gone by the next. A
stable reference such as ``client:C-1042`` is safe to keep in workflow state:
tools take references, never names or addresses, and look up personal details
from the CRM only when they run.

Outcomes:

* exactly one client the caller may act for matches: the name is replaced
* several clients the caller may act for match: ask the user which one
* only clients outside the caller's scope match: refuse, without confirming
  that the client exists
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

REFERENCE_PATTERN = re.compile(r"\bclient:C-\d+\b")


def reference(client_id: str) -> str:
    return f"client:{client_id}"


class ClientRecord(BaseModel):
    client_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    email: str
    owner_team: str


class Caller(BaseModel):
    """Who is calling the gateway. ``owner`` is the human identity behind the key."""

    key_id: str
    owner: str
    teams: list[str] = Field(default_factory=list)

    def may_act_for(self, record: ClientRecord) -> bool:
        return record.owner_team in self.teams


class ClientDirectory(Protocol):
    def all(self) -> Iterable[ClientRecord]: ...

    def get(self, client_id: str) -> ClientRecord | None: ...


class InMemoryDirectory:
    def __init__(self, records: Iterable[ClientRecord]) -> None:
        self._records = {r.client_id: r for r in records}

    @classmethod
    def from_json(cls, path: Path) -> InMemoryDirectory:
        return cls(ClientRecord.model_validate(r) for r in json.loads(path.read_text()))

    def all(self) -> Iterable[ClientRecord]:
        return self._records.values()

    def get(self, client_id: str) -> ClientRecord | None:
        return self._records.get(client_id)


class ResolutionStatus(StrEnum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"
    DENIED = "denied"


class ResolutionResult(BaseModel):
    status: ResolutionStatus
    text: str
    references: list[str] = Field(default_factory=list)
    # Matched name -> candidate references the caller may act for.
    ambiguous: dict[str, list[str]] = Field(default_factory=dict)
    # The names are never echoed back or logged. The references of out-of-scope
    # clients are kept for the audit log, so an investigation can see whose data
    # a refused request was after.
    denied_count: int = 0
    denied: list[str] = Field(default_factory=list)


def resolve_clients(text: str, caller: Caller, directory: ClientDirectory) -> ResolutionResult:
    """Replace client names in ``text`` with references the caller is allowed to use."""
    index: dict[str, list[ClientRecord]] = {}
    for record in directory.all():
        for name in (record.name, *record.aliases):
            index.setdefault(name.casefold(), []).append(record)

    # Longest names first, so "Mary Byrne" wins over a shorter alias inside it.
    names = sorted(index, key=len, reverse=True)
    if not names:
        return ResolutionResult(status=ResolutionStatus.OK, text=text)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", flags=re.IGNORECASE
    )

    references: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    denied = 0
    denied_refs: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        nonlocal denied
        candidates = index[match.group(0).casefold()]
        allowed = [r for r in candidates if caller.may_act_for(r)]
        if len(allowed) == 1:
            ref = reference(allowed[0].client_id)
            references.append(ref)
            return ref
        if len(allowed) > 1:
            ambiguous[match.group(0)] = sorted(reference(r.client_id) for r in allowed)
            return match.group(0)
        denied += 1
        denied_refs.extend(reference(r.client_id) for r in candidates)
        return match.group(0)

    resolved = pattern.sub(_replace, text)
    if denied:
        status = ResolutionStatus.DENIED
    elif ambiguous:
        status = ResolutionStatus.AMBIGUOUS
    else:
        status = ResolutionStatus.OK
    return ResolutionResult(
        status=status,
        text=resolved,
        references=sorted(set(references)),
        ambiguous=ambiguous,
        denied_count=denied,
        denied=sorted(set(denied_refs)),
    )
