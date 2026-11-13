import logging

import redis

from app.core.config import get_settings
from app.core.logging import redact
from app.core.ratelimit import MemoryLimiter, RedisLimiter, ResilientLimiter
from app.modules.system import router as system_router
from tests.helpers import (
    OTHER_PASSWORD,
    PASSWORD,
    Browser,
    enable_mfa,
    link_token,
    signed_in,
    system_rows,
    totp_code,
)

EMAIL = "trader@example.com"


def test_no_secret_reaches_the_logs_or_the_audit_trail(caplog):
    """Run every credential-bearing flow, then search the logs and audit rows for each secret."""
    caplog.set_level(logging.DEBUG)
    browser = signed_in(EMAIL)
    verify_token = link_token(EMAIL, "verify-email")
    session_token = browser.session_cookie
    secret, recovery_codes = enable_mfa(browser)
    Browser().login(EMAIL, "a-wrong-guess-123")
    visitor = Browser()
    visitor.login(EMAIL)
    visitor.second_factor(recovery_code=recovery_codes[0])
    browser.post("/auth/password/change", json={"current_password": PASSWORD, "new_password": OTHER_PASSWORD})
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    reset_token = link_token(EMAIL, "reset-password")
    browser.post("/auth/password/reset", json={"token": reset_token, "new_password": "third-password-7788"})

    secrets = [
        PASSWORD,
        OTHER_PASSWORD,
        "third-password-7788",
        "a-wrong-guess-123",
        verify_token,
        reset_token,
        session_token,
        visitor.session_cookie,
        secret,
        totp_code(secret),
        browser.csrf,
        *recovery_codes,
    ]
    audit_text = "\n".join(str(row) for row in system_rows("SELECT action, target_id, detail::text FROM audit_logs"))
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert audit_text.count("auth.") >= 9  # the flows above really were recorded
    for value in secrets:
        assert value and value not in audit_text, "secret found in the audit trail"
        assert value not in log_text, "secret found in the logs"


def test_log_redaction_masks_credentials_that_slip_into_a_message():
    line = redact('login failed password=hunter2 token: abc.def Authorization="Bearer xyz" user=asha')
    assert "hunter2" not in line and "abc.def" not in line and "xyz" not in line
    assert "user=asha" in line and line.count("[redacted]") == 3


def test_audit_detail_refuses_secret_looking_keys():
    import pytest

    from app.core import audit
    from app.core.db import new_session

    db = new_session()
    try:
        for key in ("password", "new_password", "reset_token", "totp_secret", "recovery_code"):
            with pytest.raises(ValueError, match="must not carry secrets"):
                audit.record(db, "test.event", user_id=None, detail={key: "value"})
    finally:
        db.close()


def test_health_and_status_endpoints(browser):
    assert browser.get("/health/live").json() == {"status": "ok"}
    ready = browser.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["components"]["database"] == "ok"
    status = browser.get("/system/status").json()
    assert status["live_trading_enabled"] is False and status["paper_trading_enabled"] is True
    assert set(status) == {"app_name", "version", "environment", "live_trading_enabled", "paper_trading_enabled"}


def test_readiness_reports_degraded_without_redis_and_unavailable_without_the_database(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")
    degraded = browser.get("/health/ready")
    assert degraded.status_code == 200
    assert degraded.json() == {"status": "degraded", "components": {"database": "ok", "redis": "down"}}

    from sqlalchemy import create_engine

    broken = create_engine("postgresql+psycopg://tp_app:wrong@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    monkeypatch.setattr(system_router, "get_engine", lambda: broken)
    down = browser.get("/health/ready")
    assert down.status_code == 503 and down.json()["components"]["database"] == "down"


def test_security_headers_are_set_on_api_responses(browser):
    headers = browser.get("/system/status").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"
    assert len(headers["x-request-id"]) == 32


def test_rate_limiter_counts_in_redis_and_falls_back_locally_when_redis_is_down():
    live = RedisLimiter(get_settings().redis_url, prefix="test-rl:")
    try:
        live.reset("k")
    except redis.RedisError:
        import pytest

        pytest.skip("Redis is not running")
    assert [live.hit("k", 60)[0] for _ in range(3)] == [1, 2, 3]
    count, ttl = live.peek("k")
    assert count == 3 and 0 < ttl <= 60
    live.reset("k")
    assert live.peek("k") == (0, 0)

    # Redis outage: limits keep working from the in-process counter.
    dead = ResilientLimiter(RedisLimiter("redis://127.0.0.1:1/0"), MemoryLimiter())
    assert [dead.hit("k", 60)[0] for _ in range(3)] == [1, 2, 3]
    assert dead.peek("k")[0] == 3
    dead.reset("k")
    assert dead.peek("k") == (0, 0)


def test_sign_in_still_works_and_is_still_throttled_during_a_redis_outage(monkeypatch):
    from app.core import ratelimit
    from app.modules.auth import service
    from tests.helpers import create_user

    outage = ResilientLimiter(RedisLimiter("redis://127.0.0.1:1/0"), MemoryLimiter())
    monkeypatch.setattr(ratelimit, "get_limiter", lambda: outage)
    monkeypatch.setattr(service, "get_limiter", lambda: outage)
    create_user(EMAIL)
    assert Browser().login(EMAIL).status_code == 200
    for _ in range(5):
        assert Browser().login(EMAIL, "wrong-password-000").status_code == 401
    assert Browser().login(EMAIL, "wrong-password-000").status_code == 429


def test_development_mailer_prints_the_link_to_stdout_but_not_through_the_logger(capsys, caplog):
    from app.core.email import ConsoleEmailSender, verification_email

    caplog.set_level(logging.DEBUG)
    ConsoleEmailSender().send(verification_email("dev@example.com", "the-link-token-value"))
    assert "verify-email?token=the-link-token-value" in capsys.readouterr().out
    assert "the-link-token-value" not in "\n".join(record.getMessage() for record in caplog.records)
