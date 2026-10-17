"""Masking personal data before any text leaves the gateway.

The decision model may run outside the EU (TypeSafe's service currently runs on
the US West Coast), so it only ever receives masked text. Masking reduces the
personal data it sees but cannot remove all of it: Presidio misses some, and
confidential data that is not personal data passes through. The eval harness
measures how much masking catches; this module makes no stronger claim.

Placeholders are numbered per distinct value (``<EMAIL_ADDRESS_1>``,
``<EMAIL_ADDRESS_2>``), so the decider can still tell that two addresses differ,
which matters for the exfiltration question. The mapping back to real values is
held in memory for one request only and is never written anywhere.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from derva.resolution import REFERENCE_PATTERN

# Presidio's email recogniser uses tldextract, which tries to download the public
# suffix list and falls back to a bundled snapshot when offline. Silence the
# noisy traceback it logs on the way.
logging.getLogger("tldextract").setLevel(logging.CRITICAL)

DEFAULT_ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IBAN_CODE",
    "CREDIT_CARD",
    "IP_ADDRESS",
)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity: str


@dataclass
class MaskResult:
    text: str
    counts: dict[str, int]
    # Placeholder -> original value. In memory only; never serialised or logged.
    _mapping: dict[str, str] = field(default_factory=dict, repr=False)

    def unmask(self, text: str) -> str:
        for placeholder, original in self._mapping.items():
            text = text.replace(placeholder, original)
        return text


class Masker(Protocol):
    def mask(self, text: str) -> MaskResult: ...


def _apply(text: str, spans: list[Span]) -> MaskResult:
    """Replace spans with numbered placeholders, skipping client references."""
    protected = [(m.start(), m.end()) for m in REFERENCE_PATTERN.finditer(text)]

    def _overlaps_protected(s: Span) -> bool:
        return any(s.start < end and start < s.end for start, end in protected)

    # Drop overlaps: keep the longest span first, then the earliest.
    chosen: list[Span] = []
    for span in sorted(spans, key=lambda s: (-(s.end - s.start), s.start)):
        if _overlaps_protected(span):
            continue
        if any(span.start < c.end and c.start < span.end for c in chosen):
            continue
        chosen.append(span)
    chosen.sort(key=lambda s: s.start)

    numbering: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    mapping: dict[str, str] = {}
    out: list[str] = []
    cursor = 0
    for span in chosen:
        value = text[span.start : span.end]
        key = (span.entity, value)
        if key not in numbering:
            counters[span.entity] = counters.get(span.entity, 0) + 1
            numbering[key] = f"<{span.entity}_{counters[span.entity]}>"
            mapping[numbering[key]] = value
        out.append(text[cursor : span.start])
        out.append(numbering[key])
        cursor = span.end
    out.append(text[cursor:])
    return MaskResult(text="".join(out), counts=counters, _mapping=mapping)


_REGEXES = {
    "EMAIL_ADDRESS": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "IBAN_CODE": re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
    "PHONE_NUMBER": re.compile(r"(?<![\w+])\+?\d[\d ()-]{7,}\d\b"),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


class RegexMasker:
    """Pattern-only masking. Catches structured identifiers but not names.

    Used in tests, and as the fallback when Presidio is not installed. On its own
    it is not enough for production: it cannot find a person's name.
    """

    def mask(self, text: str) -> MaskResult:
        spans = [
            Span(m.start(), m.end(), entity)
            for entity, rx in _REGEXES.items()
            for m in rx.finditer(text)
        ]
        return _apply(text, spans)


class PresidioMasker:
    """Presidio with a spaCy model, plus the structured-identifier patterns."""

    def __init__(
        self,
        model_name: str = "en_core_web_sm",
        entities: tuple[str, ...] = DEFAULT_ENTITIES,
        score_threshold: float = 0.4,
    ) -> None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": model_name}],
            }
        )
        self._analyzer = AnalyzerEngine(nlp_engine=provider.create_engine())
        self._entities = list(entities)
        self._threshold = score_threshold
        self._regex = RegexMasker()

    def mask(self, text: str) -> MaskResult:
        results = self._analyzer.analyze(
            text=text,
            entities=self._entities,
            language="en",
            score_threshold=self._threshold,
        )
        spans = [Span(r.start, r.end, r.entity_type) for r in results]
        spans += [
            Span(m.start(), m.end(), entity)
            for entity, rx in _REGEXES.items()
            for m in rx.finditer(text)
        ]
        return _apply(text, spans)


def default_masker() -> Masker:
    """Presidio when available, otherwise patterns only (with a loud warning)."""
    try:
        return PresidioMasker()
    except Exception as exc:  # pragma: no cover - depends on the environment
        import warnings

        warnings.warn(
            f"Presidio unavailable ({exc}); falling back to pattern-only masking, "
            "which does not catch names",
            stacklevel=2,
        )
        return RegexMasker()
