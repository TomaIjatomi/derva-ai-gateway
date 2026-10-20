"""Client for the System One decision API (``POST /v1/systemone``).

The same wire format reaches several deciders, so swapping one for another is a
config change:

* TypeSafe's Jev directly: ``base_url=https://api.typesafe.ai``, model ``jev-latest``
* any decision model through LiteLLM's ``/v1/systemone`` route, including
  Cloudflare's Clef-flash
* DERVA's own mock decision server, for tests and offline demos

Pin a concrete model version (for example ``jev-1.13.0``) for evaluation runs:
``jev-latest`` resolves to whatever release is current, so two runs a week apart
may not test the same model.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from derva.deciders.base import DeciderError, make_answer
from derva.types import Answer, DecisionResult, Question, QuestionSet, QuestionType, Usage


class SystemOneDecider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        name: str | None = None,
        timeout_s: float = 5.0,
        price_in_per_mtok: float = 0.0,
        price_out_per_mtok: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name or f"systemone:{model}"
        self._url = base_url.rstrip("/") + "/v1/systemone"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout_s
        self._price_in = price_in_per_mtok
        self._price_out = price_out_per_mtok
        self._client = client

    async def decide(self, state: str, questions: QuestionSet) -> DecisionResult:
        body = {"state": state, "model": self._model, "questions": questions.to_wire()}
        start = time.perf_counter()
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._url, json=body, headers=self._headers, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self._url, json=body, headers=self._headers, timeout=self._timeout
                    )
        except httpx.HTTPError as exc:
            raise DeciderError(f"{self.name}: request failed: {exc!r}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        if response.status_code != 200:
            raise DeciderError(f"{self.name}: HTTP {response.status_code}: {response.text[:200]}")
        try:
            payload = response.json()
            answers = {q.id: self._parse(q, payload["answers"][q.id]) for q in questions.questions}
        except (KeyError, TypeError, ValueError) as exc:
            raise DeciderError(f"{self.name}: malformed response: {exc!r}") from exc

        usage = Usage.model_validate(payload.get("usage") or {})
        return DecisionResult(
            decider=self.name,
            model=payload.get("model", self._model),
            answers=answers,
            latency_ms=latency_ms,
            usage=usage,
            cost_usd=(usage.input_tokens * self._price_in + usage.output_tokens * self._price_out)
            / 1e6,
        )

    @staticmethod
    def _parse(question: Question, raw: dict[str, Any]) -> Answer:
        return parse_answer(question, raw)


def parse_answer(question: Question, raw: dict[str, Any]) -> Answer:
    """Parse one System One answer into the normalised ``Answer``."""
    if raw.get("type") != question.type.value:
        raise DeciderError(
            f"{question.id}: expected a {question.type.value} answer, got {raw.get('type')!r}"
        )
    if question.type is QuestionType.NOUL:
        p = float(raw["noul"])
        return make_answer(question, {"yes": p, "no": 1 - p})
    probabilities = {str(k): float(v) for k, v in raw["probabilities"].items()}
    answer = make_answer(question, probabilities)
    # Record the provider's own confidence figure, but leave ``confidence`` as
    # DERVA computed it: policy thresholds must not depend on the provider.
    if raw.get("confidence") is not None:
        answer.provider_confidence = float(raw["confidence"])
    if question.type is QuestionType.SCORE and raw.get("score") is not None:
        answer.score = float(raw["score"])
    return answer


class LiteLLMDecisionDecider:
    """Any decision model LiteLLM supports, through ``litellm.asystemone``.

    The model string selects the provider, so moving from Jev to Clef-flash is a
    config change: ``typesafe/jev-1.13.0``, ``cloudflare/clef-flash``,
    ``openrouter/typesafe/jev-1.13`` and so on. ``api_base`` can point at any
    System One-compatible server, including DERVA's mock.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        name: str | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.name = name or f"decision:{model}"
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout_s

    async def decide(self, state: str, questions: QuestionSet) -> DecisionResult:
        import litellm

        start = time.perf_counter()
        try:
            response = await litellm.asystemone(
                model=self._model,
                state=state,
                questions=questions.to_wire(),
                api_key=self._api_key,
                api_base=self._api_base,
                timeout=self._timeout,
            )
        except Exception as exc:
            raise DeciderError(f"{self.name}: request failed: {exc!r}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        payload = response.model_dump(mode="json")
        try:
            answers = {q.id: parse_answer(q, payload["answers"][q.id]) for q in questions.questions}
        except (KeyError, TypeError, ValueError) as exc:
            raise DeciderError(f"{self.name}: malformed response: {exc!r}") from exc
        cost = response.hidden_params.get("response_cost")
        return DecisionResult(
            decider=self.name,
            model=payload.get("model") or self._model,
            answers=answers,
            latency_ms=latency_ms,
            usage=Usage.model_validate(payload.get("usage") or {}),
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )
