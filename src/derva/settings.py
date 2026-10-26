"""Runtime settings from environment variables, and the pipeline factory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from derva.audit import AuditStore
from derva.config import CONFIG_DIR, load_questions, load_thresholds
from derva.deciders.chain import FallbackChain, Link
from derva.deciders.llm import LLMDecider
from derva.deciders.rules import RulesDecider
from derva.deciders.systemone import LiteLLMDecisionDecider
from derva.masking import Masker, PresidioMasker, RegexMasker
from derva.pipeline import Pipeline
from derva.resolution import InMemoryDirectory

ROOT = CONFIG_DIR.parent


class Settings(BaseModel):
    crm_path: Path = ROOT / "data" / "fixtures" / "crm_seed.json"
    audit_url: str = "sqlite:///derva_audit.db"
    questions_path: Path = CONFIG_DIR / "questions.yaml"
    thresholds_path: Path = CONFIG_DIR / "thresholds.yaml"

    # The decision model, reached through LiteLLM (typesafe/..., cloudflare/...).
    decider_model: str | None = None
    decider_api_base: str | None = None
    decider_api_key: str | None = None
    decider_timeout_s: float = 2.0

    # Optional second link: a small LLM forced into typed answers.
    fallback_llm: str | None = None
    fallback_timeout_s: float = 6.0

    masker: Literal["presidio", "regex"] = "presidio"
    # en_core_web_lg in the container image; the small model over-masks a little.
    spacy_model: str = "en_core_web_sm"

    # The single model callers are allowed to request, and the tiers behind it.
    entry_model: str = "derva"
    small_model: str = "derva-small"
    frontier_model: str = "derva-frontier"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        values: dict[str, object] = {}
        mapping = {
            "DERVA_CRM_PATH": "crm_path",
            "DERVA_AUDIT_URL": "audit_url",
            "DERVA_QUESTIONS_PATH": "questions_path",
            "DERVA_THRESHOLDS_PATH": "thresholds_path",
            "DERVA_DECIDER_MODEL": "decider_model",
            "DERVA_DECIDER_API_BASE": "decider_api_base",
            "DERVA_DECIDER_API_KEY": "decider_api_key",
            "DERVA_DECIDER_TIMEOUT_S": "decider_timeout_s",
            "DERVA_FALLBACK_LLM": "fallback_llm",
            "DERVA_FALLBACK_TIMEOUT_S": "fallback_timeout_s",
            "DERVA_MASKER": "masker",
            "DERVA_SPACY_MODEL": "spacy_model",
            "DERVA_ENTRY_MODEL": "entry_model",
            "DERVA_SMALL_MODEL": "small_model",
            "DERVA_FRONTIER_MODEL": "frontier_model",
        }
        for var, field in mapping.items():
            if env.get(var):
                values[field] = env[var]
        return cls.model_validate(values)


def build_masker(settings: Settings, firm_domains: list[str]) -> Masker:
    if settings.masker == "presidio":
        return PresidioMasker(model_name=settings.spacy_model, firm_domains=firm_domains)
    return RegexMasker(firm_domains)


def build_pipeline(settings: Settings) -> Pipeline:
    links: list[Link] = []
    if settings.decider_model:
        links.append(
            Link(
                LiteLLMDecisionDecider(
                    model=settings.decider_model,
                    api_base=settings.decider_api_base,
                    api_key=settings.decider_api_key,
                    timeout_s=settings.decider_timeout_s,
                ),
                timeout_s=settings.decider_timeout_s,
            )
        )
    if settings.fallback_llm:
        links.append(
            Link(LLMDecider(model=settings.fallback_llm), timeout_s=settings.fallback_timeout_s)
        )
    links.append(Link(RulesDecider(), timeout_s=1.0))

    thresholds = load_thresholds(settings.thresholds_path)
    return Pipeline(
        directory=InMemoryDirectory.from_json(settings.crm_path),
        masker=build_masker(settings, thresholds.firm.domains),
        decider=FallbackChain(links),
        questions=load_questions(settings.questions_path),
        thresholds=thresholds,
        audit=AuditStore(settings.audit_url),
    )
