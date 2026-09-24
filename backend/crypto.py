# ============================================================
# SECRETS AT REST AND ONE-TIME PASSWORDS
# ============================================================
# encrypt() / decrypt(): Fernet (AES-128-CBC + HMAC-SHA256) with a key derived
# from SECRET_KEY. Used for MFA secrets and organizations' own messaging
# provider credentials. Production refuses to start without SECRET_KEY; in
# development a key is derived from the database URL and a warning is logged,
# so values encrypted in development are not portable.
#
# TOTP (RFC 6238, SHA-1, 30 s, 6 digits): compatible with Google
# Authenticator, Microsoft Authenticator, Authy, 1Password, etc.

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    material = settings.secret_key or ("dev-only:" + settings.database_url)
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as error:
        raise ValueError("Stored secret cannot be decrypted (SECRET_KEY changed?)") from error


# ------------------------------------------------------------ TOTP

STEP = 30
DIGITS = 6


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def totp_now(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // STEP))


def verify_totp(secret: str, code: str, last_counter: int | None, window: int = 1) -> int | None:
    """Return the matched time step (to store as last used), or None. A step
    at or before last_counter is rejected (no replay)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    now = int(time.time() // STEP)
    for counter in range(now - window, now + window + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(_code(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "PharmaStock") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def new_recovery_codes(count: int = 10) -> list[str]:
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(count)]


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
