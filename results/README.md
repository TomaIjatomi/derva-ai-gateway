# Results

`log.jsonl` has one line per evaluation run; `runs/<run_id>/` holds each run's
`summary.json` and per-example `records.jsonl`. `derva eval report` prints the
log as a table.

Rules for what goes here:

- Report held-out numbers only. Tune thresholds and prompts on the tuning split.
- Mock-decider runs check the harness. They are ignored by git and excluded
  from `derva eval report`. Never quote them as results.
- Every run records the git commit, decider, model version and question set
  version, so a number can always be traced to what produced it.
- Decisions that follow from results (adopting a decider, moving a threshold)
  go in `DECISIONS.md` with the run ids that justify them.
