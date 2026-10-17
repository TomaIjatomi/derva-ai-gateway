from __future__ import annotations

import json

import pytest

from derva.gateway.auth import AuthError, CallerKey, find_caller, key_hash, new_key, parse_callers

CALLERS = [
    CallerKey(key_sha256=key_hash("sk-a"), key_alias="a", owner="a@example.ie", teams=["wealth-a"]),
    CallerKey(key_sha256=key_hash("sk-b"), key_alias="b", owner="b@example.ie", teams=["wealth-b"]),
]


def test_known_key_maps_to_its_caller() -> None:
    assert find_caller("sk-b", CALLERS).teams == ["wealth-b"]
    assert find_caller("Bearer sk-a", CALLERS).key_alias == "a"


@pytest.mark.parametrize("key", ["", "Bearer ", "sk-c", key_hash("sk-a")])
def test_unknown_keys_are_refused(key: str) -> None:
    with pytest.raises(AuthError):
        find_caller(key, CALLERS)


def test_no_callers_means_nobody_gets_in() -> None:
    with pytest.raises(AuthError):
        find_caller("sk-a", [])


def test_callers_parse_from_json_and_reject_bad_hashes() -> None:
    raw = json.dumps([c.model_dump() for c in CALLERS])
    assert parse_callers(raw) == CALLERS
    with pytest.raises(ValueError):
        parse_callers(json.dumps([{"key_sha256": "abc", "key_alias": "x", "owner": "x"}]))


def test_new_keys_are_long_and_distinct() -> None:
    keys = {new_key() for _ in range(50)}
    assert len(keys) == 50 and all(len(k) > 40 for k in keys)


async def test_litellm_hook_returns_identity_with_teams(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("litellm")
    from derva.gateway import auth

    monkeypatch.setenv("DERVA_CALLERS", json.dumps([c.model_dump() for c in CALLERS]))
    auth.load_callers.cache_clear()
    identity = await auth.user_api_key_auth(None, "Bearer sk-a")
    assert identity.metadata == {"owner": "a@example.ie", "teams": ["wealth-a"]}
    assert identity.api_key == key_hash("sk-a")
    auth.load_callers.cache_clear()


async def test_litellm_hook_refuses_unknown_keys_with_a_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("litellm")
    from litellm.proxy._types import ProxyException

    from derva.gateway import auth

    monkeypatch.setenv("DERVA_CALLERS", json.dumps([c.model_dump() for c in CALLERS]))
    auth.load_callers.cache_clear()
    with pytest.raises(ProxyException) as caught:
        await auth.user_api_key_auth(None, "Bearer sk-nope")
    assert str(caught.value.code) == "401"
    auth.load_callers.cache_clear()
