# 4. Approval binds to tool calls, not requests

Status: accepted, 2026-10-17

## Context

An early design held any request whose intent looked effectful ("email Mary her statement"). That holds work before anything concrete exists to approve: the reviewer sees an intention, not the email. It also misses effectful actions that come from harmless-looking requests.

## Decision

- Incoming requests are judged for safety and routing only. Effectful intent alone does not hold them.
- When the model proposes tool calls, each call is judged by tool name: effectful and unknown tools need approval, read-only tools pass. The decision layer may tighten this (an exfiltration signal on a read-only call), never loosen it.
- In v0.1 the post-call hook removes held calls from the response and replies with an approval id, a hash of the exact tool and arguments. If any call in a message is held, the whole message is held.
- In v0.2 approvals become signed, single-use grants for the exact arguments. Tool servers verify them, so a call cannot run without one, even if an agent calls the tool directly.

## Consequences

- Reviewers approve concrete actions, and a changed argument means a new approval.
- v0.1 relies on the agent receiving tools only through the gateway's responses. The v0.2 grants remove that assumption.
