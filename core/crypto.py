"""Symmetric encryption for secrets stored in the database (e.g. TOTP seeds), keyed from SECRET_KEY."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet(purpose):
    digest = hashlib.sha256(f"{purpose}:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text, purpose="field"):
    return _fernet(purpose).encrypt(text.encode()).decode()


def decrypt(token, purpose="field"):
    try:
        return _fernet(purpose).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def keyed_hash(value, purpose="hash"):
    return hashlib.sha256(f"{purpose}:{settings.SECRET_KEY}:{value}".encode()).hexdigest()
