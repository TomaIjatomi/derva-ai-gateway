# 8. Caller keys outside LiteLLM's database

Status: accepted, 2026-10-17

## Context

Client scoping needs each key's owner and teams. LiteLLM keeps virtual keys and their metadata in its own Postgres database through Prisma, which needs generated client code and engine binaries in the image, plus migrations at startup. Without that database, the proxy has one master key and cannot tell callers apart.

## Decision

- Use LiteLLM's `custom_auth` hook. DERVA's list of callers is a JSON list of SHA-256 key hashes, aliases, owners and teams, kept in Secret Manager. It is checked in constant time, and unknown keys get a 401.
- A key without teams can act for no client.
- The master key LiteLLM insists on at boot is not accepted for requests, so LiteLLM's admin routes stay closed.
- Mint keys with `derva keys new`. The key is shown once; only its hash is stored.

## Consequences

- No Prisma, no second database schema, and the image stays simpler.
- No LiteLLM admin UI, per-key budgets or spend tracking in v0.1. Adding LiteLLM's database later would bring those back, with DERVA's auth reading teams from key metadata instead.
- Rotating a key means editing the secret and redeploying.
