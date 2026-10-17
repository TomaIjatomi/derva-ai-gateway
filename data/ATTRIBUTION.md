# Data sources

| File | Source | Licence |
|---|---|---|
| `fixtures/banking77_sample.csv`, `fixtures/banking77_categories.json` | Banking77, PolyAI ([task-specific-datasets](https://github.com/PolyAI-LDN/task-specific-datasets)): two test-set examples per intent, chosen by a hash of the text | CC BY 4.0 |
| `cache/banking77_test.csv` (downloaded, not committed) | same, full test set | CC BY 4.0 |
| `cache/deepset_injections_*.jsonl` (downloaded, not committed) | [deepset/prompt-injections](https://huggingface.co/datasets/deepset/prompt-injections) | Apache 2.0 |
| `fixtures/injection_seed.jsonl` | Written by hand for DERVA; synthetic | MIT, as the repo |
| `golden/golden_v0.jsonl` | Written by hand for DERVA; synthetic clients from `fixtures/crm_seed.json` | MIT, as the repo |
| `fixtures/crm_seed.json` | Invented clients; any resemblance to real people is accidental | MIT, as the repo |

Banking77 citation: Casanueva et al., *Efficient Intent Detection with Dual Sentence Encoders*, NLP4ConvAI 2020.
