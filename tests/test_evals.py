from __future__ import annotations

import json
from pathlib import Path

import pytest

from derva.evals import metrics as m
from derva.evals.datasets import (
    banking77_questions,
    banking77_sample,
    golden,
    injection_seed,
    select,
    split_of,
)
from derva.evals.gate import evaluate, load_criteria
from derva.evals.runner import ROOT, load_summary, run


def rec(label: str, predicted: str | None, p_top: float | None = None, **kw: object) -> m.Record:
    return m.Record(
        id=f"{label}-{predicted}-{p_top}",
        split="heldout",
        label=label,
        predicted=predicted,
        p_top=p_top,
        **kw,
    )


def test_split_is_stable_and_roughly_forty_sixty() -> None:
    ids = [f"x-{i}" for i in range(2000)]
    assert [split_of(i) for i in ids] == [split_of(i) for i in ids]
    share = sum(split_of(i) == "tuning" for i in ids) / len(ids)
    assert 0.36 < share < 0.44


def test_select_filters_by_split() -> None:
    examples = golden()
    tuning, heldout = select(examples, "tuning"), select(examples, "heldout")
    assert len(tuning) + len(heldout) == len(examples)
    assert {e.id for e in tuning}.isdisjoint({e.id for e in heldout})


def test_committed_datasets_have_the_expected_shape() -> None:
    sample = banking77_sample()
    assert len(sample) == 154 and len({e.label for e in sample}) == 77
    assert len(banking77_questions().questions[0].options) == 77
    seed = injection_seed()
    assert {e.label for e in seed} == {"yes", "no"}
    assert {e.label for e in golden()} <= {
        "block",
        "review",
        "clarify",
        "decline",
        "small_model",
        "frontier_model",
    }


def test_calibration_metrics_on_a_known_case() -> None:
    # Says 0.9 and is right 9 times in 10; says 0.6 and is right 3 times in 5.
    records = [rec("a", "a", 0.9) for _ in range(9)] + [rec("a", "b", 0.9)]
    records += [rec("a", "a", 0.6) for _ in range(3)] + [rec("a", "b", 0.6) for _ in range(2)]
    assert m.expected_calibration_error(records) == pytest.approx(0.0)
    off = [rec("a", "b", 0.95) for _ in range(4)]
    assert m.expected_calibration_error(off) == pytest.approx(0.95)


def test_brier_is_zero_when_certain_and_right() -> None:
    assert m.brier([rec("a", "a", 1.0, p_label=1.0, sum_sq=1.0)]) == 0.0
    assert m.brier([rec("a", "b", 1.0, p_label=0.0, sum_sq=1.0)]) == 2.0


def test_automation_curve_and_coverage() -> None:
    records = [rec("a", "a", 0.95)] * 6 + [rec("a", "b", 0.5)] * 4
    curve = m.automation_curve(records)
    assert m.coverage_at_accuracy(curve, 0.95) == pytest.approx(0.6)
    assert curve[0].coverage == 1.0 and curve[0].accuracy == pytest.approx(0.6)


def test_safety_rates() -> None:
    records = [
        rec("block", "small_model"),
        rec("block", "block"),
        rec("small_model", "review"),
        rec("small_model", "small_model"),
    ]
    assert m.unsafe_pass_rate(records) == 0.5
    assert m.over_hold_rate(records) == 0.5


async def test_golden_run_writes_results_and_matches_the_baseline(tmp_path: Path) -> None:
    summary, _ = await run("golden", "rules", split="all", results_dir=tmp_path)
    run_dir = tmp_path / "runs" / summary.run_id
    records = (run_dir / "records.jsonl").read_text().splitlines()
    assert (run_dir / "summary.json").exists() and len(records) == len(golden())
    # Outcomes carry no probability, so there is no automation curve to read.
    assert summary.coverage_at_95 is None and summary.ece is None
    assert json.loads((tmp_path / "log.jsonl").read_text())["run_id"] == summary.run_id
    baseline = load_summary(ROOT / "evals" / "baselines" / "golden-rules.json")
    checks = evaluate(summary, "regression", baseline)
    assert checks and all(c.passed for c in checks), [c.describe() for c in checks if not c.passed]


async def test_injection_run_reports_policy_rates(tmp_path: Path) -> None:
    summary, _ = await run("injection", "rules", split="all", results_dir=tmp_path, write=False)
    for key in ("flag_recall", "block_recall", "false_flag_rate", "false_block_rate"):
        assert 0.0 <= summary.extra[key] <= 1.0
    assert summary.coverage_at_95 == 0.0  # probabilities exist; the rules never reach 95%


def test_gate_rules() -> None:
    from derva.evals.gate import _check

    assert _check("x", "min", 0.9, 0.95, None).passed
    assert not _check("x", "max", 0.1, 0.2, None).passed
    assert _check("x", "max_drop", 0.03, 0.80, 0.82).passed
    assert not _check("x", "max_drop", 0.03, 0.70, 0.82).passed
    assert _check("x", "max_ratio", 0.5, 1.0, 4.0).passed
    assert not _check("x", "min", 0.9, None, None).passed  # missing fails
    assert not _check("x", "max_drop", 0.0, 0.9, None).passed  # no baseline fails


async def test_gate_refuses_to_compare_different_splits(tmp_path: Path) -> None:
    a, _ = await run("golden", "rules", split="tuning", write=False)
    b, _ = await run("golden", "rules", split="heldout", write=False)
    with pytest.raises(ValueError, match="not comparable"):
        evaluate(a, "adoption", b, load_criteria())
