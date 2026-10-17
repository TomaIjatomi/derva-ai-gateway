"""Caller authentication for the proxy, without LiteLLM's key database.

Each caller key maps to an owner and the teams whose clients they may act
for; client resolution uses those teams. Only SHA-256 hashes of keys are
stored, in a JSON list:

    [{"key_sha256": "...", "key_alias": "wealth-agent", "owner": "toma@example.ie",
      "teams": ["wealth-a"]}]

Load it from ``DERVA_CALLERS`` (the JSON itself, for example a Secret Manager
secret mounted as an env var) or ``DERVA_CALLERS_FILE`` (a path). Mint keys with
``derva keys new``.

Register in the LiteLLM config:

    general_settings:
      custom_auth: derva.gateway.auth.user_api_key_auth
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class CallerKey(BaseModel):
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    key_alias: str
    owner: str
    teams: list[str] = Field(default_factory=list)


class AuthError(Exception):
    pass


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> str:
    return "sk-derva-" + secrets.token_urlsafe(32)


def parse_callers(raw: str) -> list[CallerKey]:
    return [CallerKey.model_validate(item) for item in json.loads(raw)]


@lru_cache(maxsize=1)
def load_callers() -> tuple[CallerKey, ...]:
    if os.environ.get("DERVA_CALLERS"):
        return tuple(parse_callers(os.environ["DERVA_CALLERS"]))
    if os.environ.get("DERVA_CALLERS_FILE"):
        return tuple(parse_callers(Path(os.environ["DERVA_CALLERS_FILE"]).read_text()))
    # No callers configured: nobody gets in.
    return ()


def find_caller(presented: str, callers: tuple[CallerKey, ...] | list[CallerKey]) -> CallerKey:
    key = presented.removeprefix("Bearer ").strip()
    digest = key_hash(key)
    match = None
    # Compare against every entry, so timing does not reveal which one matched.
    for caller in callers:
        if hmac.compare_digest(caller.key_sha256, digest):
            match = caller
    if not key or match is None:
        raise AuthError("Invalid API key.")
    return match


async def user_api_key_auth(request: Any, api_key: str) -> Any:
    """LiteLLM custom auth hook: return the caller's identity, or raise."""
    from litellm.proxy._types import ProxyErrorTypes, ProxyException, UserAPIKeyAuth

    try:
        caller = find_caller(api_key, load_callers())
    except AuthError as exc:
        # LiteLLM turns its own exception type into a 401; anything else is a 500.
        raise ProxyException(
            message=str(exc), type=ProxyErrorTypes.auth_error, param="api_key", code=401
        ) from exc
    return UserAPIKeyAuth(
        api_key=caller.key_sha256,
        key_alias=caller.key_alias,
        user_id=caller.owner,
        metadata={"owner": caller.owner, "teams": caller.teams},
    )
