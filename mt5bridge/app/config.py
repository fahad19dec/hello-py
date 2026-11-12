"""Settings, read from environment variables."""
import os


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} must be set (see README)")
    return value


class Settings:
    def __init__(self) -> None:
        self.database_url = os.environ.get("MT5BRIDGE_DATABASE_URL", "sqlite:///./mt5bridge.db")
        # Signs browser session cookies.
        self.secret_key = _required("MT5BRIDGE_SECRET_KEY")
        # Fernet key that encrypts stored MT5 passwords.
        self.fernet_key = _required("MT5BRIDGE_FERNET_KEY")
        # Shared secret the Windows workers send in the X-Worker-Key header.
        self.worker_key = _required("MT5BRIDGE_WORKER_KEY")
        # Public base URL shown to users when building their webhook URL.
        self.public_url = os.environ.get("MT5BRIDGE_PUBLIC_URL", "http://localhost:8000").rstrip("/")
        # A queued order older than this is expired instead of executed.
        self.max_order_age_seconds = int(os.environ.get("MT5BRIDGE_MAX_ORDER_AGE", "60"))
        # Per-user webhook rate limit.
        self.alerts_per_minute = int(os.environ.get("MT5BRIDGE_ALERTS_PER_MINUTE", "60"))
        self.https_only = os.environ.get("MT5BRIDGE_HTTPS_ONLY", "0") == "1"
        # The fixed public IP this server sends Zerodha orders from. Zerodha rejects
        # API orders unless the customer has registered that IP in their Kite profile.
        self.kite_static_ip = os.environ.get("MT5BRIDGE_KITE_STATIC_IP", "")


settings = Settings()
