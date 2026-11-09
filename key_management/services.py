"""Read stored API keys, and import them from environment variables."""

import logging
import os

from cryptography.fernet import InvalidToken
from django.db import DatabaseError

from .crypto import fingerprint_key
from .models import KeyMaster, ModelProvider

logger = logging.getLogger(__name__)

# No longer used by the app, so imported switched off.
RETIRED_PROVIDERS = {"openrouter": "OpenRouter"}


def stored_api_keys(slug: str) -> list[str] | None:
    """Return a provider's active keys, or None if the table can't answer for it.

    None means "use environment variables": the provider isn't in the table, the
    database isn't available (as on Vercel), or none of its keys can be decrypted.
    """
    try:
        provider = ModelProvider.objects.filter(slug=slug).first()
        if provider is None:
            return None
        if not provider.is_active:
            return []
        rows = list(provider.keys.filter(is_active=True))
    except DatabaseError:
        return None

    keys = []
    for row in rows:
        try:
            keys.append(row.get_key())
        except InvalidToken:
            logger.warning("Could not decrypt %s key %s.", provider.slug, row.masked_key)
    return None if rows and not keys else keys


def environment_keys(slug: str) -> list[str]:
    """Return a provider's keys from <NAME>_API_KEY, then those in <NAME>_API_KEYS."""
    prefix = slug.upper()
    candidates = [os.getenv(f"{prefix}_API_KEY", "")]
    if slug == "gemini":
        candidates.append(os.getenv("GOOGLE_API_KEY", ""))
    candidates += os.getenv(f"{prefix}_API_KEYS", "").split(",")
    return list(dict.fromkeys(key.strip() for key in candidates if key.strip()))


def import_environment_keys(provider_names: dict[str, str], disabled: set[str]) -> list[dict]:
    """Save every environment key under its provider. Keys already saved are skipped.

    An existing provider's settings are kept, so changes made in the admin survive.
    """
    summary = []
    for slug, name in {**provider_names, **RETIRED_PROVIDERS}.items():
        keys = environment_keys(slug)
        if not keys:
            continue
        provider, _ = ModelProvider.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "is_active": slug not in disabled and slug not in RETIRED_PROVIDERS},
        )
        added = 0
        for priority, key in enumerate(keys):
            if KeyMaster.objects.filter(key_fingerprint=fingerprint_key(key)).exists():
                continue
            entry = KeyMaster(provider=provider, priority=priority)
            entry.set_key(key)
            entry.save()
            added += 1
        summary.append({"provider": slug, "found": len(keys), "added": added, "active": provider.is_active})
    return summary
