"""Call Claude directly through the official Anthropic SDK."""

import anthropic
from pydantic import BaseModel


def complete_structured(
    model: str,
    system: str,
    prompt: str,
    response_model: type[BaseModel],
    api_key: str,
    timeout: float,
) -> BaseModel:
    # The AI wrapper moves to the next key or model itself, so the SDK must not retry.
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_format=response_model,
    )
    if response.parsed_output is None:
        raise ValueError(f"Claude returned no structured output (stop_reason={response.stop_reason}).")
    return response.parsed_output


def list_models(api_key: str) -> list[dict]:
    client = anthropic.Anthropic(api_key=api_key, timeout=15, max_retries=0)
    return [{"id": model.id, "label": model.display_name} for model in client.models.list()]
