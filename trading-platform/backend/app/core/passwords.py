"""Password hashing (Argon2id) and the password policy."""
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .config import get_settings
from .errors import AppError

MAX_PASSWORD_LENGTH = 128

# Long enough to pass the length rule, common enough to be guessed first.
_COMMON_PASSWORDS = frozenset(
    """
    1234567890 12345678910 123456789012 0123456789 0987654321 1111111111 0000000000
    1q2w3e4r5t 1q2w3e4r5t6y 1qaz2wsx3edc qwertyuiop qwerty12345 qwerty123456 qwertyuiop123
    asdfghjkl123 zxcvbnm12345 password12 password123 password1234 password12345 passw0rd123
    p@ssw0rd123 iloveyou123 iloveyou1234 letmein1234 welcome1234 welcome12345 administrator
    admin123456 admin12345 changeme123 trustno1234 football123 baseball123 superman123
    sunshine123 princess123 dragon12345 monkey12345 abcdefghij abcdefghijk abcd123456 abc1234567
    india12345 india123456 trading123 trading1234 zerodha123 nifty12345 banknifty123
    """.split()
)


@lru_cache
def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(time_cost=s.argon2_time_cost, memory_cost=s.argon2_memory_kib, parallelism=s.argon2_parallelism)


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return _hasher().verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash was made with weaker parameters than the current ones."""
    try:
        return _hasher().check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


@lru_cache
def _dummy_hash() -> str:
    return hash_password("timing-equaliser-not-a-real-password")


def burn_verification_time(password: str) -> None:
    """Spend the same time as a real check when the account does not exist,
    so response time does not reveal which emails are registered."""
    verify_password(password, _dummy_hash())


def validate_new_password(password: str, *, email: str) -> None:
    """Raise AppError(422) with a specific reason if the password is not acceptable."""
    settings = get_settings()

    def reject(message: str) -> AppError:
        return AppError(422, "weak_password", message)

    if len(password) < settings.password_min_length:
        raise reject(f"Password must be at least {settings.password_min_length} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise reject(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        raise reject("That password is too common. Choose one that is harder to guess.")
    if len(set(password)) < 4:
        raise reject("Password must not be a repeated character or short pattern.")
    local_part = email.split("@", 1)[0].lower()
    if (len(local_part) >= 4 and local_part in lowered) or email.lower() in lowered:
        raise reject("Password must not contain your email address.")
    if settings.app_name.lower() in lowered:
        raise reject("Password must not contain the name of this service.")
