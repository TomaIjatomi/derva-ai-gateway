"""Rules: the floor of the comparison, and the last link in the fallback chain.

Free, instant and fully explainable, but shallow. The rules are deliberately
modest about what they know: they answer confidently only when a pattern is
obvious, and otherwise return low-confidence answers, which the policy engine
turns into a review. That matches the design: rules handle the obvious cases,
and everything else goes to a person.

Questions the rules know nothing about (for example Banking77's 77 intents) get
a uniform distribution. That is the honest answer, not a bug.
"""

from __future__ import annotations

import re
import time

from derva.deciders.base import make_answer
from derva.types import DecisionResult, Question, QuestionSet, QuestionType

_I = re.IGNORECASE

INJECTION_PATTERNS = [
    re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your|system)\b.{0,20}\b(instructions?|prompts?|rules|guidelines)\b",
        _I,
    ),
    re.compile(
        r"\b(reveal|print|show|repeat|output|leak)\b.{0,30}\b(system|hidden|secret|initial)\b.{0,10}\b(prompt|instructions?|message)\b",
        _I,
    ),
    re.compile(r"\byou are (now|no longer)\b", _I),
    re.compile(r"\b(new|updated) (instructions?|system prompt)\s*:", _I),
    re.compile(r"\b(do anything now|developer mode|jailbreak|DAN mode)\b", _I),
    re.compile(r"<\|im_start\|>|\[INST\]|###\s*system\b|<\s*system\s*>", _I),
    re.compile(
        r"\b(assistant|ai|model)\b.{0,30}\b(must|should|will)\b.{0,20}\b(now|instead)\b", _I
    ),
]

EXFILTRATION_PATTERNS = [
    re.compile(
        r"\b(send|forward|email|upload|post|copy|export|transfer)\b.{0,80}\b(all|every|entire|full|complete)\b.{0,40}\b(records?|data|clients?|accounts?|details|list|database|history)\b",
        _I,
    ),
    re.compile(
        r"\b(send|forward|email|upload|post|copy|export|transfer)\b.{0,60}\b(list|records?|data|database|details|history|statements?)\b.{0,40}\bto\b\s*(<EMAIL_ADDRESS_\d+>|\S+@\S+|https?://)",
        _I,
    ),
    re.compile(r"\b(send|forward|post|upload)\b.{0,60}(https?://|www\.|webhook)", _I),
    re.compile(r"\b(bcc|cc)\b.{0,30}<EMAIL_ADDRESS_\d+>", _I),
]

ACTION_PATTERNS = {
    "send_external": re.compile(
        r"\b(email|e-mail|send|forward|reply|message|notify|text|post)\b", _I
    ),
    "change_records": re.compile(
        r"\b(update|change|edit|delete|remove|modify|create|add|amend|close)\b", _I
    ),
    "read_internal_data": re.compile(
        r"\b(look ?up|find|show|list|check|what (is|was|were|did|are)|how much|balance|fees|holdings|statement)\b",
        _I,
    ),
}

FRONTIER_PATTERN = re.compile(
    r"\b(analy[sz]e|compare|calculate|reconcile|forecast|project(ion)?s?|trend|across|why|explain|summari[sz]e|plan)\b",
    _I,
)
ADVICE_PATTERN = re.compile(
    r"\b(should (i|she|he|they|we)|recommend|advice|advise|which fund|invest|pension|mortgage|loan|retire(ment)?|allocation)\b",
    _I,
)
EXTERNAL_PATTERN = re.compile(
    r"\b(email|send|letter|reply|write to)\b.{0,60}\b(client|customer|her|him|them)\b|client:C-\d+",
    _I,
)


def _noul(hit: bool, p_hit: float = 0.9, p_miss: float = 0.1) -> dict[str, float]:
    p = p_hit if hit else p_miss
    return {"yes": p, "no": 1 - p}


def _choice(options: list[str], chosen: str, mass: float) -> dict[str, float]:
    rest = (1 - mass) / (len(options) - 1)
    return {o: (mass if o == chosen else rest) for o in options}


class RulesDecider:
    name = "rules"

    def _answer(self, q: Question, state: str) -> dict[str, float]:
        if q.id == "injection":
            return _noul(any(p.search(state) for p in INJECTION_PATTERNS), 0.95, 0.15)
        if q.id == "exfiltration":
            return _noul(any(p.search(state) for p in EXFILTRATION_PATTERNS), 0.9, 0.1)
        if q.id == "action":
            for action, pattern in ACTION_PATTERNS.items():
                if pattern.search(state):
                    return _choice(q.options, action, 0.7)
            # Nothing obvious: low confidence on purpose, so a person decides.
            return _choice(q.options, "answer_only", 0.4)
        if q.id == "route":
            if len(state) > 1200 or FRONTIER_PATTERN.search(state):
                return _choice(q.options, "frontier_model", 0.8)
            # Only short, plain requests are confidently "small"; anything longer
            # stays below the policy's confidence bar and defaults to frontier.
            return _choice(q.options, "small_model", 0.85 if len(state) < 300 else 0.7)
        if q.id == "regulated_advice":
            return _noul(bool(ADVICE_PATTERN.search(state)), 0.75, 0.1)
        if q.id == "customer_facing":
            return _noul(bool(EXTERNAL_PATTERN.search(state)), 0.8, 0.15)
        # Unknown question: no information, so a uniform answer.
        if q.type is QuestionType.NOUL:
            return {"yes": 0.5, "no": 0.5}
        return {o: 1 / len(q.options) for o in q.options}

    async def decide(self, state: str, questions: QuestionSet) -> DecisionResult:
        start = time.perf_counter()
        answers = {q.id: make_answer(q, self._answer(q, state)) for q in questions.questions}
        return DecisionResult(
            decider=self.name,
            model="rules-v1",
            answers=answers,
            latency_ms=(time.perf_counter() - start) * 1000,
            cost_usd=0.0,
        )
