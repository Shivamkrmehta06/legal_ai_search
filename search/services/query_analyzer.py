import logging
import os
import re
from typing import Optional

from pydantic import BaseModel

from .ai_wrapper import PROVIDERS, AIWrapperError, build_model_chain, complete_structured

logger = logging.getLogger(__name__)


class LegalQuery(BaseModel):
    intent: str
    is_legal_query: bool
    request_type: str = "judgment_search"
    case_name: Optional[str] = None
    requested_info: list[str] = []
    domain: Optional[str] = None
    court: Optional[str] = None
    act: Optional[str] = None
    section: Optional[str] = None
    legal_issues: list[str] = []
    keywords: list[str] = []
    language: str
    clarifying_question: Optional[str] = None


SYSTEM_INSTRUCTIONS = """
You are a legal search query understanding assistant for an Indian legal knowledge
management system. Analyze the query; do not answer it and do not invent legal
facts, cases, statutes, courts, or sections.

Return the requested structured fields. Use null for unknown scalar fields and
empty lists for unknown legal_issues or keywords. Choose intent from
case_search, document_search, statute_search, or general_search. Set
is_legal_query to false when appropriate.

This service finds previous judgments related to Acts and sections. Set request_type:
- judgment_search: the user wants judgments, in any words or language, such as
  orders, rulings, decisions, precedents, case law, faisla, or just an Act or section.
- legal_question: a legal question asking for advice or an explanation rather than judgments.
- out_of_scope: nothing to do with law.

Set case_name only when the user names a specific case, such as "ABC Traders v XYZ".
Set requested_info to what the user wants to know, from: judges, parties, summary, court,
judgment_date, case_number, citation, outcome, bench, statutes (Acts and sections in
the case), issues, domains, propositions (holdings and reasoning), cases_cited, cited_by,
citation_aliases, legal_documents, case_documents, versions, metadata_versions, passages.
List only what the user explicitly asked for, and leave it empty when the user only wants
a list of judgments.

Set act to the Act's full official English name, however the user writes it: in any
language or script, abbreviated or misspelt. For example "NI Act", "एनआई एक्ट", "قانون ني"
and "این آئی ایکٹ" all mean "Negotiable Instruments Act, 1881". Set section to the section
number alone, in Western digits. Never put the section in act: "NI Act 138" means act
"Negotiable Instruments Act, 1881" and section "138".

Return language as a lowercase ISO 639-1
code such as en or hi. Ask one clarifying question only when the query is too
ambiguous to search usefully.
"""

GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {
            "type": "STRING",
            "enum": ["case_search", "document_search", "statute_search", "general_search"],
        },
        "is_legal_query": {"type": "BOOLEAN"},
        "request_type": {
            "type": "STRING",
            "enum": ["judgment_search", "legal_question", "out_of_scope"],
        },
        "case_name": {"type": "STRING", "nullable": True},
        "requested_info": {"type": "ARRAY", "items": {"type": "STRING"}},
        "domain": {"type": "STRING", "nullable": True},
        "court": {"type": "STRING", "nullable": True},
        "act": {"type": "STRING", "nullable": True},
        "section": {"type": "STRING", "nullable": True},
        "legal_issues": {"type": "ARRAY", "items": {"type": "STRING"}},
        "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "language": {"type": "STRING"},
        "clarifying_question": {"type": "STRING", "nullable": True},
    },
    "required": ["intent", "is_legal_query", "request_type", "language"],
}


def analyze_query(
    query: str,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> tuple[dict, str]:
    """Return validated metadata from the first working model, else the local fallback.

    Neither provider outages nor a settings mistake may make search unusable.
    """
    provider = (provider_override or os.getenv("AI_PROVIDER", "auto")).lower()
    if provider != "auto" and provider not in PROVIDERS:
        logger.error("AI_PROVIDER must be auto or one of: %s.", ", ".join(PROVIDERS))
        provider = "auto"

    try:
        chain = build_model_chain(None if provider == "auto" else provider, model_override)
        result, target = complete_structured(chain, SYSTEM_INSTRUCTIONS, query, LegalQuery, GEMINI_SCHEMA)
    except (AIWrapperError, ValueError) as exc:
        logger.warning("Falling back to heuristic query analysis: %s", exc)
        return _fallback_analysis(query).model_dump(), "heuristic_fallback"
    return result.model_dump(), target.label


def _fallback_analysis(query: str) -> LegalQuery:
    """Conservative local analysis used only when no LLM key is configured or on failure."""
    # Whole words only, so that "contract" doesn't count as "act".
    words = set(re.findall(r"[a-z]+", query.lower()))
    intent = "general_search"
    if words & {
        "case", "cases", "judgment", "judgments", "judgement", "judgements", "ruling", "rulings",
        "order", "orders", "decision", "decisions", "precedent", "precedents", "faisla", "faisle",
    }:
        intent = "case_search"
    elif words & {"act", "acts", "section", "sections", "statute", "statutes"}:
        intent = "statute_search"
    elif words & {"document", "documents", "notification", "notifications", "circular", "circulars"}:
        intent = "document_search"

    return LegalQuery(
        intent=intent,
        is_legal_query=True,
        domain=None,
        court=None,
        act=None,
        section=None,
        legal_issues=[],
        keywords=[query],
        language="en",
        clarifying_question=None,
    )
