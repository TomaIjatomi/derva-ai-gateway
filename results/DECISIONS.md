# Decision log

One entry per decision the results justify. Newest first. Each entry names the
run ids it rests on and the criteria version in force.

## 2026-10-26: The small-LLM baseline made runnable and fair

- The LLM decider sent `temperature=0`, which Claude 5.5 models reject, so a
  Haiku 5.5 baseline run would have failed on every example. It now asks for
  temperature 0 and lets LiteLLM drop it where a model does not take it.
- It now asks for the least thinking each model allows: minimal on Gemini
  Flash-Lite, low effort on Claude 5.5 (whose adaptive thinking cannot be
  switched off), nothing for models that do not think. Thinking is billed as
  output and adds little to a classification. Checked against the requests
  LiteLLM 1.105.1 sends, with a local stand-in for each provider.
- `derva eval run --rpm N` spaces calls for rate-limited providers (Gemini's
  free tier allows 15 a minute); time spent waiting is left out of latency.
- Empty model variables (as CI passes unset ones) now fall back to the
  defaults instead of an empty model name.
- Golden runs report `auto@95` as "-": outcomes carry no probability. Golden
  baseline regenerated (run 20261026-074854-golden-rules-d88094) for that field
  only; every gated number is unchanged.

## 2026-10-20: Confidence computed by DERVA for every decider

- A System One provider's own confidence figure used to replace DERVA's for
  choice and score answers, so the `action` and `route` thresholds could mean
  different things depending on which decision model answered. DERVA now always
  computes confidence from the probabilities with TypeSafe's formulas; the
  provider's figure is kept as `provider_confidence` in the audit log and eval
  records, and policy never reads it (ADR 2, amended).
- Options a decider leaves out now share the probability it left unassigned,
  instead of getting zero before rescaling. A one-sided `{"yes": 0.3}` from an
  LLM had become certainty, enough to decline a request or pick a route.
- No decision model had been run, so no recorded result is affected. Rules
  baselines unchanged (the rules always answer every option). Thresholds and
  criteria versions unchanged.

## 2026-10-19 (later): Written criteria for the advice questions

- Questions version 2026-10-19.2 (thresholds unchanged at 2026-10-19.1):
  `regulated_advice` and `customer_facing` now define both answers, like the
  other yes/no questions. Advice means a recommendation about what a specific
  person should do with their money; information, account facts and records of
  advice already given are not. Customer-facing includes drafts a person will
  check and send, and outside parties such as a client's accountant.
- The general-LLM fallback now receives yes/no criteria. It previously got only
  the instruction line, so the baseline LLM saw less than the decision model.
  Criteria keys for yes/no questions are validated, and a test requires every
  yes/no question to define both answers.
- Golden set extended from 50 to 56 cases on those two boundaries.
- Rules baselines regenerated (split `all`, runs
  20261019-222855-golden-rules-6ede63 and 20261019-222911-injection-rules-060fac):
  golden accuracy 0.68, unsafe-pass rate 0.07 (2 of 27), over-hold rate 0.48
  (14 of 29). The rules ignore criteria and hold three of the four new good
  cases, so the drop is the new cases, not a regression. Injection unchanged (0.67).
- Criteria version unchanged (2026-10-17.1).

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
