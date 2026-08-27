"""Password hashing and JWT issuance/verification.

Access tokens are short-lived and never persisted. Refresh tokens are opaque
random strings handed to the client; only their SHA-256 hash is stored, so a
leaked database dump can't be replayed as a valid refresh token.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
_JWT_ALGORITHM = "HS256"
_MIN_JWT_SECRET_LENGTH = 32

_hasher = PasswordHasher()

# Used to pay the same Argon2 cost when a login's email doesn't exist, so
# that lookup failures and password mismatches take the same amount of time.
DUMMY_PASSWORD_HASH = _hasher.hash("dummy-password-for-timing-safety")


def _jwt_secret() -> str:
    import os

    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is not set")
    if len(secret) < _MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET must be at least {_MIN_JWT_SECRET_LENGTH} characters long"
        )
    return secret


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)``."""
    now = datetime.now(timezone.utc)
    expires_at = now + ACCESS_TOKEN_TTL
    payload = {"sub": str(user_id), "iat": now, "exp": expires_at}
    token = jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALGORITHM)
    return token, int(ACCESS_TOKEN_TTL.total_seconds())


def decode_access_token(token: str) -> uuid.UUID:
    """Raise ``jwt.PyJWTError`` on any invalid/expired/tampered token."""
    payload = jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALGORITHM])
    return uuid.UUID(payload["sub"])


def generate_refresh_token() -> tuple[str, str, datetime]:
    """Return ``(raw_token, token_hash, expires_at)``. Only the hash is stored."""
    raw = secrets.token_urlsafe(48)
    token_hash = hash_refresh_token(raw)
    expires_at = datetime.now(timezone.utc) + REFRESH_TOKEN_TTL
    return raw, token_hash, expires_at


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
