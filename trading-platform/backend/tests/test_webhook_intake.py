"""The webhook endpoint: authentication, validation, freshness, idempotency and the audit trail."""

import hashlib
import hmac
import json
from datetime import timedelta

from app.models.trading import Signal
from tests.trading_helpers import alert, iso, now, order_rows, rows, setup_paper, signal_rows, work


def test_an_accepted_alert_is_stored_before_anything_runs_and_answered_quickly():
    trader, account, strategy, hook = setup_paper()
    response = alert(trader.browser, hook["url"], signal_id="s-1")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "accepted"
    [signal] = signal_rows()
    assert (signal.status, signal.action, signal.symbol) == ("received", "BUY", "RELIANCE")
    assert order_rows() == []  # nothing executes inside the web request
    work()
    [order] = order_rows()
    assert order.state == "FILLED" and order.is_paper
    assert rows(Signal)[0].status == "processed"


def test_the_same_signal_id_is_executed_once_however_often_it_arrives():
    trader, account, strategy, hook = setup_paper()
    first = alert(trader.browser, hook["url"], signal_id="dup-1")
    second = alert(trader.browser, hook["url"], signal_id="dup-1")
    work()
    third = alert(trader.browser, hook["url"], signal_id="dup-1")
    work()
    assert first.status_code == 202
    assert second.status_code == third.status_code == 200
    assert second.json()["status"] == "duplicate" and second.json()["signal_id"] == first.json()["signal_id"]
    [signal] = signal_rows()
    assert signal.duplicate_count == 2
    assert len(order_rows()) == 1


def test_an_identical_resend_without_a_signal_id_is_also_a_duplicate():
    trader, account, strategy, hook = setup_paper()
    body = json.dumps({"symbol": "TCS", "action": "BUY", "quantity": 1, "price": 3900, "timestamp": iso()}).encode()
    assert alert(trader.browser, hook["url"], raw=body).status_code == 202
    assert alert(trader.browser, hook["url"], raw=body).json()["status"] == "duplicate"
    # A new alert differs at least in its timestamp, so it is a new signal.
    assert (
        alert(trader.browser, hook["url"], symbol="TCS", timestamp=iso(now() + timedelta(seconds=1))).status_code == 202
    )
    assert len(signal_rows()) == 2


def test_unknown_rotated_and_revoked_urls_are_not_found():
    trader, account, strategy, hook = setup_paper()
    base = hook["url"].rsplit("/", 1)[0]
    assert alert(trader.browser, base + "/not-a-real-token-at-all-0000000000").status_code == 404
    rotated = trader.ok(trader.browser.post(f"/webhooks/{hook['webhook']['id']}/rotate"))
    assert alert(trader.browser, hook["url"]).status_code == 404
    assert alert(trader.browser, rotated["url"], signal_id="r1").status_code == 202
    trader.ok(trader.browser.post(f"/webhooks/{hook['webhook']['id']}/revoke"))
    assert alert(trader.browser, rotated["url"], signal_id="r2").status_code == 404
    assert trader.browser.post(f"/webhooks/{hook['webhook']['id']}/rotate").status_code == 409


def test_a_disabled_webhook_refuses_alerts_but_records_them():
    trader, account, strategy, hook = setup_paper()
    trader.ok(trader.browser.patch(f"/webhooks/{hook['webhook']['id']}", json={"enabled": False}))
    response = alert(trader.browser, hook["url"], signal_id="d1")
    assert response.status_code == 403
    [signal] = signal_rows()
    assert signal.status == "rejected" and "disabled" in signal.reason
    work()
    assert order_rows() == []


def test_passphrase_is_checked_and_never_stored():
    trader, account, strategy, _ = setup_paper()
    hook = trader.webhook(strategy, passphrase="open sesame 123")
    assert alert(trader.browser, hook["url"], signal_id="p1").status_code == 401
    assert alert(trader.browser, hook["url"], signal_id="p2", passphrase="wrong guess 1").status_code == 401
    assert alert(trader.browser, hook["url"], signal_id="p3", passphrase="open sesame 123").status_code == 202
    for signal in signal_rows():
        assert "passphrase" not in signal.payload
        assert "open sesame" not in json.dumps(signal.payload)


def test_signed_webhooks_need_a_valid_hmac_signature():
    trader, account, strategy, _ = setup_paper()
    created = trader.webhook(strategy, require_signature=True)
    secret = created["signing_secret"]
    body = json.dumps(
        {"symbol": "INFY", "action": "BUY", "quantity": 1, "price": 1500, "signal_id": "h1", "timestamp": iso()}
    ).encode()
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert alert(trader.browser, created["url"], raw=body).status_code == 401
    assert (
        alert(trader.browser, created["url"], raw=body, headers={"X-Signature": "sha256=" + "0" * 64}).status_code
        == 401
    )
    tampered = body.replace(b'"quantity": 1', b'"quantity": 9')
    assert alert(trader.browser, created["url"], raw=tampered, headers={"X-Signature": good}).status_code == 401
    assert alert(trader.browser, created["url"], raw=body, headers={"X-Signature": good}).status_code == 202
    # The rejected attempts did not claim the signal id; the signed one did, and only once.
    assert (
        alert(trader.browser, created["url"], raw=body, headers={"X-Signature": good}).json()["status"] == "duplicate"
    )


def test_a_forged_alert_cannot_block_the_genuine_one_with_the_same_id():
    trader, account, strategy, _ = setup_paper()
    hook = trader.webhook(strategy, passphrase="open sesame 123")
    # Someone who learned the URL, but not the passphrase, tries to claim tomorrow's signal ids.
    for _ in range(3):
        assert alert(trader.browser, hook["url"], signal_id="next-1", quantity=999).status_code == 401
    genuine = alert(trader.browser, hook["url"], signal_id="next-1", passphrase="open sesame 123")
    assert genuine.status_code == 202
    work()
    [order] = order_rows()
    assert order.quantity == 10 and order.state == "FILLED"


def test_stale_and_future_signals_are_rejected_with_the_reason():
    trader, account, strategy, hook = setup_paper()
    old = alert(trader.browser, hook["url"], signal_id="old", timestamp=iso(now() - timedelta(minutes=5)))
    future = alert(trader.browser, hook["url"], signal_id="future", timestamp=iso(now() + timedelta(minutes=5)))
    assert old.status_code == future.status_code == 422
    assert "old; the limit is 60s" in old.json()["message"]
    assert "future" in future.json()["message"]
    # A tighter age limit for this strategy applies at intake too.
    trader.limits(f"strategy:{strategy['id']}", max_signal_age_seconds=5)
    late = alert(trader.browser, hook["url"], signal_id="late", timestamp=iso(now() - timedelta(seconds=20)))
    assert late.status_code == 422
    work()
    assert order_rows() == []
    assert {s.status for s in signal_rows()} == {"rejected"}


def test_malformed_alerts_are_rejected_and_kept_in_the_signal_log():
    trader, account, strategy, hook = setup_paper()
    cases = {
        b"buy reliance now": "not valid JSON",
        b"[1, 2]": "must be a JSON object",
        json.dumps({"symbol": "X", "action": "BUY", "quantity": 1}).encode(): "timestamp",
        json.dumps({"symbol": "X", "action": "HOLD", "timestamp": iso()}).encode(): "unknown action 'HOLD'",
        json.dumps(
            {"symbol": "X", "action": "BUY", "order_type": "LIMIT", "timestamp": iso()}
        ).encode(): "need a price",
        json.dumps({"symbol": "X", "action": "BUY", "quantity": -1, "timestamp": iso()}).encode(): "greater than zero",
        json.dumps({"symbol": "X", "action": "BUY", "quantity": "lots", "timestamp": iso()}).encode(): "number",
        json.dumps({"symbol": "X", "action": "BUY", "timestamp": "yesterday"}).encode(): "ISO 8601",
        json.dumps({"action": "BUY", "timestamp": iso()}).encode(): "needs a symbol",
        json.dumps({"symbol": "X", "action": "MODIFY_SL", "timestamp": iso()}).encode(): "needs stop_loss",
        json.dumps(
            {"symbol": "X", "action": "BUY", "price": 100, "stop_loss": 105, "timestamp": iso()}
        ).encode(): "wrong side",
        json.dumps(
            {"symbol": "X", "action": "BUY", "strategy_id": "OTHER", "timestamp": iso()}
        ).encode(): "does not match",
    }
    for body, reason in cases.items():
        response = alert(trader.browser, hook["url"], raw=body)
        assert response.status_code == 400, body
        assert reason in response.json()["message"], (body, response.json())
    assert alert(trader.browser, hook["url"], raw=b"x" * 9000).status_code == 413
    assert len([s for s in signal_rows() if s.status == "rejected"]) == len(cases)
    work()
    assert order_rows() == []


def test_action_aliases_are_configurable():
    trader, account, strategy, hook = setup_paper()
    assert alert(trader.browser, hook["url"], signal_id="a1", action="long").status_code == 202
    trader.ok(trader.browser.patch(f"/strategies/{strategy['id']}", json={"action_aliases": {"GO_LONG": "BUY"}}))
    assert alert(trader.browser, hook["url"], signal_id="a2", action="LONG").status_code == 400
    assert alert(trader.browser, hook["url"], signal_id="a3", action="GO_LONG").status_code == 202
    assert {s.action for s in signal_rows() if s.status == "received"} == {"BUY"}
    bad = trader.browser.patch(f"/strategies/{strategy['id']}", json={"action_aliases": {"BUY": "SELL"}})
    assert bad.status_code == 422


def test_exchange_prefix_is_stripped_and_unix_timestamps_are_accepted():
    trader, account, strategy, hook = setup_paper()
    millis = int(now().timestamp() * 1000)
    assert (
        alert(trader.browser, hook["url"], symbol="NSE:reliance", timestamp=millis, signal_id="u1").status_code == 202
    )
    assert signal_rows()[0].symbol == "RELIANCE"


def test_the_test_endpoint_checks_without_storing_or_executing():
    trader, account, strategy, hook = setup_paper()
    report = trader.ok(
        trader.browser.post(
            f"/webhooks/{hook['webhook']['id']}/test",
            json={
                "message": json.dumps(
                    {"symbol": "RELIANCE", "action": "BUY", "quantity": 5, "price": 2500, "timestamp": iso()}
                )
            },
        )
    )
    assert report["valid"] is True and report["action"] == "BUY"
    assert report["accounts"] == [{"account": "Paper", "broker": "paper", "result": "would be queued", "quantity": "5"}]
    bad = trader.ok(trader.browser.post(f"/webhooks/{hook['webhook']['id']}/test", json={"message": "{}"}))
    assert bad["valid"] is False and bad["problems"]
    assert signal_rows() == [] and order_rows() == []


def test_alert_intake_still_works_while_redis_is_down(monkeypatch):
    from app.core.config import get_settings

    trader, account, strategy, hook = setup_paper()
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")
    assert alert(trader.browser, hook["url"], signal_id="r1").status_code == 202
    work()  # the worker also polls, so the signal is processed without the wake-up
    assert order_rows()[0].state == "FILLED"


def test_a_database_outage_answers_with_an_error_and_records_nothing_half_done(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_module
    from app.main import app

    trader, account, strategy, hook = setup_paper()
    broken = sessionmaker(
        bind=create_engine("postgresql+psycopg://tp_app:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    )

    def failing_db():
        session = broken()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = failing_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        path = "/api/v1" + hook["url"].split("/api/v1", 1)[1]
        response = client.post(path, content=json.dumps({"symbol": "X", "action": "BUY", "timestamp": iso()}))
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
    assert signal_rows() == []  # TradingView logs the failed delivery; nothing was half-recorded here
