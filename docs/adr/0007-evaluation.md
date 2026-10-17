# 7. Evaluation discipline

Status: accepted, 2026-10-17

## Context

The project's claim is that a fast decision model is worth its place in the gateway. It is easy to make such a claim look true by tuning on the test data, choosing criteria after seeing results, or reporting runs against a mock.

## Decision

- Split every dataset by a hash of the example id: 40% tuning, 60% held out. Tune only on tuning, report only held out.
- Write adoption criteria (`evals/criteria.yaml`) before running any decision model. Change them only with a new version and a logged reason.
- Measure calibration (ECE, Brier, reliability diagram) and the automation curve, not only accuracy, plus p95 latency and cost per thousand decisions.
- Record the commit, decider, model version and question-set version with every run; log decisions with the run ids behind them.
- Pin model versions for evaluation runs; `-latest` aliases move.
- Flag mock runs, exclude them from git and reports, and never quote them.
- Gate CI on the deterministic rules decider against committed baselines, so changes to rules, questions or policy cannot quietly make the gateway worse.

## Consequences

- Results take longer to produce and may disappoint, which is the point.
- The hand-written golden and injection sets carry the author's bias; public datasets and independent labels are the remedy.
