# 2. Typed questions, a fallback chain, and failing closed

Status: accepted, 2026-10-17

## Context

The gateway has to judge every request in well under the time a model call takes, in a form policy can act on. Free-text judgements from an LLM are slow, hard to threshold and hard to audit. System One decision models answer typed questions (yes/no, choice, score) with probabilities, in tens of milliseconds. But they are new, and the project must show they beat simpler options rather than assume it.

## Decision

- Ask a fixed, versioned set of six questions (`config/questions.yaml`): injection, exfiltration, action, route, regulated advice, customer facing.
- Every decider returns the same normalised answers: probabilities over the options, plus confidence. These deciders are a System One model, a small LLM prompted for JSON probabilities, and rules. Answers outside the question's options are rejected.
- Chain deciders with per-link timeouts and circuit breakers. Policy acts on the first answer; the chain records which links it skipped and why.
- If no decider answers, the request is held for review. The gateway never lets a request through unjudged.
- Do not train a classifier in this project. The comparison is between off-the-shelf options.

## Consequences

- Policy is decider-agnostic, so swapping deciders is a config change, and the evaluation compares like with like.
- The rules floor means an outage of the decision model degrades quality, not safety: rules are conservative and send uncertain cases to people.
- Policy evaluates every outcome on every request, and the worst finding wins, so adding a question can only add holds.
