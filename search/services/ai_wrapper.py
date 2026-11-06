"""Send structured requests through an ordered chain of AI models and API keys."""

from dataclasses import dataclass
import json
import logging
import os
import time

from openai import OpenAI
from pydantic import BaseModel
import requests

from . import anthropic_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    label: str
    # Set for providers that accept OpenAI-style requests. Adding another such
    # provider only needs a new entry here and its <NAME>_API_KEY.
    openai_base_url: str | None = None
    # False for providers offering JSON mode but no schema-constrained output.
    supports_json_schema: bool = True


# Dictionary order is the default order backups are tried in.
PROVIDERS = {
    "gemini": Provider("Google Gemini"),
    "groq": Provider("Groq", "https://api.groq.com/openai/v1"),
    "cerebras": Provider("Cerebras", "https://api.cerebras.ai/v1"),
    "mistral": Provider("Mistral", "https://api.mistral.ai/v1"),
    "cohere": Provider("Cohere", "https://api.cohere.ai/compatibility/v1"),
    "deepseek": Provider("DeepSeek", "https://api.deepseek.com", supports_json_schema=False),
    "xai": Provider("xAI Grok", "https://api.x.ai/v1"),
    "openai": Provider("OpenAI", "https://api.openai.com/v1"),
    "anthropic": Provider("Anthropic Claude"),
}

DEFAULT_MODELS = {
    "gemini": (
        # Verified on 2026-11-06 with this project's Gemini account for the
        # JSON-schema request used by legal query analysis.
        "gemini-flash-lite-latest",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    ),
    "anthropic": ("claude-opus-5-5",),
}

# Statuses meaning this key is out of quota or credit, or was rejected.
KEY_PROBLEM_STATUSES = {401, 402, 403, 429}


class AIWrapperError(Exception):
    """Raised when no model in the chain returned a valid response."""


class ProviderHTTPError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Status {status_code}: {detail[:300]}")
        self.status_code = status_code


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def api_keys(provider: str) -> list[str]:
    """Return a provider's keys: <NAME>_API_KEY, then those in <NAME>_API_KEYS.

    AI_DISABLED_PROVIDERS switches a provider off, for example while it has no credit.
    """
    disabled = {name.strip().lower() for name in os.getenv("AI_DISABLED_PROVIDERS", "").split(",")}
    if provider in disabled:
        return []
    prefix = provider.upper()
    candidates = [os.getenv(f"{prefix}_API_KEY", "")]
    if provider == "gemini":
        candidates.append(os.getenv("GOOGLE_API_KEY", ""))
    candidates += os.getenv(f"{prefix}_API_KEYS", "").split(",")
    return list(dict.fromkeys(key.strip() for key in candidates if key.strip()))


def build_model_chain(provider: str | None = None, model: str | None = None) -> list[ModelTarget]:
    """Return models to try in order, skipping providers without an API key.

    A chosen provider or model moves to the front; the rest stay as backups.
    Raises ValueError when AI_MODEL_CHAIN is malformed.
    """
    chain = _configured_chain()
    if provider and model:
        chain.insert(0, ModelTarget(provider, model))
    elif provider:
        chain.sort(key=lambda target: target.provider != provider)
    return [target for target in dict.fromkeys(chain) if api_keys(target.provider)]


def complete_structured(
    chain: list[ModelTarget],
    system: str,
    prompt: str,
    response_model: type[BaseModel],
    gemini_schema: dict,
) -> tuple[BaseModel, ModelTarget]:
    """Return the first valid response from the chain and the model that gave it.

    A key problem moves to the provider's next key; any other failure moves to
    the next model, because another key would not help.
    """
    deadline = time.monotonic() + float(os.getenv("AI_TOTAL_TIMEOUT_SECONDS") or "45")
    for target in chain:
        for position, key in enumerate(api_keys(target.provider), start=1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AIWrapperError("The AI model chain ran out of time.")
            try:
                return _call(target, key, system, prompt, response_model, gemini_schema, remaining), target
            except Exception as exc:
                # One failing model must not stop the others from being tried.
                if _is_key_problem(exc):
                    # Logs name the key's position only, so someone can replace it safely.
                    logger.warning("%s API key #%d is exhausted or was rejected: %s", target.provider, position, exc)
                    continue
                logger.warning("AI model %s failed: %s", target.label, exc)
                break
    raise AIWrapperError("No AI model in the chain returned a valid response.")


def openai_client(provider: str, api_key: str, timeout: float) -> OpenAI:
    # The wrapper moves to the next key or model itself, so the client must not retry.
    return OpenAI(
        api_key=api_key,
        base_url=PROVIDERS[provider].openai_base_url,
        timeout=timeout,
        max_retries=0,
    )


def _configured_chain() -> list[ModelTarget]:
    raw_chain = os.getenv("AI_MODEL_CHAIN", "").strip()
    if raw_chain:
        return [_parse_target(entry.strip()) for entry in raw_chain.split(",") if entry.strip()]

    chain = []
    for provider in PROVIDERS:
        configured = os.getenv(f"{provider.upper()}_MODEL")
        models = [configured] if configured else []
        models += DEFAULT_MODELS.get(provider, ())
        chain += [ModelTarget(provider, model) for model in models]
    return chain


def _parse_target(entry: str) -> ModelTarget:
    # Only the first colon separates them: model ids can contain colons.
    provider, _, model = entry.partition(":")
    if provider not in PROVIDERS or not model:
        raise ValueError(f"AI_MODEL_CHAIN entry must look like provider:model, got {entry!r}.")
    return ModelTarget(provider, model)


def _is_key_problem(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) in KEY_PROBLEM_STATUSES:
        return True
    # Some providers report these as a plain 400.
    message = str(exc).lower()
    return getattr(exc, "status_code", None) == 400 and ("api key" in message or "credit balance" in message)


def _call(
    target: ModelTarget,
    api_key: str,
    system: str,
    prompt: str,
    response_model: type[BaseModel],
    gemini_schema: dict,
    remaining: float,
) -> BaseModel:
    timeout = min(float(os.getenv("AI_MODEL_TIMEOUT_SECONDS") or "15"), remaining)
    if target.provider == "anthropic":
        return anthropic_provider.complete_structured(target.model, system, prompt, response_model, api_key, timeout)
    if target.provider == "gemini":
        text = _call_gemini(target.model, system, prompt, gemini_schema, api_key, timeout)
    else:
        text = _call_openai_compatible(target, system, prompt, response_model, api_key, timeout)
    return response_model.model_validate_json(text)


def _call_gemini(model: str, system: str, prompt: str, schema: dict, api_key: str, timeout: float) -> str:
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        },
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        timeout=timeout,
    )
    if not response.ok:
        raise ProviderHTTPError(response.status_code, response.text)
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_compatible(
    target: ModelTarget,
    system: str,
    prompt: str,
    response_model: type[BaseModel],
    api_key: str,
    timeout: float,
) -> str:
    schema = _strict_schema(response_model)
    if PROVIDERS[target.provider].supports_json_schema:
        response_format = {"type": "json_schema", "json_schema": {
            "name": response_model.__name__.lower(), "strict": True, "schema": schema,
        }}
    else:
        # JSON mode doesn't enforce a schema, so the prompt has to describe it.
        response_format = {"type": "json_object"}
        system = f"{system}\nReturn only a JSON object matching this JSON schema:\n{json.dumps(schema)}"
    response = openai_client(target.provider, api_key, timeout).chat.completions.create(
        model=target.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format=response_format,
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        # Reasoning models can return thinking blocks alongside the answer's text.
        return "".join(block["text"] for block in content if block.get("type") == "text")
    return content


def _strict_schema(response_model: type[BaseModel]) -> dict:
    """Strict mode needs every field required; unknown values are returned as null."""
    schema = response_model.model_json_schema()
    return {**schema, "additionalProperties": False, "required": list(schema["properties"])}
