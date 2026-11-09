"""Encrypt API keys so the database never holds them as readable text."""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _cipher() -> Fernet:
    # Changing DJANGO_SECRET_KEY makes stored keys unreadable until they are imported again.
    digest = hashlib.sha256(f"key_management:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(api_key: str) -> str:
    return _cipher().encrypt(api_key.encode()).decode()


def decrypt_key(encrypted_key: str) -> str:
    return _cipher().decrypt(encrypted_key.encode()).decode()


def fingerprint_key(api_key: str) -> str:
    """Identify a key without storing it, so the same key isn't saved twice."""
    return hashlib.sha256(api_key.encode()).hexdigest()
