"""Run a decider over a dataset and write the results.

Every run writes ``results/runs/<run_id>/summary.json`` and ``records.jsonl``
and appends one line to ``results/log.jsonl``. Runs against the mock decider
are marked ``synthetic_decider: true``: they check that the harness works and
must never be reported as findings.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from derva.audit import AuditStore
from derva.config import CONFIG_DIR, load_questions, load_thresholds
from derva.deciders.base import Decider
from derva.deciders.cache import CachingDecider
from derva.deciders.llm import LLMDecider
from derva.deciders.ratelimit import RateLimitedDecider, rate_wait_s
from derva.deciders.rules import RulesDecider
from derva.deciders.systemone import LiteLLMDecisionDecider
from derva.evals import metrics as m
from derva.evals.datasets import Example, Split, Task, banking77_questions, load, select, split_of
from derva.masking import Masker, RegexMasker
from derva.pipeline import GatewayRequest, Pipeline
from derva.resolution import Caller, InMemoryDirectory
from derva.types import Answer, QuestionSet

ROOT = CONFIG_DIR.parent
RESULTS = ROOT / "results"
FIXTURE_CALLER = Caller(key_id="eval", owner="eval@example.ie", teams=["wealth-a"])

PRESETS = {
    "rules": "the rules decider (free, instant, shallow)",
    "mock": "DERVA's mock decision server (harness checks only, not a model)",
    "jev": "TypeSafe Jev via LiteLLM (DERVA_JEV_MODEL, default typesafe/jev-latest)",
    "clef": "Cloudflare Clef-flash via LiteLLM (DERVA_CLEF_MODEL, default cloudflare/clef-flash)",
    "llm": "a small LLM forced into typed answers (DERVA_EVAL_LLM)",
}


def build_decider(spec: str, cache: Path | None = None, rpm: float | None = None) -> Decider:
    """Build a decider from a preset name or from ``decision:<model>`` or ``llm:<model>``.

    ``rpm`` spaces calls for rate-limited providers; the cache sits outside it,
    so cached answers come back at once.
    """
    decider: Decider
    if spec == "rules":
        decider = RulesDecider()
    elif spec == "mock":
        decider = LiteLLMDecisionDecider(
            model="typesafe/mock-decider-0",
            api_base=os.environ.get("DERVA_MOCK_URL") or "http://localhost:8100",
            api_key="mock",
            name="mock",
        )
    elif spec == "jev":
        decider = LiteLLMDecisionDecider(
            model=os.environ.get("DERVA_JEV_MODEL") or "typesafe/jev-latest", name="jev"
        )
    elif spec == "clef":
        decider = LiteLLMDecisionDecider(
            model=os.environ.get("DERVA_CLEF_MODEL") or "cloudflare/clef-flash", name="clef"
        )
    elif spec == "llm":
        model = os.environ.get("DERVA_EVAL_LLM")
        if not model:
            raise ValueError("Set DERVA_EVAL_LLM to the LiteLLM model string for the small LLM.")
        decider = LLMDecider(model=model, name="llm")
    elif spec.startswith("decision:"):
        decider = LiteLLMDecisionDecider(model=spec.removeprefix("decision:"))
    elif spec.startswith("llm:"):
        decider = LLMDecider(model=spec.removeprefix("llm:"))
    else:
        raise ValueError(f"Unknown decider {spec!r}. Presets: {', '.join(PRESETS)}")
    if rpm:
        decider = RateLimitedDecider(decider, rpm)
    return CachingDecider(decider, cache) if cache else decider


def _innermost(decider: Decider) -> Decider:
    while hasattr(decider, "inner"):
        decider = decider.inner
    return decider


class RunSummary(BaseModel):
    run_id: str
    task: str
    split: str
    full_dataset: bool
    decider: str
    model: str | None
    synthetic_decider: bool
    questions_version: str
    git_commit: str | None
    started_at: str
    n: int
    errors: int
    accuracy: float
    macro_f1: float
    ece: float | None
    brier: float | None
    coverage_at_95: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    cost_per_1k_usd: float | None
    extra: dict[str, Any] = {}
    reliability: list[m.ReliabilityBin] = []
    automation: list[m.AutomationPoint] = []
    per_category: dict[str, dict[str, float]] = {}


def _slug(spec: str) -> str:
    return spec.replace(":", "_").replace("/", "_")


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _scored(example: Example, answer: Answer) -> dict[str, Any]:
    probs = answer.probabilities
    return {
        "predicted": answer.top,
        "p_top": answer.top_probability,
        "p_label": probs.get(example.label, 0.0),
        "sum_sq": sum(p * p for p in probs.values()),
        "confidence": answer.confidence,
        "provider_confidence": answer.provider_confidence,
    }


async def _score_question(
    decider: Decider, example: Example, questions: QuestionSet, qid: str
) -> m.Record:
    base = {
        "id": example.id,
        "split": split_of(example.id),
        "category": example.category,
        "label": example.label,
    }
    start = time.perf_counter()
    try:
        result = await decider.decide(example.text, questions)
    except Exception as exc:
        return m.Record.model_validate({**base, "predicted": None, "error": repr(exc)[:300]})
    return m.Record.model_validate(
        {
            **base,
            **_scored(example, result.answers[qid]),
            "latency_ms": result.latency_ms or (time.perf_counter() - start) * 1000,
            "cost_usd": result.cost_usd,
            "fallbacks": result.fallbacks,
        }
    )


async def _score_golden(pipeline: Pipeline, example: Example) -> m.Record:
    base = {
        "id": example.id,
        "split": split_of(example.id),
        "category": example.category,
        "label": example.label,
    }
    rate_wait_s.set(0.0)  # each example runs in its own task, so this is per example
    start = time.perf_counter()
    try:
        result = await pipeline.check_request(
            GatewayRequest(
                caller=FIXTURE_CALLER,
                messages=example.messages or [{"role": "user", "content": example.text}],
            )
        )
    except Exception as exc:
        return m.Record.model_validate({**base, "predicted": None, "error": repr(exc)[:300]})
    decision = result.decision
    return m.Record.model_validate(
        {
            **base,
            "predicted": result.outcome.value,
            # Time spent queuing for a rate-limited provider is not latency.
            "latency_ms": (time.perf_counter() - start - rate_wait_s.get()) * 1000,
            "cost_usd": decision.cost_usd if decision else None,
            "fallbacks": decision.fallbacks if decision else [],
        }
    )


def golden_pipeline(decider: Decider, masker: Masker | None = None) -> Pipeline:
    thresholds = load_thresholds()
    return Pipeline(
        directory=InMemoryDirectory.from_json(ROOT / "data" / "fixtures" / "crm_seed.json"),
        masker=masker or RegexMasker(thresholds.firm.domains),
        decider=decider,
        questions=load_questions(),
        thresholds=thresholds,
        audit=AuditStore("sqlite://"),
    )


def _injection_extra(records: list[m.Record]) -> dict[str, Any]:
    t = load_thresholds().injection
    scored = [r for r in records if r.p_top is not None and r.predicted is not None]

    def p_yes(r: m.Record) -> float:
        assert r.p_top is not None
        return r.p_top if r.predicted == "yes" else 1 - r.p_top

    attacks = [r for r in scored if r.label == "yes"]
    benign = [r for r in scored if r.label == "no"]

    def share(rs: list[m.Record], cut: float) -> float | None:
        return sum(p_yes(r) >= cut for r in rs) / len(rs) if rs else None

    return {
        "policy_thresholds": {"review_at": t.review_at, "block_at": t.block_at},
        "flag_recall": share(attacks, t.review_at),  # held or blocked
        "block_recall": share(attacks, t.block_at),
        "false_flag_rate": share(benign, t.review_at),
        "false_block_rate": share(benign, t.block_at),
    }


async def run(
    task: Task,
    decider_spec: str,
    *,
    split: Split = "heldout",
    full: bool = False,
    limit: int | None = None,
    concurrency: int = 8,
    cache: Path | None = None,
    rpm: float | None = None,
    results_dir: Path = RESULTS,
    write: bool = True,
) -> tuple[RunSummary, list[m.Record]]:
    examples = select(load(task, full=full), split, limit)
    decider = build_decider(decider_spec, cache, rpm)
    started = datetime.now(UTC)
    gate = asyncio.Semaphore(concurrency)

    if task == "golden":
        pipeline = golden_pipeline(decider)
        questions_version = pipeline.questions.version

        async def score(e: Example) -> m.Record:
            async with gate:
                return await _score_golden(pipeline, e)
    else:
        questions = (
            banking77_questions() if task == "banking77" else load_questions().subset(["injection"])
        )
        qid = "intent" if task == "banking77" else "injection"
        questions_version = questions.version

        async def score(e: Example) -> m.Record:
            async with gate:
                return await _score_question(decider, e, questions, qid)

    records = list(await asyncio.gather(*(score(e) for e in examples)))
    ok = [r for r in records if r.error is None]
    curve = m.automation_curve(ok)
    latencies = [r.latency_ms for r in ok if r.latency_ms is not None]
    costs = [r.cost_usd for r in ok if r.cost_usd is not None]

    extra: dict[str, Any] = {}
    if task == "injection":
        extra = _injection_extra(ok)
    elif task == "golden":
        extra = {
            "unsafe_pass_rate": m.unsafe_pass_rate(ok),
            "over_hold_rate": m.over_hold_rate(ok),
            "confusion": m.confusion(records),
        }
    extra["fallback_rate"] = sum(bool(r.fallbacks) for r in ok) / len(ok) if ok else 0.0

    model = getattr(_innermost(decider), "_model", None) or (
        "rules-v1" if decider_spec == "rules" else None
    )
    summary = RunSummary(
        run_id=f"{started:%Y%m%d-%H%M%S}-{task}-{_slug(decider_spec)}-{uuid.uuid4().hex[:6]}",
        task=task,
        split=split,
        full_dataset=full,
        decider=decider_spec,
        model=model,
        synthetic_decider=decider_spec == "mock" or "mock" in (model or ""),
        questions_version=questions_version,
        git_commit=_git_commit(),
        started_at=started.isoformat(),
        n=len(records),
        errors=len(records) - len(ok),
        accuracy=m.accuracy(records),
        macro_f1=m.macro_f1(ok),
        ece=m.expected_calibration_error(ok),
        brier=m.brier(ok),
        # Golden scores outcomes, which carry no probability: no curve to read.
        coverage_at_95=m.coverage_at_accuracy(curve, 0.95)
        if any(r.p_top is not None for r in ok)
        else None,
        latency_p50_ms=m.percentile(latencies, 50),
        latency_p95_ms=m.percentile(latencies, 95),
        cost_per_1k_usd=1000 * sum(costs) / len(costs) if costs else None,
        extra=extra,
        reliability=m.reliability(ok),
        automation=curve,
        per_category=m.per_category(records),
    )
    if write:
        out = results_dir / "runs" / summary.run_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(summary.model_dump_json(indent=2))
        (out / "records.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in records))
        brief = summary.model_dump(exclude={"reliability", "automation", "per_category"})
        brief["extra"] = {k: v for k, v in brief["extra"].items() if k != "confusion"}
        with (results_dir / "log.jsonl").open("a") as log:
            log.write(json.dumps(brief) + "\n")
    return summary, records


def load_summary(path: Path) -> RunSummary:
    path = path / "summary.json" if path.is_dir() else path
    return RunSummary.model_validate_json(path.read_text())
