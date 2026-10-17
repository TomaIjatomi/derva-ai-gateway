# garak runs

The nightly workflow scans the same model twice, through the gateway and
around it, and `scripts/garak_summary.py` writes the attack success rate per
probe for both to `results/garak/<date>.json`.

- Probes: `promptinject`, `dan.Dan_11_0`, `latentinjection`, `encoding.InjectBase64`
  (chosen for the threats the gateway claims to stop; extend as needed).
- One generation per prompt, to keep the cost of a nightly run down.
- `generator.json` follows garak's `OpenAICompatible` generator options. If a
  garak release renames an option, fix it here; the first nightly run will show it.
- A gateway refusal is an ordinary completion, so garak scores it as a failed
  attack rather than an error. Over-blocking is measured separately by the
  golden set, not by garak.
