"""Loading and validating the versioned config files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from derva.types import QuestionSet

# The repository root holds config/, data/ and evals/. An installed package (as in
# the container image) cannot find it from its own path, so DERVA_HOME says where.
HOME = Path(os.environ.get("DERVA_HOME") or Path(__file__).resolve().parents[2])
CONFIG_DIR = HOME / "config"

RouteTier = Literal["small_model", "frontier_model"]


class NoulThreshold(BaseModel):
    block_at: float = Field(ge=0, le=1)
    review_at: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> NoulThreshold:
        if self.review_at > self.block_at:
            raise ValueError("review_at must not exceed block_at")
        return self


class ActionThreshold(BaseModel):
    min_confidence: float = Field(ge=0, le=1)


class AdviceThreshold(BaseModel):
    regulated_advice_at: float = Field(ge=0, le=1)
    customer_facing_at: float = Field(ge=0, le=1)


class OffTopicThreshold(BaseModel):
    decline_at: float = Field(ge=0, le=1)


class RouteThreshold(BaseModel):
    min_confidence: float = Field(ge=0, le=1)
    default: RouteTier = "frontier_model"


class ToolPolicy(BaseModel):
    effectful: list[str]
    read_only: list[str]

    @model_validator(mode="after")
    def _disjoint(self) -> ToolPolicy:
        overlap = set(self.effectful) & set(self.read_only)
        if overlap:
            raise ValueError(f"tools listed as both effectful and read-only: {sorted(overlap)}")
        return self


class Thresholds(BaseModel):
    version: str
    injection: NoulThreshold
    exfiltration: NoulThreshold
    action: ActionThreshold
    advice: AdviceThreshold
    route: RouteThreshold
    off_topic: OffTopicThreshold
    tools: ToolPolicy


def load_questions(path: Path | None = None) -> QuestionSet:
    raw = yaml.safe_load((path or CONFIG_DIR / "questions.yaml").read_text())
    return QuestionSet.model_validate(raw)


def load_thresholds(path: Path | None = None) -> Thresholds:
    raw = yaml.safe_load((path or CONFIG_DIR / "thresholds.yaml").read_text())
    return Thresholds.model_validate(raw)
