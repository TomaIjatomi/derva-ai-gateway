"""Spacing calls to a rate-limited provider, for evaluation runs.

Free tiers cap requests a minute (Gemini's Flash-Lite free tier allows 15).
The eval runner sends several examples at once, so without spacing most of a
run would come back as rate-limit errors, and errors fail the adoption gate.
This wrapper starts at most ``rpm`` calls a minute, evenly spaced. Put it inside
the cache, so cached answers are not slowed down. It is not used in the live
gateway, where a slow decider is cut off by its timeout instead.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from derva.deciders.base import Decider
from derva.types import DecisionResult, QuestionSet

# Seconds the current task spent waiting for its turn, so a caller timing a
# whole request (the golden pipeline) can leave the wait out of its latency.
rate_wait_s: ContextVar[float] = ContextVar("rate_wait_s", default=0.0)


class RateLimitedDecider:
    def __init__(
        self,
        inner: Decider,
        rpm: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.inner = inner
        self.name = inner.name
        self.interval_s = 60.0 / rpm
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_start = 0.0

    async def decide(self, state: str, questions: QuestionSet) -> DecisionResult:
        queued_at = self._clock()
        async with self._lock:
            start = max(self._clock(), self._next_start)
            wait = start - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._next_start = start + self.interval_s
        rate_wait_s.set(rate_wait_s.get() + max(0.0, self._clock() - queued_at))
        return await self.inner.decide(state, questions)
