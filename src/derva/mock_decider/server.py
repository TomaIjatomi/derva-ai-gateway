"""A mock decision server speaking the System One API.

It exists so that tests, CI and the local demo run offline, with no keys and no
spend. Its answers come from simple heuristics and are NOT a decision model:
never report mock results as findings.

Record and replay: set ``DERVA_MOCK_RECORDINGS`` to a JSON file mapping request
hashes to real provider responses, and the server returns the recorded response
for any matching request. That lets CI replay real Jev or Clef answers without
calling the provider.

The same server also offers a demo chat model at ``/v1/chat/completions``, so
the offline demo has something to route to. It returns canned text and, when
the request offers tools, proposes the obvious one (``send_email`` for "email
...", ``update_record`` for "update ..."). That is enough to exercise the
gateway's tool-call checkpoint; it is not a language model.

Run it with ``derva mock-server`` (port 8100 by default).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from derva.deciders.base import make_answer
from derva.deciders.rules import RulesDecider
from derva.types import Answer, Question, QuestionSet, QuestionType

MOCK_MODEL = "mock-decider-0"
_WORD = re.compile(r"[a-z0-9]+")


class SystemOneRequest(BaseModel):
    state: Any
    model: str
    questions: dict[str, dict[str, Any]]


def request_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _to_question_set(questions: dict[str, dict[str, Any]]) -> QuestionSet:
    parsed = []
    for qid, spec in questions.items():
        instructions = spec.get("instructions")
        parsed.append(
            Question(
                id=qid,
                type=QuestionType(spec["type"]),
                instructions=instructions
                if isinstance(instructions, str)
                else json.dumps(instructions),
                criteria=spec.get("criteria"),
            )
        )
    return QuestionSet(version="wire", questions=parsed)


def _overlap_answer(question: Question, state: str) -> dict[str, float]:
    """Word overlap between the state and each option: a crude, deterministic guess."""
    words = set(_WORD.findall(state.lower()))
    assert isinstance(question.criteria, dict | list)
    if isinstance(question.criteria, dict):
        descriptions = {o: f"{o} {d or ''}" for o, d in question.criteria.items()}
    else:
        descriptions = {str(i): d for i, d in enumerate(question.criteria)}
    scores = {
        o: len(words & set(_WORD.findall(text.lower().replace("_", " "))))
        for o, text in descriptions.items()
    }
    exp = {o: math.exp(1.5 * s) for o, s in scores.items()}
    total = sum(exp.values())
    return {o: v / total for o, v in exp.items()}


def _to_wire(question: Question, answer: Answer) -> dict[str, Any]:
    if question.type is QuestionType.NOUL:
        return {"type": "noul", "noul": round(answer.p_yes, 6)}
    body: dict[str, Any] = {
        "type": question.type.value,
        "probabilities": {k: round(v, 6) for k, v in answer.probabilities.items()},
        "confidence": round(answer.confidence or 0.0, 6),
    }
    if question.type is QuestionType.CHOICE:
        body["choice"] = answer.top
    else:
        assert isinstance(question.criteria, list)
        body["score"] = round(answer.score or 0.0, 6)
        body["legend"] = {str(i): d for i, d in enumerate(question.criteria)}
    return body


_TOOL_TRIGGERS = [
    ("send_email", re.compile(r"\b(email|send|forward|reply)\b", re.IGNORECASE)),
    ("update_record", re.compile(r"\b(update|change|amend|close)\b", re.IGNORECASE)),
    ("lookup_client", re.compile(r"\b(look ?up|show|find|fees|balance)\b", re.IGNORECASE)),
    ("read_inbox", re.compile(r"\b(inbox|unread|emails)\b", re.IGNORECASE)),
]
_CLIENT_REF = re.compile(r"client:C-\d+")


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else json.dumps(content)
    return ""


def demo_completion(body: dict[str, Any]) -> dict[str, Any]:
    """Build the demo model's reply: a tool call when one fits, else canned text."""
    messages = body.get("messages") or []
    text = _last_user_text(messages)
    offered = {
        t["function"]["name"] for t in body.get("tools") or [] if t.get("type") == "function"
    }
    # After a tool has run, answer in text rather than calling tools forever.
    tool_already_ran = bool(messages) and messages[-1].get("role") == "tool"
    message: dict[str, Any] = {"role": "assistant", "content": None}
    finish = "stop"
    for tool, trigger in _TOOL_TRIGGERS:
        if tool in offered and trigger.search(text) and not tool_already_ran:
            ref = _CLIENT_REF.search(text)
            args: dict[str, Any] = {"client": ref.group(0) if ref else None}
            if tool == "send_email":
                args |= {
                    "subject": "Following up",
                    "body": "Demo message drafted by the demo model.",
                }
            message["tool_calls"] = [
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(args)},
                }
            ]
            finish = "tool_calls"
            break
    else:
        message["content"] = f"[demo reply from {body.get('model', 'demo-model')}]"
    return {
        "id": f"chatcmpl-demo-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "demo-model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": max(1, len(text) // 4),
            "completion_tokens": 8,
            "total_tokens": max(1, len(text) // 4) + 8,
        },
    }


def _stream(completion: dict[str, Any]) -> StreamingResponse:
    choice = completion["choices"][0]
    base = {k: completion[k] for k in ("id", "created", "model")} | {
        "object": "chat.completion.chunk"
    }
    delta: dict[str, Any] = {"role": "assistant", "content": choice["message"]["content"]}
    if choice["message"].get("tool_calls"):
        delta["tool_calls"] = [
            dict(c, index=i) for i, c in enumerate(choice["message"]["tool_calls"])
        ]

    def events() -> Any:
        first = {"index": 0, "delta": delta, "finish_reason": None}
        last = {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
        yield f"data: {json.dumps(base | {'choices': [first]})}\n\n"
        yield f"data: {json.dumps(base | {'choices': [last]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def create_app(recordings: Path | None = None) -> FastAPI:
    app = FastAPI(title="DERVA mock decision server")
    rules = RulesDecider()
    path = recordings or (
        Path(os.environ["DERVA_MOCK_RECORDINGS"])
        if os.environ.get("DERVA_MOCK_RECORDINGS")
        else None
    )
    recorded: dict[str, Any] = json.loads(path.read_text()) if path and path.exists() else {}
    known = {
        "injection",
        "exfiltration",
        "action",
        "route",
        "regulated_advice",
        "customer_facing",
        "off_topic",
    }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": MOCK_MODEL}

    @app.post("/v1/systemone")
    async def systemone(request: SystemOneRequest) -> dict[str, Any]:
        body = request.model_dump()
        key = request_hash(body)
        if key in recorded:
            return dict(recorded[key])
        try:
            questions = _to_question_set(request.questions)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        state = request.state if isinstance(request.state, str) else json.dumps(request.state)

        answers: dict[str, Any] = {}
        for q in questions.questions:
            if q.id in known:
                result = await rules.decide(state, questions.subset([q.id]))
                answer = result.answers[q.id]
            elif q.type is QuestionType.NOUL:
                answer = make_answer(q, {"yes": 0.5, "no": 0.5})
            else:
                answer = make_answer(q, _overlap_answer(q, state))
            answers[q.id] = _to_wire(q, answer)
        return {
            "model": MOCK_MODEL,
            "answers": answers,
            "usage": {"input_tokens": max(1, len(state) // 4), "output_tokens": 0},
        }

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(body: dict[str, Any]) -> dict[str, Any] | StreamingResponse:
        completion = demo_completion(body)
        return _stream(completion) if body.get("stream") else completion

    return app
