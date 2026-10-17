"""Check a run against criteria written down before the run.

Two kinds of check, both read from ``evals/criteria.yaml``:

* ``regression`` (CI): a change must not make the offline gateway worse than
  the committed baseline run.
* ``adoption``: what a candidate decider must show on the held-out split before
  it goes into the chain ahead of the current default.

Rules: ``min``/``max`` are absolute; ``max_drop``/``max_rise``/``min_gain`` and
``max_ratio`` compare with a baseline run. Metric names reach into ``extra``
with a dot (``extra.flag_recall``). Missing values fail rather than pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from derva.config import CONFIG_DIR
from derva.evals.runner import RunSummary

CRITERIA = CONFIG_DIR.parent / "evals" / "criteria.yaml"
Mode = Literal["regression", "adoption"]
_EPS = 1e-9


class Check(BaseModel):
    metric: str
    rule: str
    limit: float
    value: float | None
    baseline: float | None
    passed: bool

    def describe(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        base = f" (baseline {self.baseline:.4g})" if self.baseline is not None else ""
        value = "missing" if self.value is None else f"{self.value:.4g}"
        return f"{mark}  {self.metric}: {value}{base}  [{self.rule} {self.limit:g}]"


def metric(summary: RunSummary, name: str) -> float | None:
    data: Any = summary.model_dump()
    for part in name.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return float(data) if isinstance(data, int | float) else None


def _check(name: str, rule: str, limit: float, value: float | None, base: float | None) -> Check:
    comparative = rule in {"max_drop", "max_rise", "min_gain", "max_ratio"}
    if value is None or (comparative and base is None):
        return Check(metric=name, rule=rule, limit=limit, value=value, baseline=base, passed=False)
    assert value is not None
    if rule == "min":
        ok = value >= limit - _EPS
    elif rule == "max":
        ok = value <= limit + _EPS
    elif rule == "max_drop":
        assert base is not None
        ok = base - value <= limit + _EPS
    elif rule == "max_rise":
        assert base is not None
        ok = value - base <= limit + _EPS
    elif rule == "min_gain":
        assert base is not None
        ok = value - base >= limit - _EPS
    elif rule == "max_ratio":
        assert base is not None
        ok = value <= limit * base + _EPS if base > 0 else value <= _EPS
    else:
        raise ValueError(f"Unknown rule {rule!r} for {name}")
    return Check(metric=name, rule=rule, limit=limit, value=value, baseline=base, passed=ok)


def load_criteria(path: Path = CRITERIA) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text()))


def evaluate(
    summary: RunSummary,
    mode: Mode,
    baseline: RunSummary | None = None,
    criteria: dict[str, Any] | None = None,
) -> list[Check]:
    criteria = criteria or load_criteria()
    if baseline is not None and (baseline.task, baseline.split) != (summary.task, summary.split):
        raise ValueError(
            f"Baseline is {baseline.task}/{baseline.split}, run is "
            f"{summary.task}/{summary.split}: not comparable."
        )
    section = criteria.get(mode, {})
    rules: dict[str, dict[str, float]] = {**section.get("any", {}), **section.get(summary.task, {})}
    checks = []
    for name, spec in rules.items():
        for rule, limit in spec.items():
            checks.append(
                _check(
                    name,
                    rule,
                    float(limit),
                    metric(summary, name),
                    metric(baseline, name) if baseline else None,
                )
            )
    return checks
