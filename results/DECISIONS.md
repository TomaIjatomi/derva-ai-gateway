# Decision log

One entry per decision the results justify. Newest first. Each entry names the
run ids it rests on and the criteria version in force.

## 2026-10-17: Offline baseline recorded

- Criteria version: 2026-10-17.1 (`evals/criteria.yaml`).
- Rules decider on the golden set (42 cases) and injection seed set (52), split
  `all`, committed as `evals/baselines/` for the CI regression gate.
- No decision model has been evaluated yet. Next: Jev, Clef-flash and a small
  LLM on the held-out splits, against the adoption criteria.
