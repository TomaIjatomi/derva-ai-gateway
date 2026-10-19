"""The policy engine: plain code that turns typed answers into one outcome.

Two rules shape everything here:

* The decider only answers questions. Every threshold, and every judgement about
  what an answer means, lives in this file and in ``config/thresholds.yaml``.
* When in doubt, fail closed. A missing answer or an unavailable decider sends the
  request to a person, never straight through.

Approval for effectful actions is decided on the tool call, not on the incoming
request: a person can only approve something concrete, such as one email to one
recipient, and that only exists once the model has proposed the call.

The review queue is for legitimate work the gateway is unsure about. Requests
that are clearly not the adviser's work are declined with a polite reply
instead, so they cost no reviewer's time; safety findings still outrank that.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from derva.config import RouteTier, Thresholds
from derva.types import DecisionResult, QuestionType


class Outcome(StrEnum):
    BLOCK = "block"
    REVIEW = "review"
    DECLINE = "decline"  # off-topic: refused politely, not sent to a model
    CLARIFY = "clarify"
    ALLOW = "allow"  # tool calls only
    SMALL_MODEL = "small_model"
    FRONTIER_MODEL = "frontier_model"


# Higher number wins when several rules fire.
_SEVERITY = {
    Outcome.BLOCK: 5,
    Outcome.REVIEW: 4,
    Outcome.DECLINE: 3,
    Outcome.CLARIFY: 2,
    Outcome.FRONTIER_MODEL: 1,
    Outcome.SMALL_MODEL: 1,
    Outcome.ALLOW: 1,
}

REQUEST_QUESTIONS = (
    "injection",
    "exfiltration",
    "action",
    "route",
    "regulated_advice",
    "customer_facing",
    "off_topic",
)
# Asked only when the incoming turn includes a user message.
USER_TURN_QUESTIONS = ("off_topic",)
TOOL_CALL_QUESTIONS = ("injection", "exfiltration", "regulated_advice")


class Finding(BaseModel):
    rule: str
    outcome: Outcome
    reason: str


class PolicyDecision(BaseModel):
    outcome: Outcome
    rule: str
    findings: list[Finding] = Field(default_factory=list)
    thresholds_version: str
    # Where the request goes once allowed or approved.
    route: RouteTier | None = None

    @property
    def reasons(self) -> list[str]:
        return [f.reason for f in self.findings]


def _p_yes(result: DecisionResult, question_id: str) -> float | None:
    answer = result.answers.get(question_id)
    if answer is None or answer.type is not QuestionType.NOUL:
        return None
    return answer.p_yes


def _noul_findings(result: DecisionResult, t: Thresholds, ids: tuple[str, ...]) -> list[Finding]:
    findings: list[Finding] = []
    limits = {"injection": t.injection, "exfiltration": t.exfiltration}
    for qid in ids:
        if qid not in limits:
            continue
        p = _p_yes(result, qid)
        if p is None:
            findings.append(
                Finding(rule=f"missing_{qid}", outcome=Outcome.REVIEW, reason=f"no {qid} answer")
            )
        elif p >= limits[qid].block_at:
            findings.append(
                Finding(
                    rule=f"{qid}_block",
                    outcome=Outcome.BLOCK,
                    reason=f"{qid} probability {p:.2f} >= {limits[qid].block_at}",
                )
            )
        elif p >= limits[qid].review_at:
            findings.append(
                Finding(
                    rule=f"{qid}_review",
                    outcome=Outcome.REVIEW,
                    reason=f"{qid} probability {p:.2f} in review band",
                )
            )
    return findings


def _route(result: DecisionResult, t: Thresholds) -> tuple[RouteTier, str]:
    answer = result.answers.get("route")
    if answer is None or answer.confidence is None:
        return t.route.default, "no confident route answer; using default"
    if answer.confidence >= t.route.min_confidence and answer.top in (
        "small_model",
        "frontier_model",
    ):
        tier: RouteTier = "small_model" if answer.top == "small_model" else "frontier_model"
        return tier, f"router chose {answer.top} with confidence {answer.confidence:.2f}"
    return t.route.default, f"router confidence {answer.confidence:.2f} below threshold"


def _decide(findings: list[Finding], t: Thresholds, route: RouteTier | None) -> PolicyDecision:
    worst = max(findings, key=lambda f: _SEVERITY[f.outcome])
    return PolicyDecision(
        outcome=worst.outcome,
        rule=worst.rule,
        findings=findings,
        thresholds_version=t.version,
        route=route,
    )


def evaluate_request(result: DecisionResult | None, t: Thresholds) -> PolicyDecision:
    """Decide what happens to an incoming request (messages and tool results)."""
    if result is None:
        return PolicyDecision(
            outcome=Outcome.REVIEW,
            rule="no_decider_available",
            findings=[
                Finding(
                    rule="no_decider_available",
                    outcome=Outcome.REVIEW,
                    reason="every decider failed; failing closed",
                )
            ],
            thresholds_version=t.version,
            route=t.route.default,
        )

    findings = _noul_findings(result, t, ("injection", "exfiltration"))

    # Off-topic: nothing will be sent to a model, so the action, advice and route
    # answers no longer matter. The safety findings above still apply and
    # outrank a decline. No answer (the question is not asked on turns with
    # only tool results) means no finding: this is about scope, not safety.
    off_topic = _p_yes(result, "off_topic")
    if off_topic is not None and off_topic >= t.off_topic.decline_at:
        findings.append(
            Finding(
                rule="off_topic",
                outcome=Outcome.DECLINE,
                reason=f"off-topic probability {off_topic:.2f} >= {t.off_topic.decline_at}",
            )
        )
        return _decide(findings, t, route=None)

    action = result.answers.get("action")
    if action is None or action.confidence is None:
        findings.append(
            Finding(rule="missing_action", outcome=Outcome.REVIEW, reason="no action answer")
        )
    elif action.confidence < t.action.min_confidence:
        findings.append(
            Finding(
                rule="action_unclear",
                outcome=Outcome.REVIEW,
                reason=f"action confidence {action.confidence:.2f} < {t.action.min_confidence}",
            )
        )

    advice = _p_yes(result, "regulated_advice")
    external = _p_yes(result, "customer_facing")
    if (
        advice is not None
        and external is not None
        and advice >= t.advice.regulated_advice_at
        and external >= t.advice.customer_facing_at
    ):
        findings.append(
            Finding(
                rule="regulated_advice_external",
                outcome=Outcome.REVIEW,
                reason=f"personal financial advice ({advice:.2f}) going outside ({external:.2f})",
            )
        )

    tier, why = _route(result, t)
    findings.append(Finding(rule="route", outcome=Outcome(tier), reason=why))
    return _decide(findings, t, tier)


def evaluate_tool_call(
    tool_name: str, result: DecisionResult | None, t: Thresholds
) -> PolicyDecision:
    """Decide whether a proposed tool call may run.

    The tool's name decides whether a person must approve it. The decider's
    answers can only make the outcome stricter, never more lenient.
    """
    findings: list[Finding] = []
    if tool_name in t.tools.effectful:
        findings.append(
            Finding(
                rule="effectful_tool",
                outcome=Outcome.REVIEW,
                reason=f"{tool_name} sends or changes something; a person must approve it",
            )
        )
    elif tool_name in t.tools.read_only:
        findings.append(
            Finding(
                rule="read_only_tool", outcome=Outcome.ALLOW, reason=f"{tool_name} is read-only"
            )
        )
    else:
        findings.append(
            Finding(
                rule="unknown_tool",
                outcome=Outcome.REVIEW,
                reason=f"{tool_name} is not in the tool policy; failing closed",
            )
        )

    if result is None:
        findings.append(
            Finding(
                rule="no_decider_available",
                outcome=Outcome.REVIEW,
                reason="no decider answered for the tool call; failing closed",
            )
        )
    else:
        findings.extend(_noul_findings(result, t, ("injection", "exfiltration")))

    return _decide(findings, t, route=None)
