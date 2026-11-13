"""Application settings, read from environment variables (or a local .env file).

Nothing here has a secret default: the process refuses to start without the
keys it needs, and refuses unsafe combinations in production.
"""
import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "TradeBridge"  # placeholder product name
    environment: Literal["development", "test", "production"] = "development"
    version: str = "0.1.0"

    # --- data stores -------------------------------------------------------
    # Runtime database role. It must not own the tables and must not be able
    # to bypass row-level security (see docs/security.md).
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_backend: Literal["redis", "memory"] = "redis"

    # --- secrets -----------------------------------------------------------
    # Binds CSRF tokens to sessions.
    secret_key: str = Field(min_length=32)
    # Field-encryption key ring: "id:base64(32 bytes)" entries separated by
    # commas. New data is encrypted with the active key; old keys stay listed
    # so existing data can still be read while it is re-encrypted.
    data_encryption_keys: str
    data_encryption_active_key: str

    # --- web ---------------------------------------------------------------
    # Origin of the dashboard. Used for links in emails and for the Origin
    # check on state-changing requests.
    frontend_origin: str = "http://localhost:3000"
    extra_allowed_origins: str = ""  # comma separated
    session_cookie_name: str = "tp_session"
    cookie_secure: bool = True
    session_idle_minutes: int = 24 * 60
    session_absolute_days: int = 7
    # Number of reverse proxies in front of the API whose X-Forwarded-For
    # entry can be trusted. 0 means: use the socket address.
    trusted_proxy_hops: int = 0

    # --- passwords ---------------------------------------------------------
    password_min_length: int = 10
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4

    # --- email -------------------------------------------------------------
    email_backend: Literal["console", "memory", "smtp"] = "console"
    email_from: str = "no-reply@example.com"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True

    sentry_dsn: str = ""

    # --- trading gates -----------------------------------------------------
    # Live execution is off unless explicitly enabled AND a reference to the
    # signed-off compliance review is recorded (docs/architecture.md, section
    # "Regulatory gate"). Paper trading is independent of this flag.
    live_trading_enabled: bool = False
    live_trading_compliance_approval: str = ""
    paper_trading_enabled: bool = True

    @property
    def allowed_origins(self) -> set[str]:
        extra = {o.strip().rstrip("/") for o in self.extra_allowed_origins.split(",") if o.strip()}
        return {self.frontend_origin.rstrip("/")} | extra

    @property
    def cookie_name(self) -> str:
        # The __Host- prefix makes browsers refuse the cookie unless it is
        # Secure, host-only and scoped to "/", which blocks subdomain tricks.
        return f"__Host-{self.session_cookie_name}" if self.cookie_secure else self.session_cookie_name

    def encryption_keys(self) -> dict[str, bytes]:
        keys: dict[str, bytes] = {}
        for entry in self.data_encryption_keys.split(","):
            entry = entry.strip()
            if not entry:
                continue
            key_id, _, encoded = entry.partition(":")
            if not key_id or not encoded:
                raise ValueError("DATA_ENCRYPTION_KEYS entries must look like 'id:base64key'")
            try:
                key = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ValueError(f"encryption key '{key_id}' is not valid base64") from exc
            if len(key) != 32:
                raise ValueError(f"encryption key '{key_id}' must be 32 bytes")
            keys[key_id] = key
        return keys

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        keys = self.encryption_keys()
        if self.data_encryption_active_key not in keys:
            raise ValueError("DATA_ENCRYPTION_ACTIVE_KEY is not listed in DATA_ENCRYPTION_KEYS")
        if self.live_trading_enabled and not self.live_trading_compliance_approval.strip():
            raise ValueError(
                "LIVE_TRADING_ENABLED requires LIVE_TRADING_COMPLIANCE_APPROVAL: a reference to the "
                "signed-off compliance review. Live trading stays off without it."
            )
        if self.environment == "production":
            problems = []
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true")
            if not self.frontend_origin.startswith("https://"):
                problems.append("FRONTEND_ORIGIN must be an https:// origin")
            if self.email_backend != "smtp":
                problems.append("EMAIL_BACKEND must be smtp (other backends print or drop security emails)")
            if self.rate_limit_backend != "redis":
                problems.append("RATE_LIMIT_BACKEND must be redis (limits must be shared across instances)")
            if problems:
                raise ValueError("unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
