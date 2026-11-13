"""Time-based one-time passwords (authenticator apps)."""

import hmac
import time

import pyotp

from app.core.config import get_settings

STEP_SECONDS = 30


def _clock() -> float:
    """The current time. A separate function so tests can pin it."""
    return time.time()


def new_secret() -> str:
    return pyotp.random_base32(32)


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=get_settings().app_name)


def match_step(secret: str, code: str, *, now: float | None = None) -> int | None:
    """The time step `code` is valid for, or None.

    Accepts the current 30-second step and one either side, to tolerate clock
    drift. The caller stores the returned step and refuses any code at or
    before it, so a code that was observed cannot be replayed.
    """
    code = "".join(code.split())
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = int((_clock() if now is None else now) // STEP_SECONDS)
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None
