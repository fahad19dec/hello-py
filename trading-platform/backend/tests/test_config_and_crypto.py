import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.crypto import DecryptionError, FieldCipher

KEY_1 = base64.b64encode(b"\x01" * 32).decode()
KEY_2 = base64.b64encode(b"\x02" * 32).decode()
BASE = {
    "database_url": "postgresql+psycopg://u:p@localhost/db",
    "secret_key": "x" * 40,
    "data_encryption_keys": f"k1:{KEY_1}",
    "data_encryption_active_key": "k1",
}


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch):
    """These tests are about the code's own defaults, so hide the test environment's values."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**BASE, **overrides})


def test_live_trading_is_off_by_default_and_paper_trading_is_independent():
    s = settings()
    assert s.live_trading_enabled is False
    assert s.paper_trading_enabled is True


def test_live_trading_cannot_be_enabled_without_a_recorded_compliance_approval():
    with pytest.raises(ValidationError, match="LIVE_TRADING_COMPLIANCE_APPROVAL"):
        settings(live_trading_enabled=True)
    with pytest.raises(ValidationError, match="LIVE_TRADING_COMPLIANCE_APPROVAL"):
        settings(live_trading_enabled=True, live_trading_compliance_approval="   ")
    assert settings(live_trading_enabled=True, live_trading_compliance_approval="CR-2026-001").live_trading_enabled


def test_production_refuses_unsafe_settings():
    with pytest.raises(ValidationError) as error:
        settings(environment="production", cookie_secure=False, email_backend="console", rate_limit_backend="memory")
    message = str(error.value)
    for expected in ("COOKIE_SECURE", "FRONTEND_ORIGIN", "EMAIL_BACKEND", "RATE_LIMIT_BACKEND"):
        assert expected in message
    ok = settings(
        environment="production", cookie_secure=True, email_backend="smtp", frontend_origin="https://app.example.com"
    )
    assert ok.cookie_name == "__Host-tp_session"


def test_secret_and_encryption_keys_are_validated():
    with pytest.raises(ValidationError):
        settings(secret_key="too-short")
    with pytest.raises(ValidationError, match="32 bytes"):
        settings(data_encryption_keys="k1:" + base64.b64encode(b"short").decode())
    with pytest.raises(ValidationError, match="not listed"):
        settings(data_encryption_active_key="k9")


def test_field_encryption_round_trip_and_binding_to_its_row():
    cipher = FieldCipher({"k1": b"\x01" * 32}, "k1")
    sealed = cipher.encrypt("JBSWY3DPEHPK3PXP", "user:1:totp")
    assert "JBSWY3DPEHPK3PXP" not in sealed
    assert cipher.decrypt(sealed, "user:1:totp") == "JBSWY3DPEHPK3PXP"
    # The same plaintext never produces the same ciphertext twice.
    assert cipher.encrypt("JBSWY3DPEHPK3PXP", "user:1:totp") != sealed
    # Moved to another user's row, or tampered with, it does not decrypt.
    with pytest.raises(DecryptionError):
        cipher.decrypt(sealed, "user:2:totp")
    with pytest.raises(DecryptionError):
        cipher.decrypt(sealed[:-4] + "AAAA", "user:1:totp")
    with pytest.raises(DecryptionError):
        cipher.decrypt("not-a-ciphertext", "user:1:totp")


def test_key_rotation_keeps_old_data_readable():
    old = FieldCipher({"k1": b"\x01" * 32}, "k1")
    sealed = old.encrypt("secret", "aad")
    rotated = FieldCipher({"k1": b"\x01" * 32, "k2": b"\x02" * 32}, "k2")
    assert rotated.decrypt(sealed, "aad") == "secret"
    assert rotated.needs_rotation(sealed)
    assert not rotated.needs_rotation(rotated.encrypt("secret", "aad"))
    # Once the old key is retired, data still under it cannot be read.
    with pytest.raises(DecryptionError):
        FieldCipher({"k2": b"\x02" * 32}, "k2").decrypt(sealed, "aad")
