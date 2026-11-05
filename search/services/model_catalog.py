"""Discover models exposed by configured cloud AI providers."""

import os

import requests


class ModelCatalogError(Exception):
    """Raised when the local Ollama catalogue cannot be reached."""


VERIFIED_GEMINI_STRUCTURED_MODELS = {
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
}


def list_gemini_models() -> list[dict]:
    """List models enabled for this Gemini API key from Google's live catalogue."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ModelCatalogError("Gemini is not configured.")

    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": api_key},
            params={"pageSize": 1000},
            timeout=15,
        )
        response.raise_for_status()
        models = response.json().get("models", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise ModelCatalogError("The Gemini model catalogue is unavailable.") from exc

    result = []
    for model in models:
        if not isinstance(model, dict) or "generateContent" not in model.get("supportedGenerationMethods", []):
            continue
        model_id = model.get("name", "").removeprefix("models/")
        if not model_id:
            continue
        result.append(
            {
                "id": model_id,
                "label": model.get("displayName") or model_id,
                "provider": "gemini",
                "is_free_local": False,
                "is_legal_search_compatible": model_id in VERIFIED_GEMINI_STRUCTURED_MODELS,
            }
        )
    return result


def list_openrouter_models() -> list[dict]:
    """List OpenRouter models that advertise JSON structured output."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ModelCatalogError("OpenRouter is not configured. Set OPENROUTER_API_KEY.")
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        response.raise_for_status()
        models = response.json().get("data", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise ModelCatalogError("The OpenRouter model catalogue is unavailable.") from exc

    return [
        {
            "id": model["id"],
            "label": model.get("name") or model["id"],
            "provider": "openrouter",
            "is_legal_search_compatible": "structured_outputs" in model.get("supported_parameters", []),
            "pricing": model.get("pricing"),
        }
        for model in models
        if isinstance(model, dict) and model.get("id")
    ]
