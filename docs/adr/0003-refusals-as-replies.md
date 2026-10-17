# 3. Refusals are ordinary replies

Status: accepted, 2026-10-17

## Context

When the gateway blocks, holds or asks a question, the caller is usually an agent loop or an SDK. An HTTP error makes SDKs retry or raise, and red-teaming tools score it as an error rather than a refusal. The person behind the agent needs a reason and something to quote to support.

## Decision

Return blocked, held and clarify outcomes as normal chat completions (HTTP 200) whose text says what happened and carries the request id. Streamed requests get the same text as a stream.

In LiteLLM 1.105, returning a string from the pre-call hook gives an HTTP 400 for chat requests. So the hook raises a `RejectedRequestError` subclass with status 200. The proxy's streaming branch also loses its logging object before it reads it. The failure hook puts it back, for DERVA's own replies only.

## Consequences

- Clients need no special handling. Logs and the audit trail tie every refusal to a request id.
- The workaround depends on LiteLLM internals. It is pinned, covered by a unit test and the smoke test, and should be reported upstream.
- Refusals are indistinguishable from model output by status code alone. Monitoring counts outcomes from the audit log, not from HTTP status.
