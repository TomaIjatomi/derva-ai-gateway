"""LiteLLM proxy hooks: the DERVA pipeline in front of every chat completion.

Callers send requests to one model alias (``derva``). The pre-call hook runs the
pipeline on the incoming content and then either:

* rewrites the model to the chosen tier (``derva-small`` or ``derva-frontier``)
  and swaps client names for their references, or
* raises ``GatewayReply``, which the proxy sends back as a normal chat
  completion (HTTP 200, streamed or not). That is how blocked, held and clarify
  outcomes reach the caller: an ordinary OpenAI-shaped reply carrying the
  request id, never an HTTP error, so standard SDK clients and scanners such as
  garak see a refusal rather than a crash.

  Returning a string from the hook is not enough: LiteLLM turns a string into a
  completion only for the legacy ``completion`` call type and into an HTTP 400
  for ``acompletion``, which is what chat requests use.

The post-call hook inspects tool calls the model proposes. An effectful call is
removed from the response and replaced with a held-for-review message, so the
agent never receives it. This is the v0.1 control; v0.2 adds the stronger one,
where tool servers accept only calls the gateway has signed and approved.

Register in the LiteLLM config:

    litellm_settings:
      callbacks: derva.gateway.litellm_hook.proxy_handler_instance
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException
from litellm.exceptions import RejectedRequestError
from litellm.integrations.custom_logger import CustomLogger

from derva.pipeline import GatewayRequest, Pipeline
from derva.policy import Outcome
from derva.resolution import Caller
from derva.settings import Settings, build_pipeline

_CHAT_CALL_TYPES = {"completion", "acompletion"}
_HOLD = {Outcome.REVIEW, Outcome.BLOCK}


class GatewayReply(RejectedRequestError):
    """A gateway decision delivered to the caller as an ordinary assistant reply."""

    def __init__(self, reply: str, data: dict[str, Any]) -> None:
        super().__init__(
            message=reply, model=str(data.get("model", "")), llm_provider="", request_data=data
        )
        # The proxy copies ``message`` into the reply and uses ``status_code`` for
        # streamed replies; set both after the base class has added its prefixes.
        self.message = reply
        self.status_code = 200  # type: ignore[assignment]
        self.logging_obj = data.get("litellm_logging_obj")


def caller_from_key(user_api_key_dict: Any) -> Caller:
    """The caller, from the identity derva.gateway.auth attached to the key."""
    metadata = getattr(user_api_key_dict, "metadata", None) or {}
    key_id = (
        getattr(user_api_key_dict, "key_alias", None)
        or (getattr(user_api_key_dict, "token", None) or "")[:12]
        or "unknown-key"
    )
    owner = metadata.get("owner") or getattr(user_api_key_dict, "user_id", None) or key_id
    # No teams means no clients: a key without them can act for nobody.
    teams = metadata.get("teams") or []
    return Caller(key_id=str(key_id), owner=str(owner), teams=list(teams))


class DervaGuard(CustomLogger):
    def __init__(self, pipeline: Pipeline | None = None, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings or Settings.from_env()
        self._pipeline = pipeline

    @property
    def pipeline(self) -> Pipeline:
        # Built lazily, so importing the module (as LiteLLM does) stays cheap.
        if self._pipeline is None:
            self._pipeline = build_pipeline(self.settings)
        return self._pipeline

    def _tier_model(self, outcome: Outcome) -> str:
        if outcome is Outcome.SMALL_MODEL:
            return self.settings.small_model
        return self.settings.frontier_model

    async def async_pre_call_hook(
        self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: str
    ) -> dict[str, Any]:
        if call_type not in _CHAT_CALL_TYPES or "messages" not in data:
            # Fail closed: only chat completions go through the checks, so
            # nothing else (embeddings, responses, passthrough) is served.
            raise HTTPException(
                status_code=403,
                detail={"error": f"This gateway serves chat completions only, not {call_type}."},
            )
        if data.get("model") != self.settings.entry_model:
            # The tier is the gateway's choice, never the caller's.
            raise GatewayReply(
                f"This gateway serves one model, '{self.settings.entry_model}'. "
                "Send requests to that model and the gateway picks the tier.",
                data,
            )
        if data.get("stream") and data.get("tools"):
            raise GatewayReply(
                "This gateway does not yet check tool calls in streamed responses. "
                "Send the request with stream set to false.",
                data,
            )

        caller = caller_from_key(user_api_key_dict)
        request_id = str(data.get("litellm_call_id") or uuid.uuid4().hex)
        result = await self.pipeline.check_request(
            GatewayRequest(caller=caller, messages=data["messages"], request_id=request_id)
        )
        if result.reply is not None:
            raise GatewayReply(result.reply, data)

        data["messages"] = self.pipeline.forwardable_messages(data["messages"], caller)
        metadata = data.setdefault("metadata", {})
        metadata["derva_request_id"] = result.request_id
        metadata["derva_outcome"] = result.outcome.value
        data["model"] = self._tier_model(result.outcome)
        return data

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> None:
        # Workaround for LiteLLM 1.105: the proxy's failure handling removes the
        # logging object from the request before its streaming branch reads it,
        # so a streamed gateway reply crashes with a 500. Put it back, for our
        # own replies only.
        if (
            isinstance(original_exception, GatewayReply)
            and request_data.get("stream")
            and original_exception.logging_obj is not None
            and "litellm_logging_obj" not in request_data
        ):
            request_data["litellm_logging_obj"] = original_exception.logging_obj
        return None

    async def async_post_call_success_hook(
        self, data: dict[str, Any], user_api_key_dict: Any, response: Any
    ) -> Any:
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        caller = caller_from_key(user_api_key_dict)
        request_id = (data.get("metadata") or {}).get("derva_request_id") or str(
            data.get("litellm_call_id") or uuid.uuid4().hex
        )

        changed = False
        for choice in choices:
            message = getattr(choice, "message", None)
            tool_calls = getattr(message, "tool_calls", None) or []
            held: list[str] = []
            for call in tool_calls:
                name = call.function.name
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {"_unparseable": call.function.arguments}
                check = await self.pipeline.check_tool_call(request_id, caller, name, arguments)
                if check.outcome in _HOLD:
                    held.append(f"{name} (approval id {check.arguments_hash[:16]})")
            if held and message is not None:
                # Hold the whole message: never pass on part of a set of calls.
                message.tool_calls = None
                message.content = (
                    "The proposed action is held for review by a person before it runs: "
                    + ", ".join(held)
                    + f". Request id: {request_id}"
                )
                choice.finish_reason = "stop"
                changed = True
        return response if changed else None


proxy_handler_instance = DervaGuard()
