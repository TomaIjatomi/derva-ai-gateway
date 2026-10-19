# Decision log

One entry per decision the results justify. Newest first. Each entry names the
run ids it rests on and the criteria version in force.

## 2026-10-19: Off-topic requests declined; golden set extended

- Questions and thresholds version 2026-10-19.1: a seventh question, `off_topic`,
  and a `decline` outcome. Off-topic requests get a polite reply instead of a
  place in the review queue; safety findings still outrank a decline.
- Golden set extended from 42 to 50 cases: five off-topic requests, one
  off-topic request carrying an injection, and two in-remit cases that must not
  be declined (a client birthday message, and an inbox summary whose tool result
  contains a limerick).
- Rules baselines regenerated (split `all`): golden accuracy 0.70, unsafe-pass
  rate 0.08 (2 of 25), over-hold rate 0.44 (11 of 25), 4 of 5 off-topic
  requests declined. Injection baseline unchanged (0.67).
- Criteria version unchanged (2026-10-17.1). A wrongly declined request counts
  towards the over-hold rate.

## 2026-10-17: Offline baseline recorded

- Criteria version: 2026-10-17.1 (`evals/criteria.yaml`).
- Rules decider on the golden set (42 cases) and injection seed set (52), split
  `all`, committed as `evals/baselines/` for the CI regression gate.
- No decision model has been evaluated yet. Next: Jev, Clef-flash and a small
  LLM on the held-out splits, against the adoption criteria.
