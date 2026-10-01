"""Password hashing and signed session cookies (stdlib only)."""
import hashlib
import hmac
import secrets
import time

from app.core.config import settings

SESSION_COOKIE = "session_user"
SESSION_MAX_AGE = 30 * 24 * 3600  # seconds
MIN_PASSWORD_LENGTH = 8

_HASH_PREFIX = "scrypt$"
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1

_secret: bytes | None = None


# ── Passwords ─────────────────────────────────────────

def is_hashed(stored: str) -> bool:
    return stored.startswith(_HASH_PREFIX)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"{_HASH_PREFIX}{_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except ValueError:
        return False


# ── Sessions ──────────────────────────────────────────

def _get_secret() -> bytes:
    """Returns the signing key, generating and persisting one on first use."""
    global _secret
    if _secret is None:
        if settings.secret_key:
            _secret = settings.secret_key.encode()
        else:
            key_path = settings.data_dir / ".secret_key"
            if not key_path.exists():
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key_path.write_text(secrets.token_hex(32))
            _secret = key_path.read_text().strip().encode()
    return _secret


def _sign(payload: str) -> str:
    return hmac.new(_get_secret(), payload.encode(), hashlib.sha256).hexdigest()


def create_session(username: str) -> str:
    """Returns a signed 'username.expiry.signature' cookie value."""
    payload = f"{username}.{int(time.time()) + SESSION_MAX_AGE}"
    return f"{payload}.{_sign(payload)}"


def read_session(token: str | None) -> str | None:
    """Returns the username from a valid, unexpired session cookie, else None."""
    if not token:
        return None
    try:
        username, expiry, signature = token.split(".")
        if not hmac.compare_digest(signature, _sign(f"{username}.{expiry}")):
            return None
        if int(expiry) < time.time():
            return None
    except ValueError:
        return None
    return username
