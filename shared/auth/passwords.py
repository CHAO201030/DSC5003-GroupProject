"""Password hashing.

Uses bcrypt (via the ``bcrypt`` package) with a SHA-256 pre-hash so passwords
are not limited to bcrypt's 72-byte input.  A PBKDF2-SHA256 implementation is
kept as a portable fallback for environments without the bcrypt wheel.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from shared.errors import ValidationFailed

try:
    import bcrypt as _bcrypt
except Exception:  # pragma: no cover - fallback path
    _bcrypt = None

_BCRYPT_PREFIX = "bcrypt_sha256$"
_PBKDF2_PREFIX = "pbkdf2_sha256$"
_PBKDF2_ITERATIONS = 600_000


def validate_password(password: str) -> None:
    if not isinstance(password, str):
        raise ValidationFailed("密码格式不合法")
    if len(password) < 6:
        raise ValidationFailed("密码至少需要 6 个字符")
    if len(password) > 256:
        raise ValidationFailed("密码不能超过 256 个字符")


def _prehash(password: str) -> bytes:
    # base64 keeps the result at 44 bytes (< bcrypt's 72-byte limit).
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str, rounds: int = 12) -> str:
    validate_password(password)
    if _bcrypt is not None:
        hashed = _bcrypt.hashpw(_prehash(password), _bcrypt.gensalt(rounds=rounds))
        return _BCRYPT_PREFIX + hashed.decode("ascii")

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITERATIONS
    )
    return f"{_PBKDF2_PREFIX}{_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash:
        return False
    try:
        if stored_hash.startswith(_BCRYPT_PREFIX):
            if _bcrypt is None:
                return False
            raw = stored_hash[len(_BCRYPT_PREFIX) :].encode("ascii")
            return _bcrypt.checkpw(_prehash(password), raw)
        if stored_hash.startswith("$2"):  # plain bcrypt hash
            if _bcrypt is None:
                return False
            return _bcrypt.checkpw(password.encode("utf-8")[:72], stored_hash.encode("ascii"))
        if stored_hash.startswith(_PBKDF2_PREFIX):
            _, iterations, salt, digest_hex = stored_hash.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations)
            )
            return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False
    return False


def generate_salt() -> str:
    """Legacy ``user_table.salt`` compatibility helper."""
    return os.urandom(8).hex()
