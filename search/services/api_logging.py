"""Show API requests and responses in the terminal. Secrets are never included."""

import json
import logging
import os

logger = logging.getLogger("api_calls")


def log_api_call(name: str, request, response=None, *, status=None, seconds=None, error=None, secrets=()) -> None:
    """Log one call. Pass values such as API keys in `secrets` to hide them if an error repeats them."""
    if not logger.isEnabledFor(logging.INFO):
        return
    outcome = " ".join(
        part for part in (
            str(status) if status is not None else None,
            f"{seconds:.2f}s" if seconds is not None else None,
            "FAILED" if error else None,
        ) if part
    )
    lines = [f"--- {name} {outcome}".rstrip(), f"Request: {_format(request, secrets)}"]
    if error:
        lines.append(f"Error: {_format(str(error), secrets)}")
    else:
        lines.append(f"Response: {_format(response, secrets)}")
    logger.info("\n".join(lines))


def _format(value, secrets) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[hidden]")
    limit = int(os.getenv("API_LOG_MAX_CHARS") or "3000")
    if limit and len(text) > limit:
        return f"{text[:limit]}\n... [{len(text) - limit} more characters; set API_LOG_MAX_CHARS=0 to show everything]"
    return text
