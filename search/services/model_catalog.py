"""Discover the chat models each configured AI provider currently offers."""

import os

import requests

from . import anthropic_provider
from .ai_wrapper import PROVIDERS, api_keys, openai_client

# Catalogues also contain models unsuited to analysing a legal search query.
UNSUITABLE_MODEL_TERMS = (
    "antigravity", "audio", "codestral", "computer-use", "customtools", "dall-e",
    "deep-research", "embed", "guard", "image", "imagine", "labs-", "lyria", "mistral-code",
    "moderation", "nano-banana", "ocr", "orpheus", "realtime", "rerank", "robotics", "sora",
    "transcribe", "tts", "vibe-cli", "voxtral", "whisper",
)

# Models that answered a real request on 2026-11-06; refresh with `manage.py check_models`.
# Override with <NAME>_AVAILABLE_MODELS. A provider with no list shows every suitable model.
TESTED_MODELS = {
    "gemini": (
        "gemini-flash-lite-latest",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
    ),
}


class ModelCatalogError(Exception):
    """Raised when a provider's model catalogue cannot be read."""


def list_providers() -> list[dict]:
    return [
        {"id": provider_id, "label": provider.label, "configured": bool(api_keys(provider_id))}
        for provider_id, provider in PROVIDERS.items()
    ]


def list_models(provider: str, only_tested: bool = True) -> list[dict]:
    """List a provider's suitable models using the first of its keys that works.

    Reading the live catalogue means retired models drop out on their own.
    """
    keys = api_keys(provider)
    if not keys:
        raise ModelCatalogError(f"{PROVIDERS[provider].label} is not configured.")

    for key in keys:
        try:
            models = _fetch_models(provider, key)
        except Exception:
            continue
        tested = _tested_models(provider) if only_tested else None
        usable = [
            model for model in models
            if not _is_unsuitable(model["id"]) and (tested is None or model["id"] in tested)
        ]
        return sorted(
            ({**model, "provider": provider} for model in usable),
            key=lambda model: model["id"],
        )
    raise ModelCatalogError(f"The {PROVIDERS[provider].label} model catalogue is unavailable.")


def _is_unsuitable(model_id: str) -> bool:
    return any(term in model_id.lower() for term in UNSUITABLE_MODEL_TERMS)


def _tested_models(provider: str) -> set[str] | None:
    configured = os.getenv(f"{provider.upper()}_AVAILABLE_MODELS", "")
    if configured.strip():
        return {model.strip() for model in configured.split(",") if model.strip()}
    return set(TESTED_MODELS[provider]) if provider in TESTED_MODELS else None


def _fetch_models(provider: str, api_key: str) -> list[dict]:
    if provider == "gemini":
        return _fetch_gemini_models(api_key)
    if provider == "anthropic":
        return anthropic_provider.list_models(api_key)
    return [{"id": model.id, "label": model.id} for model in openai_client(provider, api_key, 15).models.list()]


def _fetch_gemini_models(api_key: str) -> list[dict]:
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key},
        params={"pageSize": 1000},
        timeout=15,
    )
    response.raise_for_status()
    return [
        {
            "id": model["name"].removeprefix("models/"),
            "label": model.get("displayName") or model["name"].removeprefix("models/"),
        }
        for model in response.json().get("models", [])
        if model.get("name") and "generateContent" in model.get("supportedGenerationMethods", [])
    ]
