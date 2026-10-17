from __future__ import annotations

from pathlib import Path

import pytest

from derva.audit import AuditStore
from derva.config import Thresholds, load_questions, load_thresholds
from derva.deciders.rules import RulesDecider
from derva.masking import RegexMasker
from derva.pipeline import Pipeline
from derva.resolution import Caller, InMemoryDirectory
from derva.types import QuestionSet

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def questions() -> QuestionSet:
    return load_questions()


@pytest.fixture
def thresholds() -> Thresholds:
    return load_thresholds()


@pytest.fixture
def directory() -> InMemoryDirectory:
    return InMemoryDirectory.from_json(ROOT / "data" / "fixtures" / "crm_seed.json")


@pytest.fixture
def caller() -> Caller:
    return Caller(key_id="key-demo-agent", owner="toma@example.ie", teams=["wealth-a"])


@pytest.fixture
def audit() -> AuditStore:
    return AuditStore("sqlite://")


@pytest.fixture
def pipeline(
    directory: InMemoryDirectory,
    questions: QuestionSet,
    thresholds: Thresholds,
    audit: AuditStore,
) -> Pipeline:
    return Pipeline(
        directory=directory,
        masker=RegexMasker(),
        decider=RulesDecider(),
        questions=questions,
        thresholds=thresholds,
        audit=audit,
    )
