# 2. Typed questions, a fallback chain, and failing closed

Status: accepted, 2026-10-17; amended 2026-10-19 (off-topic question); amended 2026-10-20 (confidence computed by DERVA, missing options)

## Context

The gateway has to judge every request in well under the time a model call takes, in a form policy can act on. Free-text judgements from an LLM are slow, hard to threshold and hard to audit. System One decision models answer typed questions (yes/no, choice, score) with probabilities, in tens of milliseconds. But they are new, and the project must show they beat simpler options rather than assume it.

## Decision

- Ask a fixed, versioned set of seven questions (`config/questions.yaml`): injection, exfiltration, action, route, regulated advice, customer facing, and off-topic. The off-topic question is asked only when the turn includes a user message, because tool results such as emails can be about anything.
- Every decider returns the same normalised answers: probabilities over the options, plus confidence. These deciders are a System One model, a small LLM prompted for JSON probabilities, and rules. Answers outside the question's options are rejected.
- DERVA computes confidence itself from the probabilities, with TypeSafe's formulas, for every decider. A provider's own confidence figure is recorded (`provider_confidence`) but never used by policy, because providers need not compute it the same way, and a threshold must mean the same whichever decider answered.
- Options a decider leaves out share the probability it left unassigned. A one-sided `{"yes": 0.3}` stays 0.3; rescaling it would turn a weak guess into certainty.
- Chain deciders with per-link timeouts and circuit breakers. Policy acts on the first answer; the chain records which links it skipped and why.
- If no decider answers, the request is held for review. The gateway never lets a request through unjudged.
- Keep the review queue for legitimate work the gateway is unsure about. A clearly off-topic request is declined with a polite reply and a request id, at no reviewer's cost. Safety findings (injection, exfiltration) still outrank a decline.
- Do not train a classifier in this project. The comparison is between off-the-shelf options.

## Consequences

- Policy is decider-agnostic, so swapping deciders is a config change, and the evaluation compares like with like.
- The rules floor means an outage of the decision model degrades quality, not safety: rules are conservative and send uncertain cases to people.
- Policy evaluates every rule on every request and the worst finding wins, in the order block, review, decline, clarify, then routing.
- A decline is the one finding that makes others moot: once nothing will be sent to a model, the action, advice and route answers no longer matter. A wrongly declined request costs the caller a rephrase, and the golden set's over-hold rate counts it.
