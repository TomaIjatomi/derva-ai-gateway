"""Evaluation datasets and the fixed tuning/held-out split.

Three tasks, each with a committed sample so CI runs offline:

* ``banking77``: 77-way intent classification (PolyAI, CC BY 4.0). Tests a
  decider on a wide choice question. Full test set: ``fetch_banking77()``.
* ``injection``: is this text trying to override the assistant's instructions?
  The committed seed set is hand-written and synthetic. The public deepset set
  loads with ``fetch_deepset_injections()`` where Hugging Face is reachable.
* ``golden``: whole-gateway cases with the outcome a careful operator would
  want. Hand-written for v0.1; review and extend before reporting results.

The split is a hash of the example id, so it never changes between runs and
nobody can move a hard example from one side to the other. Thresholds and
prompts are tuned on ``tuning`` only; reported numbers come from ``heldout``.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from derva.config import CONFIG_DIR
from derva.types import Question, QuestionSet, QuestionType

DATA_DIR = CONFIG_DIR.parent / "data"
FIXTURES = DATA_DIR / "fixtures"
CACHE = DATA_DIR / "cache"

Task = Literal["banking77", "injection", "golden"]
Split = Literal["tuning", "heldout", "all"]
TUNING_SHARE = 0.4

BANKING77_RAW = (
    "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data"
)
DEEPSET_ROWS = "https://datasets-server.huggingface.co/rows?dataset=deepset/prompt-injections&config=default&split={split}&offset={offset}&length=100"


class Example(BaseModel):
    id: str
    text: str
    label: str
    messages: list[dict[str, Any]] | None = None
    category: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


def split_of(example_id: str) -> Literal["tuning", "heldout"]:
    bucket = int(hashlib.sha256(example_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "tuning" if bucket < TUNING_SHARE else "heldout"


def select(examples: list[Example], split: Split, limit: int | None = None) -> list[Example]:
    chosen = examples if split == "all" else [e for e in examples if split_of(e.id) == split]
    return chosen[:limit] if limit else chosen


# --- Banking77 ---------------------------------------------------------------


def _read_banking77_csv(path: Path, prefix: str) -> list[Example]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [
        Example(id=f"{prefix}-{i:05d}", text=row["text"], label=row["category"])
        for i, row in enumerate(rows)
    ]


def fetch_banking77(cache: Path = CACHE) -> list[Example]:
    """The full Banking77 test set (3,080 rows), downloaded once from GitHub."""
    path = cache / "banking77_test.csv"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        response = httpx.get(f"{BANKING77_RAW}/test.csv", timeout=30, follow_redirects=True)
        response.raise_for_status()
        path.write_bytes(response.content)
    return _read_banking77_csv(path, "b77")


def banking77_sample() -> list[Example]:
    """Two examples per intent (154 rows), committed for offline runs."""
    return _read_banking77_csv(FIXTURES / "banking77_sample.csv", "b77s")


def make_banking77_sample(full: list[Example], per_class: int = 2) -> list[Example]:
    by_label: dict[str, list[Example]] = defaultdict(list)
    for example in full:
        by_label[example.label].append(example)
    sample: list[Example] = []
    for label in sorted(by_label):
        ranked = sorted(by_label[label], key=lambda e: hashlib.sha256(e.text.encode()).hexdigest())
        sample.extend(ranked[:per_class])
    return sample


def banking77_labels() -> list[str]:
    labels: list[str] = json.loads((FIXTURES / "banking77_categories.json").read_text())
    return labels


def banking77_questions() -> QuestionSet:
    """One choice question, criteria fixed before any run: the label, spelled out."""
    labels = banking77_labels()
    return QuestionSet(
        version="banking77-v1",
        questions=[
            Question(
                id="intent",
                type=QuestionType.CHOICE,
                instructions=(
                    "Which banking customer-service intent does this customer message express?"
                ),
                criteria={label: label.replace("_", " ") for label in labels},
            )
        ],
    )


# --- Prompt injection --------------------------------------------------------


def _read_jsonl(path: Path) -> list[Example]:
    return [
        Example.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()
    ]


def injection_seed() -> list[Example]:
    """Hand-written synthetic set: direct and indirect injections, and hard negatives."""
    return _read_jsonl(FIXTURES / "injection_seed.jsonl")


def fetch_deepset_injections(split: str = "test", cache: Path = CACHE) -> list[Example]:
    """deepset/prompt-injections (Apache 2.0) via the Hugging Face rows API."""
    path = cache / f"deepset_injections_{split}.jsonl"
    if not path.exists():
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = httpx.get(DEEPSET_ROWS.format(split=split, offset=offset), timeout=30)
            response.raise_for_status()
            batch = response.json()["rows"]
            rows.extend(r["row"] for r in batch)
            if len(batch) < 100:
                break
            offset += 100
        cache.mkdir(parents=True, exist_ok=True)
        examples = [
            Example(
                id=f"deepset-{split}-{i:05d}",
                text=r["text"],
                label="yes" if r["label"] == 1 else "no",
            )
            for i, r in enumerate(rows)
        ]
        path.write_text("".join(e.model_dump_json() + "\n" for e in examples))
    return _read_jsonl(path)


# --- Golden set --------------------------------------------------------------


def golden() -> list[Example]:
    return _read_jsonl(DATA_DIR / "golden" / "golden_v0.jsonl")


def load(task: Task, *, full: bool = False) -> list[Example]:
    if task == "banking77":
        return fetch_banking77() if full else banking77_sample()
    if task == "injection":
        return fetch_deepset_injections() if full else injection_seed()
    return golden()
