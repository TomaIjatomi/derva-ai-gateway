"""A response cache for evaluation runs.

The CI eval gate re-runs the same items on every pull request. Caching answers
by decider, question set and input means unchanged items cost nothing and return
identical answers, so the gate measures changes in the code, not provider noise.

Never use this in the live gateway: a cached answer to a live request would hide
a model change from the audit log.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from derva.deciders.base import Decider
from derva.types import DecisionResult, QuestionSet


class CachingDecider:
    def __init__(self, inner: Decider, path: Path) -> None:
        self.inner = inner
        self.name = inner.name
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, result TEXT)")
        self.hits = 0
        self.misses = 0

    def _key(self, state: str, questions: QuestionSet) -> str:
        material = json.dumps(
            {
                "decider": self.inner.name,
                "version": questions.version,
                "questions": questions.to_wire(),
                "state": state,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode()).hexdigest()

    async def decide(self, state: str, questions: QuestionSet) -> DecisionResult:
        key = self._key(state, questions)
        row = self._db.execute("SELECT result FROM answers WHERE key = ?", (key,)).fetchone()
        if row:
            self.hits += 1
            return DecisionResult.model_validate_json(row[0])
        self.misses += 1
        result = await self.inner.decide(state, questions)
        self._db.execute(
            "INSERT OR REPLACE INTO answers VALUES (?, ?)", (key, result.model_dump_json())
        )
        self._db.commit()
        return result
