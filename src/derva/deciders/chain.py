"""The fallback chain: decision model, then a small LLM, then rules.

Each link has its own timeout and circuit breaker. After repeated failures a
link is skipped for a cooldown period, then a single request probes whether it
has recovered. If every link fails, ``AllDecidersFailed`` is raised and the
policy engine fails closed: nothing goes straight through.

The result records which link answered and why earlier links were skipped, so
the audit log shows how often the gateway ran on its fallbacks.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from derva.deciders.base import Decider, DeciderError
from derva.types import DecisionResult, QuestionSet


class AllDecidersFailed(Exception):
    def __init__(self, failures: list[str]) -> None:
        super().__init__("; ".join(failures))
        self.failures = failures


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at >= self.cooldown_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.failure_threshold:
            self.opened_at = self.clock()


@dataclass
class Link:
    decider: Decider
    timeout_s: float
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


class FallbackChain:
    def __init__(self, links: list[Link], name: str = "chain") -> None:
        if not links:
            raise ValueError("a fallback chain needs at least one decider")
        self.name = name
        self.links = links

    async def decide(self, state: str, questions: QuestionSet) -> DecisionResult:
        failures: list[str] = []
        for link in self.links:
            label = link.decider.name
            if not link.breaker.allow():
                failures.append(f"{label}: skipped, circuit open")
                continue
            try:
                result = await asyncio.wait_for(
                    link.decider.decide(state, questions), timeout=link.timeout_s
                )
            except TimeoutError:
                link.breaker.record_failure()
                failures.append(f"{label}: timed out after {link.timeout_s}s")
                continue
            except DeciderError as exc:
                link.breaker.record_failure()
                failures.append(f"{label}: {exc}")
                continue
            link.breaker.record_success()
            result.fallbacks = failures
            return result
        raise AllDecidersFailed(failures)
