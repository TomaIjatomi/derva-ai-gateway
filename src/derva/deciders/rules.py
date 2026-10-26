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

# Exfiltration turns on where data would go. The masker labels email addresses
# <FIRM_EMAIL_n> or <EXTERNAL_EMAIL_n>. The rules know no firm domains, so every
# link, and any address that reaches them unmasked, counts as outside.
_SEND = r"\b(send|forward|email|e-mail|upload|post|copy|export|transfer|share|save)\b"
_BULK = r"\b(all|every|entire|full|complete|whole)\b"
_DATA = r"\b(records?|data|clients?|accounts?|details|list|database|history|statements?|balances?|holdings|portfolios?)\b"
_OUT_ADDRESS = r"<EXTERNAL_EMAIL_\d+>|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|https?://"
_OUTSIDE = (
    _OUT_ADDRESS + r"|www\.|webhook"
    r"|\b(personal|private|home)\s+(e-?mail|address|account|inbox)"
    r"|\b(gmail|hotmail|yahoo|icloud|protonmail|dropbox|wetransfer|pastebin)\b"
)
OUTSIDE_PATTERN = re.compile(_OUTSIDE, _I)
INSIDE_PATTERN = re.compile(
    r"<FIRM_EMAIL_\d+>"
    r"|\b(colleagues?|the team|compliance team|back office|crm|shared drive|sharepoint|intranet)\b",
    _I,
)
BULK_PATTERN = re.compile(f"{_SEND}.{{0,80}}{_BULK}.{{0,40}}{_DATA}", _I)

EXFILTRATION_PATTERNS = [
    # Records in bulk, with somewhere outside the firm in view.
    re.compile(f"{_SEND}.{{0,80}}{_BULK}.{{0,40}}{_DATA}.{{0,80}}({_OUTSIDE})", _I),
    # Data sent to an outside address or link.
    re.compile(
        _SEND
        + r".{0,60}\b(list|records?|data|database|details|history|statements?|balances?|portfolio)\b"
        + rf".{{0,40}}\b(to|into)\b\s*({_OUT_ADDRESS})",
        _I,
    ),
    # Anything sent to the user's own personal account.
    re.compile(
        r"\b(to|into)\s+(my|his|her|their)\s+(own\s+)?(personal|private|home|gmail|hotmail|yahoo|icloud)\b",
        _I,
    ),
    re.compile(r"\b(send|forward|post|upload)\b.{0,60}(https?://|www\.|webhook)", _I),
    re.compile(r"\b(bcc|cc)\b.{0,30}<EXTERNAL_EMAIL_\d+>", _I),
]

ACTION_PATTERNS = {
    # A send is internal only when it names somewhere inside and nothing outside.
    "send": re.compile(r"\b(email|e-mail|send|forward|reply|message|notify|text|post)\b", _I),
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
# Off-topic: only obvious cases. A work word anywhere wins, and anything the rules
# cannot place stays below the decline threshold, so the normal checks decide.
OFF_TOPIC_PATTERN = re.compile(
    r"\b(poems?|poetry|limericks?|haiku|sonnet|songs?|lyrics|jokes?|riddles?|recipes?|baking|horoscope|crossword|sudoku|movies?|films?|netflix|football|soccer|rugby|hurling|video games?|dating)\b",
    _I,
)
WORK_PATTERN = re.compile(
    r"\b(clients?|customers?|accounts?|portfolios?|funds?|fees?|pensions?|invest\w*|statements?|holdings?|balances?|reviews?|compliance|mortgages?|loans?|tax|markets?|meetings?|emails?|reports?|records?|advice|products?|transfers?|payments?|kyc|aml)\b|client:C-\d+",
    _I,
)


def user_text(state: str) -> str:
    """The user's own words from a labelled state; tool results are left out.

    Unlabelled text (an evaluation example, a direct API call) counts as the
    user's words.
    """
    if not state.startswith("["):
        return state
    lines, keep = [], False
    for line in state.splitlines():
        if line.startswith("[user] "):
            keep = True
            lines.append(line.removeprefix("[user] "))
        elif line.startswith("[tool result] ") or line.startswith("[system] "):
            keep = False
        elif keep:
            lines.append(line)
    return "\n".join(lines)


EXTERNAL_PATTERN = re.compile(
    r"\b(email|send|letter|reply|write to)\b.{0,60}\b(client|customer|her|him|them)\b|client:C-\d+",
    _I,
)


def _inside_only(state: str) -> bool:
    """Names somewhere inside the firm, and nowhere outside it."""
    return bool(INSIDE_PATTERN.search(state)) and not OUTSIDE_PATTERN.search(state)


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
            if any(p.search(state) for p in EXFILTRATION_PATTERNS):
                return _noul(True, 0.9)
            if BULK_PATTERN.search(state) and not _inside_only(state):
                # Records in bulk with no destination the rules can place: a person decides.
                return {"yes": 0.5, "no": 0.5}
            return _noul(False, p_miss=0.1)
        if q.id == "action":
            for action, pattern in ACTION_PATTERNS.items():
                if pattern.search(state):
                    if action == "send":
                        action = "send_internal" if _inside_only(state) else "send_external"
                    if action in q.options:
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
        if q.id == "off_topic":
            words = user_text(state)
            if WORK_PATTERN.search(words):
                return _noul(False, p_miss=0.05)
            # Unsure (0.2) stays well below the decline threshold.
            return _noul(bool(OFF_TOPIC_PATTERN.search(words)), 0.85, 0.2)
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
