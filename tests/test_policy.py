from __future__ import annotations

import pytest

from derva.config import Thresholds
from derva.deciders.base import make_answer
from derva.policy import Outcome, evaluate_request, evaluate_tool_call
from derva.types import DecisionResult, QuestionSet


def result(questions: QuestionSet, **probs: dict[str, float]) -> DecisionResult:
    """A decision with sensible defaults, overridden per question."""
    defaults: dict[str, dict[str, float]] = {
        "injection": {"yes": 0.05},
        "exfiltration": {"yes": 0.05},
        "action": {"answer_only": 0.9},
        "route": {"small_model": 0.9},
        "regulated_advice": {"yes": 0.05},
        "customer_facing": {"yes": 0.05},
    }
    defaults.update(probs)
    answers = {}
    for q in questions.questions:
        p = defaults[q.id]
        if "yes" in p and "no" not in p:
            p = {"yes": p["yes"], "no": 1 - p["yes"]}
        if q.id in ("action", "route"):
            rest = (1 - sum(p.values())) / (len(q.options) - len(p))
            p = {o: p.get(o, rest) for o in q.options}
        answers[q.id] = make_answer(q, p)
    return DecisionResult(decider="test", answers=answers, latency_ms=1.0)


def test_clean_request_routes_to_small_model(
    questions: QuestionSet, thresholds: Thresholds
) -> None:
    decision = evaluate_request(result(questions), thresholds)
    assert decision.outcome is Outcome.SMALL_MODEL
    assert decision.thresholds_version == thresholds.version


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (0.95, Outcome.BLOCK),
        (0.8, Outcome.BLOCK),
        (0.6, Outcome.REVIEW),
        (0.39, Outcome.SMALL_MODEL),
    ],
)
def test_injection_bands(
    questions: QuestionSet, thresholds: Thresholds, p: float, expected: Outcome
) -> None:
    decision = evaluate_request(result(questions, injection={"yes": p}), thresholds)
    assert decision.outcome is expected


def test_block_beats_review(questions: QuestionSet, thresholds: Thresholds) -> None:
    decision = evaluate_request(
        result(questions, injection={"yes": 0.9}, exfiltration={"yes": 0.5}), thresholds
    )
    assert decision.outcome is Outcome.BLOCK
    assert {f.rule for f in decision.findings} >= {"injection_block", "exfiltration_review"}


def test_unclear_action_goes_to_review(questions: QuestionSet, thresholds: Thresholds) -> None:
    decision = evaluate_request(result(questions, action={"answer_only": 0.35}), thresholds)
    assert decision.outcome is Outcome.REVIEW
    assert decision.rule == "action_unclear"


def test_effectful_request_is_not_held_until_the_tool_call(
    questions: QuestionSet, thresholds: Thresholds
) -> None:
    # Approval binds to a concrete call, which only exists once the model proposes it.
    decision = evaluate_request(result(questions, action={"send_external": 0.9}), thresholds)
    assert decision.outcome is Outcome.SMALL_MODEL


def test_regulated_advice_going_outside_needs_review(
    questions: QuestionSet, thresholds: Thresholds
) -> None:
    decision = evaluate_request(
        result(questions, regulated_advice={"yes": 0.7}, customer_facing={"yes": 0.8}), thresholds
    )
    assert decision.outcome is Outcome.REVIEW
    assert decision.rule == "regulated_advice_external"


def test_unsure_router_falls_back_to_frontier(
    questions: QuestionSet, thresholds: Thresholds
) -> None:
    decision = evaluate_request(result(questions, route={"small_model": 0.7}), thresholds)
    assert decision.outcome is Outcome.FRONTIER_MODEL
    assert decision.route == "frontier_model"


def test_no_decider_fails_closed(thresholds: Thresholds) -> None:
    decision = evaluate_request(None, thresholds)
    assert decision.outcome is Outcome.REVIEW
    assert decision.rule == "no_decider_available"


def test_missing_answer_fails_closed(questions: QuestionSet, thresholds: Thresholds) -> None:
    full = result(questions)
    del full.answers["injection"]
    assert evaluate_request(full, thresholds).outcome is Outcome.REVIEW


def test_effectful_tool_always_needs_review(questions: QuestionSet, thresholds: Thresholds) -> None:
    # Even a perfectly clean decision cannot let an effectful tool run unapproved.
    decision = evaluate_tool_call("send_email", result(questions), thresholds)
    assert decision.outcome is Outcome.REVIEW
    assert decision.rule == "effectful_tool"


def test_read_only_tool_is_allowed(questions: QuestionSet, thresholds: Thresholds) -> None:
    assert (
        evaluate_tool_call("lookup_client", result(questions), thresholds).outcome is Outcome.ALLOW
    )


def test_unknown_tool_fails_closed(questions: QuestionSet, thresholds: Thresholds) -> None:
    assert evaluate_tool_call("wire_money", result(questions), thresholds).outcome is Outcome.REVIEW


def test_decider_can_only_tighten_tool_policy(
    questions: QuestionSet, thresholds: Thresholds
) -> None:
    decision = evaluate_tool_call(
        "lookup_client", result(questions, exfiltration={"yes": 0.95}), thresholds
    )
    assert decision.outcome is Outcome.BLOCK
