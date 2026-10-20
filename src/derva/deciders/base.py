"""The decider interface, and the confidence formulas TypeSafe documents.

A decider answers a set of typed questions about one piece of (masked) text.
Every implementation, whether rules, an LLM or a decision model, returns the same
``DecisionResult``, so the policy engine and the eval harness never need to know
which one answered.
"""

from __future__ import annotations

from typing import Protocol

from derva.types import Answer, DecisionResult, Question, QuestionSet, QuestionType


class DeciderError(Exception):
    """A decider could not produce a valid answer set."""


class Decider(Protocol):
    name: str

    async def decide(self, state: str, questions: QuestionSet) -> DecisionResult: ...


def noul_confidence(p_yes: float) -> float:
    """Distance from an even split, on the same 0-1 scale as choice confidence."""
    return abs(2 * p_yes - 1)


def choice_confidence(probabilities: dict[str, float]) -> float:
    n = len(probabilities)
    p_max = max(probabilities.values())
    return max(0.0, min(1.0, (p_max - 1 / n) / (1 - 1 / n)))


def score_confidence(probabilities: list[float]) -> float:
    n = len(probabilities)
    peak = probabilities.index(max(probabilities))
    spread = sum(p * abs(i - peak) for i, p in enumerate(probabilities))
    even_spread = sum(abs(i - (n - 1) / 2) for i in range(n)) / n
    return max(0.0, 1 - spread / even_spread)


def normalise(probabilities: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in probabilities.values())
    if total <= 0:
        n = len(probabilities)
        return {k: 1 / n for k in probabilities}
    return {k: max(0.0, v) / total for k, v in probabilities.items()}


def fill_missing(options: list[str], given: dict[str, float]) -> dict[str, float]:
    """Give the options a decider left out the probability it left unassigned.

    Without this, ``{"yes": 0.3}`` would be rescaled to certainty. With it, "no"
    gets the remaining 0.7. Nothing given at all becomes an even split.
    """
    filled = {o: max(0.0, float(given[o])) for o in options if o in given}
    missing = [o for o in options if o not in given]
    if missing:
        rest = max(0.0, 1.0 - sum(filled.values())) / len(missing)
        filled.update({o: rest for o in missing})
    return {o: filled[o] for o in options}


def make_answer(question: Question, probabilities: dict[str, float]) -> Answer:
    """Build a normalised answer from probabilities over the question's options.

    Unknown options are an error, because a decider that invents options has
    broken the type contract. Missing options share whatever probability the
    decider left unassigned. Confidence is always computed here, never taken
    from the decider, so thresholds mean the same for every decider.
    """
    unknown = set(probabilities) - set(question.options)
    if unknown:
        raise DeciderError(f"{question.id}: answer outside the schema: {sorted(unknown)}")
    probs = normalise(fill_missing(question.options, probabilities))
    if question.type is QuestionType.NOUL:
        return Answer(
            question_id=question.id,
            type=question.type,
            probabilities=probs,
            confidence=noul_confidence(probs["yes"]),
        )
    if question.type is QuestionType.CHOICE:
        return Answer(
            question_id=question.id,
            type=question.type,
            probabilities=probs,
            confidence=choice_confidence(probs),
        )
    levels = [probs[str(i)] for i in range(len(question.options))]
    return Answer(
        question_id=question.id,
        type=question.type,
        probabilities=probs,
        confidence=score_confidence(levels),
        score=sum(i * p for i, p in enumerate(levels)),
    )
