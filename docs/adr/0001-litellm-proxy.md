# 1. LiteLLM proxy as the base

Status: accepted, 2026-10-17

## Context

The gateway needs an OpenAI-compatible endpoint, many model providers, streaming, and places to run code before and after each model call. Building that from scratch would spend the project's effort on plumbing rather than on the controls and the evaluation. The alternatives were a custom FastAPI service, Envoy with an external processor, or a hosted gateway.

## Decision

Run the LiteLLM proxy and add DERVA as hooks: a pre-call hook for the request checks and a post-call hook for the tool-call checkpoint. Reach decision models through LiteLLM's `asystemone`, so Jev, Clef-flash or any future System One provider is a model string. Pin LiteLLM to the version the hooks were verified against.

## Consequences

- Any OpenAI SDK client works unchanged, and so do red-teaming tools such as garak.
- DERVA depends on LiteLLM internals in two places: how a hook's rejection becomes a reply, and a workaround for streamed rejections (see ADR 3). Upgrades need the smoke test to pass first. Dependabot is told not to bump LiteLLM.
- Only chat completions are served. Other routes are refused rather than left unchecked.
