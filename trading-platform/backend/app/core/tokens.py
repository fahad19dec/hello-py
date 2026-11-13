"""Random tokens for sessions, email links and recovery codes.

Only a SHA-256 hash of each token is stored. The tokens are long random
values, so a fast hash is safe here (unlike for passwords) and lets the
database look a token up by its hash.
"""
import hashlib
import hmac
import secrets

_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alike characters


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for(session_token: str, secret_key: str) -> str:
    """CSRF token bound to one session: an HMAC of the session token.

    It can be recomputed from the session cookie on every request, so nothing
    extra is stored, and it changes whenever the session token rotates.
    """
    return hmac.new(secret_key.encode(), f"csrf:{session_token}".encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalise_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def hash_recovery_code(code: str) -> str:
    return hash_token(normalise_recovery_code(code))
