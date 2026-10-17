"""Shared types for questions, answers and decisions.

The wire format follows TypeSafe's System One API (``POST /v1/systemone``), which
LiteLLM also serves for any decision model. Three question types exist:

* ``noul``: probability that a statement is true (no separate confidence)
* ``choice``: one option from a fixed list, with probabilities and confidence
* ``score``: a rating on ordered levels, with probabilities and confidence
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class QuestionType(StrEnum):
    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


class Question(BaseModel):
    """One typed question asked of the decider."""

    id: str
    type: QuestionType
    instructions: str
    # choice: {option: description | None}; score: ordered level descriptions;
    # noul: optional {"true": ..., "false": ...} descriptions.
    criteria: dict[str, str | None] | list[str] | None = None

    @model_validator(mode="after")
    def _check_criteria(self) -> Question:
        if self.type is QuestionType.CHOICE:
            if not isinstance(self.criteria, dict) or len(self.criteria) < 2:
                raise ValueError(f"choice question {self.id!r} needs at least two options")
            if len(self.criteria) > 255:
                raise ValueError(f"choice question {self.id!r} exceeds 255 options")
        if self.type is QuestionType.SCORE and (
            not isinstance(self.criteria, list) or not 2 <= len(self.criteria) <= 10
        ):
            raise ValueError(f"score question {self.id!r} needs 2 to 10 levels")
        return self

    @property
    def options(self) -> list[str]:
        """The answer space as strings: options, level indices, or yes/no."""
        if self.type is QuestionType.CHOICE:
            assert isinstance(self.criteria, dict)
            return list(self.criteria)
        if self.type is QuestionType.SCORE:
            assert isinstance(self.criteria, list)
            return [str(i) for i in range(len(self.criteria))]
        return ["yes", "no"]

    def to_wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type.value, "instructions": self.instructions}
        if self.criteria is not None:
            body["criteria"] = self.criteria
        return body


class QuestionSet(BaseModel):
    """A versioned set of questions, loaded from ``config/questions.yaml``."""

    version: str
    questions: list[Question]

    @model_validator(mode="after")
    def _unique_ids(self) -> QuestionSet:
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique")
        return self

    def get(self, question_id: str) -> Question:
        for q in self.questions:
            if q.id == question_id:
                return q
        raise KeyError(question_id)

    def subset(self, ids: list[str]) -> QuestionSet:
        return QuestionSet(version=self.version, questions=[self.get(i) for i in ids])

    def to_wire(self) -> dict[str, dict[str, Any]]:
        return {q.id: q.to_wire() for q in self.questions}


class Answer(BaseModel):
    """A typed answer, normalised across deciders.

    ``probabilities`` always maps every option in the question's answer space to a
    probability. For a noul answer the space is ``{"yes", "no"}``.
    """

    question_id: str
    type: QuestionType
    probabilities: dict[str, float]
    confidence: float | None = None
    score: float | None = None  # probability-weighted level, score questions only

    @property
    def top(self) -> str:
        return max(self.probabilities, key=lambda k: self.probabilities[k])

    @property
    def top_probability(self) -> float:
        return self.probabilities[self.top]

    @property
    def p_yes(self) -> float:
        if self.type is not QuestionType.NOUL:
            raise TypeError("p_yes is only defined for noul answers")
        return self.probabilities["yes"]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class DecisionResult(BaseModel):
    """Everything one decider returned for one state."""

    decider: str
    model: str | None = None
    answers: dict[str, Answer]
    latency_ms: float
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = None
    # Which decider in a fallback chain produced this result, and what failed first.
    fallbacks: list[str] = Field(default_factory=list)

    def answer(self, question_id: str) -> Answer:
        return self.answers[question_id]
