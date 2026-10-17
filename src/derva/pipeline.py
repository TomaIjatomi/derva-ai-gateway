"""The request pipeline: resolve, mask, decide, apply policy, record.

This module knows nothing about LiteLLM or HTTP. The gateway adapter in
``derva.gateway`` calls it, and so do the tests and the eval harness, which is
why the pipeline can be tested end to end without any provider.

Two checkpoints:

* ``check_request`` runs on incoming content: the user's message and any tool
  results the agent passes back, which is where injected instructions hide.
* ``check_tool_call`` runs on every tool call the model proposes, before it
  executes. Effectful tools always need a person (see ``policy.py``).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, Field

from derva.audit import AuditStore, EventType
from derva.config import Thresholds
from derva.deciders.base import Decider, DeciderError
from derva.deciders.chain import AllDecidersFailed
from derva.masking import Masker
from derva.policy import (
    TOOL_CALL_QUESTIONS,
    Outcome,
    PolicyDecision,
    evaluate_request,
    evaluate_tool_call,
)
from derva.resolution import Caller, ClientDirectory, ResolutionStatus, resolve_clients
from derva.types import DecisionResult, QuestionSet


class GatewayRequest(BaseModel):
    caller: Caller
    messages: list[dict[str, Any]]
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class PipelineResult(BaseModel):
    request_id: str
    outcome: Outcome
    policy: PolicyDecision | None = None
    decision: DecisionResult | None = None
    masked_state: str = ""
    references: list[str] = Field(default_factory=list)
    # Completion text returned to the caller when the request does not go to a model.
    reply: str | None = None


class ToolCallCheck(BaseModel):
    request_id: str
    tool: str
    arguments_hash: str
    outcome: Outcome
    policy: PolicyDecision


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content parts
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return "" if content is None else json.dumps(content)


def incoming_state(messages: list[dict[str, Any]]) -> str:
    """Everything that arrived since the model last spoke: user turns and tool results."""
    since: list[dict[str, Any]] = []
    for message in reversed(messages):
        if message.get("role") == "assistant":
            break
        since.append(message)
    if not since and messages:
        since = [messages[-1]]
    parts = []
    for message in reversed(since):
        role = message.get("role", "user")
        if role == "system":
            continue
        label = "tool result" if role == "tool" else role
        parts.append(f"[{label}] {_text(message.get('content'))}")
    return "\n".join(parts)


def canonical_hash(data: Any) -> str:
    material = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def _answers_for_audit(decision: DecisionResult) -> dict[str, Any]:
    return {
        qid: {
            "top": a.top,
            "probabilities": {k: round(v, 4) for k, v in a.probabilities.items()},
            "confidence": None if a.confidence is None else round(a.confidence, 4),
        }
        for qid, a in decision.answers.items()
    }


class Pipeline:
    def __init__(
        self,
        *,
        directory: ClientDirectory,
        masker: Masker,
        decider: Decider,
        questions: QuestionSet,
        thresholds: Thresholds,
        audit: AuditStore,
    ) -> None:
        self.directory = directory
        self.masker = masker
        self.decider = decider
        self.questions = questions
        self.thresholds = thresholds
        self.audit = audit

    async def _decide(
        self, state: str, questions: QuestionSet
    ) -> tuple[DecisionResult | None, list[str]]:
        try:
            return await self.decider.decide(state, questions), []
        except AllDecidersFailed as exc:
            return None, exc.failures
        except DeciderError as exc:
            return None, [str(exc)]

    async def check_request(self, request: GatewayRequest) -> PipelineResult:
        rid = request.request_id
        actor = request.caller.key_id
        state = incoming_state(request.messages)
        self.audit.append(
            rid,
            EventType.RECEIVED,
            actor,
            {
                "owner": request.caller.owner,
                "messages": len(request.messages),
                "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
                "state_chars": len(state),
            },
        )

        resolution = resolve_clients(state, request.caller, self.directory)
        self.audit.append(
            rid,
            EventType.RESOLVED,
            actor,
            {
                "status": resolution.status.value,
                "references": resolution.references,
                "ambiguous_candidates": sorted(
                    {ref for refs in resolution.ambiguous.values() for ref in refs}
                ),
                "denied_count": resolution.denied_count,
            },
        )
        reply: str | None
        if resolution.status is ResolutionStatus.DENIED:
            reply = (
                "I couldn't match a client you're authorised to act for. Check the name, "
                f"or ask the client's account team. Request id: {rid}"
            )
            self.audit.append(rid, EventType.BLOCKED, actor, {"rule": "client_not_authorised"})
            return PipelineResult(request_id=rid, outcome=Outcome.BLOCK, reply=reply)
        if resolution.status is ResolutionStatus.AMBIGUOUS:
            options = "; ".join(
                f"'{name}' matches {', '.join(refs)}" for name, refs in resolution.ambiguous.items()
            )
            reply = f"More than one of your clients matches: {options}. Which one did you mean?"
            self.audit.append(rid, EventType.CLARIFY, actor, {"rule": "ambiguous_client"})
            return PipelineResult(request_id=rid, outcome=Outcome.CLARIFY, reply=reply)

        masked = self.masker.mask(resolution.text)
        decision, failures = await self._decide(masked.text, self.questions)
        policy = evaluate_request(decision, self.thresholds)
        self.audit.append(
            rid,
            EventType.DECIDED,
            "gateway",
            {
                "questions_version": self.questions.version,
                "thresholds_version": policy.thresholds_version,
                "decider": decision.decider if decision else None,
                "model": decision.model if decision else None,
                "fallbacks": (decision.fallbacks if decision else failures),
                "answers": _answers_for_audit(decision) if decision else {},
                "latency_ms": round(decision.latency_ms, 1) if decision else None,
                "cost_usd": decision.cost_usd if decision else None,
                "mask_counts": masked.counts,
                "masked_state": masked.text,
                "outcome": policy.outcome.value,
                "rule": policy.rule,
                "findings": [f.model_dump(mode="json") for f in policy.findings],
            },
        )

        reply = None
        if policy.outcome is Outcome.BLOCK:
            reply = (
                "This request was blocked by the gateway's safety checks and was not sent "
                f"to a model. Request id: {rid}"
            )
            self.audit.append(rid, EventType.BLOCKED, "gateway", {"rule": policy.rule})
        elif policy.outcome is Outcome.REVIEW:
            reply = (
                "This request is held for review by a person before it can go ahead. "
                f"Request id: {rid}"
            )
            self.audit.append(rid, EventType.HELD, "gateway", {"rule": policy.rule})
        else:
            self.audit.append(rid, EventType.ROUTED, "gateway", {"tier": policy.outcome.value})

        return PipelineResult(
            request_id=rid,
            outcome=policy.outcome,
            policy=policy,
            decision=decision,
            masked_state=masked.text,
            references=resolution.references,
            reply=reply,
        )

    async def check_tool_call(
        self, request_id: str, caller: Caller, tool: str, arguments: dict[str, Any]
    ) -> ToolCallCheck:
        args_hash = canonical_hash({"tool": tool, "arguments": arguments})
        masked = self.masker.mask(json.dumps(arguments, sort_keys=True))
        state = f"Proposed tool call: {tool}\nArguments: {masked.text}"
        questions = self.questions.subset(list(TOOL_CALL_QUESTIONS))
        decision, failures = await self._decide(state, questions)
        policy = evaluate_tool_call(tool, decision, self.thresholds)
        self.audit.append(
            request_id,
            EventType.TOOL_PROPOSED,
            caller.key_id,
            {
                "tool": tool,
                "arguments_sha256": args_hash,
                "decider": decision.decider if decision else None,
                "fallbacks": (decision.fallbacks if decision else failures),
                "answers": _answers_for_audit(decision) if decision else {},
                "outcome": policy.outcome.value,
                "rule": policy.rule,
                "thresholds_version": policy.thresholds_version,
            },
        )
        if policy.outcome is Outcome.REVIEW:
            self.audit.append(
                request_id,
                EventType.APPROVAL_REQUESTED,
                "gateway",
                {"tool": tool, "arguments_sha256": args_hash},
            )
        elif policy.outcome is Outcome.BLOCK:
            self.audit.append(request_id, EventType.BLOCKED, "gateway", {"rule": policy.rule})
        return ToolCallCheck(
            request_id=request_id,
            tool=tool,
            arguments_hash=args_hash,
            outcome=policy.outcome,
            policy=policy,
        )

    def forwardable_messages(
        self, messages: list[dict[str, Any]], caller: Caller
    ) -> list[dict[str, Any]]:
        """The messages to send on to the model once a request has passed.

        Client names the caller may act for become their references, so the
        model never sees them and the agent's tools receive references they can
        act on. A message whose names do not all resolve cleanly (for example
        an older turn naming two clients called John Kelly) is left as it was:
        the checks above cover the incoming turn, which did resolve.
        """
        forwarded = []
        for message in messages:
            content = message.get("content")
            if message.get("role") in {"user", "tool"} and isinstance(content, str):
                resolution = resolve_clients(content, caller, self.directory)
                if resolution.status is ResolutionStatus.OK and resolution.text != content:
                    message = {**message, "content": resolution.text}
            forwarded.append(message)
        return forwarded
