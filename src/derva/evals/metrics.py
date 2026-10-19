"""Metrics for deciders: accuracy, calibration, cost of automation.

Calibration uses the probability of the top answer, which every decider
produces in the same way. The providers' own ``confidence`` figures use
different formulas, so they are recorded but not compared directly.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel


class Record(BaseModel):
    """One example scored by one decider."""

    id: str
    split: str
    category: str | None = None
    label: str
    predicted: str | None
    p_top: float | None = None
    p_label: float | None = None
    sum_sq: float | None = None  # sum of squared probabilities, for the Brier score
    confidence: float | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    fallbacks: list[str] = []
    error: str | None = None

    @property
    def correct(self) -> bool:
        return self.predicted == self.label


class ReliabilityBin(BaseModel):
    lower: float
    upper: float
    count: int
    mean_confidence: float | None
    accuracy: float | None


class AutomationPoint(BaseModel):
    threshold: float
    coverage: float  # share of examples decided automatically
    accuracy: float | None  # accuracy on those


def accuracy(records: Sequence[Record]) -> float:
    return sum(r.correct for r in records) / len(records) if records else 0.0


def macro_f1(records: Sequence[Record]) -> float:
    labels = {r.label for r in records}
    scores = []
    for label in labels:
        tp = sum(r.predicted == label and r.label == label for r in records)
        fp = sum(r.predicted == label and r.label != label for r in records)
        fn = sum(r.predicted != label and r.label == label for r in records)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def reliability(records: Sequence[Record], bins: int = 10) -> list[ReliabilityBin]:
    scored = [r for r in records if r.p_top is not None]
    out = []
    for i in range(bins):
        lower, upper = i / bins, (i + 1) / bins
        inside = [
            r
            for r in scored
            if r.p_top is not None and (lower < r.p_top <= upper or (i == 0 and r.p_top == 0))
        ]
        out.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(inside),
                mean_confidence=statistics.fmean(r.p_top for r in inside if r.p_top is not None)
                if inside
                else None,
                accuracy=accuracy(inside) if inside else None,
            )
        )
    return out


def expected_calibration_error(records: Sequence[Record], bins: int = 10) -> float | None:
    scored = [r for r in records if r.p_top is not None]
    if not scored:
        return None
    total = len(scored)
    return sum(
        b.count / total * abs(b.accuracy - b.mean_confidence)
        for b in reliability(scored, bins)
        if b.count and b.accuracy is not None and b.mean_confidence is not None
    )


def brier(records: Sequence[Record]) -> float | None:
    """Multi-class Brier score: sum over options of (p - y)^2, averaged."""
    scored = [r for r in records if r.sum_sq is not None and r.p_label is not None]
    if not scored:
        return None
    return statistics.fmean(
        r.sum_sq - 2 * r.p_label + 1
        for r in scored
        if r.sum_sq is not None and r.p_label is not None
    )


DEFAULT_THRESHOLDS = tuple(step / 20 for step in range(20))


def automation_curve(
    records: Sequence[Record], thresholds: Sequence[float] = DEFAULT_THRESHOLDS
) -> list[AutomationPoint]:
    """Decide automatically when p_top >= threshold; send the rest to a person."""
    scored = [r for r in records if r.p_top is not None]
    points = []
    for t in thresholds:
        auto = [r for r in scored if r.p_top is not None and r.p_top >= t]
        points.append(
            AutomationPoint(
                threshold=t,
                coverage=len(auto) / len(scored) if scored else 0.0,
                accuracy=accuracy(auto) if auto else None,
            )
        )
    return points


def coverage_at_accuracy(curve: Sequence[AutomationPoint], target: float) -> float:
    """The largest share that can be automated while keeping accuracy at the target."""
    eligible = [p.coverage for p in curve if p.accuracy is not None and p.accuracy >= target]
    return max(eligible, default=0.0)


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q / 100 * (len(ordered) - 1))))
    return ordered[index]


def confusion(records: Sequence[Record]) -> dict[str, dict[str, int]]:
    table: dict[str, Counter[str]] = {}
    for r in records:
        table.setdefault(r.label, Counter())[r.predicted or "error"] += 1
    return {label: dict(counts) for label, counts in sorted(table.items())}


def per_category(records: Sequence[Record]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[Record]] = {}
    for r in records:
        groups.setdefault(r.category or "-", []).append(r)
    return {k: {"n": len(v), "accuracy": accuracy(v)} for k, v in sorted(groups.items())}


# Outcomes that stop or hold a request, for the golden set's safety metrics.
STOPPING = {"block", "review", "clarify", "decline"}


def unsafe_pass_rate(records: Sequence[Record]) -> float:
    """Of the cases that should have been stopped or held, the share that went through."""
    should_stop = [r for r in records if r.label in STOPPING]
    passed = [r for r in should_stop if r.predicted not in STOPPING]
    return len(passed) / len(should_stop) if should_stop else 0.0


def over_hold_rate(records: Sequence[Record]) -> float:
    """Of the cases that should have gone through, the share that was stopped or held."""
    should_pass = [r for r in records if r.label not in STOPPING]
    held = [r for r in should_pass if r.predicted in STOPPING]
    return len(held) / len(should_pass) if should_pass else 0.0
