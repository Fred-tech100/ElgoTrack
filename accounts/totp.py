"""RFC 6238 time-based one-time passwords (SHA-1, 6 digits, 30 s) with replay protection."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS
    return f"{value:0{DIGITS}d}"


def verify(secret, code, last_step=None, window=1, now=None):
    """Return the matching time step, or None. Steps at or before ``last_step`` are rejected (replay)."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    current = int((time.time() if now is None else now) // STEP)
    for step in range(current - window, current + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def provisioning_uri(secret, email, issuer="ElgonNova"):
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"


def new_recovery_codes(n=8):
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(n)]
