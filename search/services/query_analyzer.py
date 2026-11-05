import json
import logging
import os
import re
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel
import requests

logger = logging.getLogger(__name__)


class LegalQuery(BaseModel):
    intent: str
    is_legal_query: bool
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
is_legal_query to false when appropriate. Ask one clarifying question only when
the query is too ambiguous to search usefully.
"""

GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {
            "type": "STRING",
            "enum": ["case_search", "document_search", "statute_search", "general_search"],
        },
        "is_legal_query": {"type": "BOOLEAN"},
        "domain": {"type": "STRING", "nullable": True},
        "court": {"type": "STRING", "nullable": True},
        "act": {"type": "STRING", "nullable": True},
        "section": {"type": "STRING", "nullable": True},
        "legal_issues": {"type": "ARRAY", "items": {"type": "STRING"}},
        "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "language": {"type": "STRING"},
        "clarifying_question": {"type": "STRING", "nullable": True},
    },
    "required": ["intent", "is_legal_query", "language"],
}


class QueryAnalysisError(Exception):
    """Raised when configured LLM query analysis cannot be completed."""


def analyze_query(
    query: str,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> tuple[dict, str]:
    """Return validated metadata, preferring cloud providers with configured keys."""
    provider = (provider_override or os.getenv("AI_PROVIDER", "auto")).lower()
    if provider not in {"auto", "gemini", "openrouter"}:
        raise QueryAnalysisError("AI_PROVIDER must be auto, gemini, or openrouter.")

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if provider in {"auto", "gemini"} and gemini_key:
        try:
            return _analyze_with_gemini(query, gemini_key, model_override or os.getenv("GEMINI_MODEL"))
        except QueryAnalysisError as exc:
            # Provider outages must not make the legal-search endpoint unusable.
            # In auto mode another configured provider may still be available.
            logger.warning("Gemini query analysis unavailable: %s", exc)
            if provider == "gemini":
                # Explicit selection falls through to another configured cloud
                # provider before the local fallback, avoiding provider 502s.
                logger.info("Trying another configured cloud provider after Gemini failure.")

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if provider in {"auto", "openrouter"} and openrouter_key:
        try:
            return _analyze_with_openrouter(query, openrouter_key, model_override or os.getenv("OPENROUTER_MODEL"))
        except QueryAnalysisError as exc:
            logger.warning("OpenRouter query analysis unavailable: %s", exc)

    return _fallback_analysis(query).model_dump(), "heuristic_fallback"


def _analyze_with_gemini(query: str, api_key: str, model_override: str | None = None) -> tuple[dict, str]:
    """Analyze query via Google Gemini API structured output with auto-fallback."""
    configured_model = model_override or os.getenv("GEMINI_MODEL", "gemini-3.0-flash")
    candidates = [
        configured_model,
        # Verified on 2026-11-05 with this project's Gemini account for the
        # JSON-schema request used by legal query analysis.
        "gemini-flash-lite-latest",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
    ]
    # Remove duplicates preserving order
    models_to_try = list(dict.fromkeys(candidates))

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_INSTRUCTIONS}]
        },
        "contents": [
            {
                "parts": [{"text": query}]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_SCHEMA,
        },
    }

    last_error_detail = None
    timeout_seconds = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "40"))

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                timeout=timeout_seconds,
            )
            if not response.ok:
                try:
                    err_json = response.json()
                    err_msg = err_json.get("error", {}).get("message", response.text)
                except Exception:
                    err_msg = response.text

                last_error_detail = f"Status {response.status_code} on {model}: {err_msg}"

                # If Google mentions another model in the error message, add it to try
                suggested_models = re.findall(r"models/([a-zA-Z0-9\.\-_]+)", err_msg)
                for sm in suggested_models:
                    if sm not in models_to_try:
                        models_to_try.append(sm)

                err_lower = err_msg.lower()
                if (
                    response.status_code == 404
                    or response.status_code == 429
                    or response.status_code >= 500
                    or "not found" in err_lower
                    or "no longer available" in err_lower
                    or "not supported" in err_lower
                    or "not available" in err_lower
                    or "high demand" in err_lower
                ):
                    continue

                raise QueryAnalysisError(f"Gemini API error ({response.status_code}): {err_msg}")

            data = response.json()
            text_content = data["candidates"][0]["content"]["parts"][0]["text"]
            result = LegalQuery.model_validate_json(text_content)
            return result.model_dump(), "gemini"

        except requests.Timeout:
            logger.warning("Gemini API call timed out on model %s", model)
            last_error_detail = f"Request timed out on {model} after {timeout_seconds}s"
            continue
        except QueryAnalysisError:
            raise
        except Exception as exc:
            last_error_detail = str(exc)
            continue

    # If all models timed out or failed, fall back to heuristic analysis rather than throwing 502
    logger.warning("All Gemini models failed (%s); falling back to heuristic.", last_error_detail)
    fallback = _fallback_analysis(query)
    return fallback.model_dump(), "heuristic_fallback"


def _analyze_with_openrouter(query: str, api_key: str, model: str | None) -> tuple[dict, str]:
    if not model:
        raise QueryAnalysisError("Choose an OpenRouter model from GET /api/models/?provider=openrouter.")
    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={"HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
                         "X-Title": os.getenv("OPENROUTER_APP_NAME", "Legal Search")},
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": query},
            ],
            response_format={"type": "json_schema", "json_schema": {
                "name": "legal_query", "strict": True,
                "schema": LegalQuery.model_json_schema(),
            }},
        )
        result = LegalQuery.model_validate_json(response.choices[0].message.content)
    except Exception as exc:
        raise QueryAnalysisError("The selected OpenRouter model failed structured query analysis.") from exc
    return result.model_dump(), f"openrouter:{model}"


def _fallback_analysis(query: str) -> LegalQuery:
    """Conservative local analysis used only when no LLM key is configured or on failure."""
    normalized = query.lower()
    intent = "general_search"
    if any(term in normalized for term in ("case", "judgment", "judgement", "ruling")):
        intent = "case_search"
    elif any(term in normalized for term in ("act", "section", "statute", "bare act")):
        intent = "statute_search"
    elif any(term in normalized for term in ("document", "notification", "circular")):
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
