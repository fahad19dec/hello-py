"""Zerodha execution against a simulated broker: acceptance is not a fill, and nothing is re-sent blindly."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import httpx

from app.brokers.base import OrderRequest, Outcome
from app.brokers.kite import KiteAdapter, login_url, token_expiry
from app.core.db import SYSTEM, access, new_session
from app.models.trading import BrokerCredential
from tests.trading_helpers import alert, fill_rows, now, order_rows, position_rows, setup_kite, work


def test_an_accepted_order_is_submitted_not_filled_until_the_broker_says_so(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["accept"])
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=2500)
    work()
    [order] = order_rows()
    assert order.state == "SUBMITTED" and order.broker_order_id == "SIM1" and fill_rows() == []
    request = sim.placed[0]
    assert (request.symbol, request.exchange, request.product, request.side) == ("RELIANCE", "NSE", "MIS", "BUY")
    assert request.client_order_id == order.client_order_id  # sent as the Zerodha tag
    sim.fill(order.client_order_id, Decimal(4), Decimal("2500.5"))
    work()
    [order] = order_rows()
    assert order.state == "PARTIALLY_FILLED" and order.filled_quantity == 4
    sim.fill(order.client_order_id, Decimal(10), Decimal("2501.0"))
    work()
    [order] = order_rows()
    assert order.state == "FILLED" and order.filled_quantity == 10
    fills = sorted(fill_rows(), key=lambda f: f.quantity)
    assert [(f.quantity, f.price) for f in fills] == [
        (4, Decimal("2500.5")),
        (6, Decimal("2501.333333333333333333")),
    ] or sum(f.quantity for f in fills) == 10
    assert position_rows()[0].quantity == 10
    work()  # polling again changes nothing
    assert len(fill_rows()) == 2 and len(sim.placed) == 1


def test_a_lost_reply_is_reconciled_by_its_tag_and_never_sent_twice(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["timeout"])
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=2500)
    work()
    [order] = order_rows()
    assert order.state in ("UNKNOWN", "FILLED")  # the reconciler may find it in the same pass
    work()
    [order] = order_rows()
    assert order.state == "FILLED" and order.broker_order_id == "SIM1"
    assert len(sim.placed) == 1  # the order was looked up, not placed again
    events = [e["to"] for e in trader.ok(trader.browser.get(f"/orders/{order.id}"))["events"]]
    assert "UNKNOWN" in events and events[-1] == "FILLED"


def test_an_order_that_never_reached_the_broker_fails_only_after_checking(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["lost"])
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=2500)
    work()
    assert order_rows()[0].state == "UNKNOWN"
    work()  # still inside the grace period: the order book may lag
    assert order_rows()[0].state == "UNKNOWN"
    sim.reachable = False
    work(at=now() + timedelta(seconds=40))  # broker unreachable: stays unknown, no guess
    assert order_rows()[0].state == "UNKNOWN"
    sim.reachable = True
    work(at=now() + timedelta(seconds=40))
    order = order_rows()[0]
    assert order.state == "FAILED" and "not found at Zerodha" in order.reason
    assert len(sim.placed) == 1


def test_a_refused_connection_is_retried_because_nothing_was_sent(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["refused", "accept"])
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=2500)
    work()
    assert order_rows()[0].state == "QUEUED"
    work(at=now() + timedelta(seconds=3))
    order = order_rows()[0]
    assert order.state == "SUBMITTED" and order.attempts == 2 and len(sim.placed) == 2


def test_broker_rejection_is_recorded_and_repeated_failures_open_the_circuit(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["reject", "reject", "reject"])
    for n in range(4):
        alert(trader.browser, hook["url"], signal_id=f"r{n}", quantity=1, price=2500)
        work()
    orders = order_rows()
    assert [o.rule for o in orders[:3]] == ["broker_rejected"] * 3
    assert "Insufficient funds" in orders[0].reason
    assert orders[3].rule == "circuit_breaker"  # stopped after three failures in a row
    assert len(sim.placed) == 3
    trader.ok(trader.browser.post(f"/broker-accounts/{account['id']}/reset-circuit"))
    alert(trader.browser, hook["url"], signal_id="after", quantity=1, price=2500)
    work()
    assert order_rows()[-1].state in ("SUBMITTED", "FILLED")


def test_an_expired_login_marks_the_account_and_stops_further_orders(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["auth"])
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, price=2500)
    work()
    assert order_rows()[0].rule == "broker_rejected"
    assert trader.ok(trader.browser.get(f"/broker-accounts/{account['id']}"))["connection_status"] == "login_required"
    alert(trader.browser, hook["url"], signal_id="2", quantity=1, price=2500)
    work()
    assert order_rows()[-1].rule == "broker_not_connected"


def test_unmapped_symbols_and_attached_stops_are_refused_for_zerodha(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch)
    alert(trader.browser, hook["url"], signal_id="1", symbol="TCS", quantity=1, price=3900)
    alert(trader.browser, hook["url"], signal_id="2", quantity=1, price=2500, stop_loss=2450)
    alert(trader.browser, hook["url"], signal_id="3", action="MODIFY_SL", stop_loss=2400, quantity=None)
    work()
    unmapped, with_stop, modify = order_rows()
    assert unmapped.rule == "instrument_unmapped" and "no instrument mapping" in unmapped.reason
    assert with_stop.rule == "not_supported" and "stop loss" in with_stop.reason
    assert modify.rule == "not_supported"
    assert sim.placed == []  # nothing reached the broker


def test_closed_market_is_refused_before_anything_is_sent(monkeypatch):
    from app.trading import risk

    trader, account, strategy, hook, sim = setup_kite(monkeypatch)
    monkeypatch.setattr(risk, "kite_market_closed", lambda exchange, at: "NSE is closed (Sat 11:00 IST)")
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, price=2500)
    work()
    assert order_rows()[0].rule == "market_closed" and sim.placed == []


def test_close_uses_the_platform_position_and_paper_and_live_stay_separate(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["fill", "fill"])
    paper = trader.account("paper", "Paper")
    trader.link(strategy, paper)
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=2500)
    work(passes=2)
    alert(trader.browser, hook["url"], signal_id="2", action="CLOSE", quantity=None, price=2510)
    work(passes=2)
    live = [o for o in order_rows() if not o.is_paper]
    assert [(o.intent, o.side, o.quantity, o.state) for o in live] == [
        ("open", "BUY", 10, "FILLED"),
        ("close", "SELL", 10, "FILLED"),
    ]
    assert sim.placed[1].side == "SELL" and sim.placed[1].quantity == 10
    overview = trader.ok(trader.browser.get("/overview"))
    assert overview["live"]["successful_executions"] == 2 and overview["paper"]["successful_executions"] == 2


def test_postbacks_are_verified_with_the_api_secret(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch, script=["accept"])
    trader.ok(
        trader.browser.put(
            f"/broker-accounts/{account['id']}/kite/credentials",
            json={"api_key": "kitekey1", "api_secret": "s3cret-value"},
        )
    )
    from tests.trading_helpers import connect_kite

    connect_kite(account["id"])
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=2500)
    work()
    order = order_rows()[0]
    payload = {
        "order_id": order.broker_order_id,
        "order_timestamp": "2026-11-13 10:00:00",
        "status": "COMPLETE",
        "filled_quantity": 10,
        "average_price": 2502,
        "tag": order.client_order_id,
    }
    payload["checksum"] = "0" * 64
    bad = trader.browser.http.post("/api/v1/hooks/kite/postback", content=json.dumps(payload))
    assert bad.status_code == 401 and order_rows()[0].state == "SUBMITTED"
    payload["checksum"] = hashlib.sha256(
        (order.broker_order_id + "2026-11-13 10:00:00" + "s3cret-value").encode()
    ).hexdigest()
    good = trader.browser.http.post("/api/v1/hooks/kite/postback", content=json.dumps(payload))
    assert good.status_code == 200
    order = order_rows()[0]
    assert order.state == "FILLED" and order.average_price == 2502
    again = trader.browser.http.post("/api/v1/hooks/kite/postback", content=json.dumps(payload))
    assert again.status_code == 200 and len(fill_rows()) == 1  # a repeated postback changes nothing


def test_kite_login_flow_stores_the_daily_token_encrypted(monkeypatch):
    trader, account, strategy, hook, sim = setup_kite(monkeypatch)
    fresh = trader.account("kite", "Zerodha 2")
    assert trader.browser.post(f"/broker-accounts/{fresh['id']}/kite/login").status_code == 409
    trader.ok(
        trader.browser.put(
            f"/broker-accounts/{fresh['id']}/kite/credentials",
            json={"api_key": "abcd1234", "api_secret": "the-secret-1"},
        )
    )
    started = trader.ok(trader.browser.post(f"/broker-accounts/{fresh['id']}/kite/login"))
    assert started["login_url"].startswith("https://kite.zerodha.com/connect/login?v=3&api_key=abcd1234")
    state = httpx.URL(started["login_url"]).params["redirect_params"].split("=", 1)[1]

    def exchange(self, request_token, api_secret):
        assert (request_token, api_secret) == ("reqtoken1", "the-secret-1")
        self.access_token = "daily-access-token"
        return self.access_token

    from app.brokers.base import AccountInfo

    monkeypatch.setattr(KiteAdapter, "exchange_request_token", exchange)
    monkeypatch.setattr(
        KiteAdapter,
        "get_account_info",
        lambda self: AccountInfo("INR", Decimal(5000), Decimal(5000), Decimal(4000), now()),
    )
    forged = trader.browser.post(
        "/broker-accounts/kite/session", json={"state": state + "x", "request_token": "reqtoken1"}
    )
    assert forged.status_code == 400
    connected = trader.ok(
        trader.browser.post("/broker-accounts/kite/session", json={"state": state, "request_token": "reqtoken1"})
    )
    assert connected["connection_status"] == "connected" and connected["margin_available"] == 4000
    db = new_session()
    with access(db, SYSTEM):
        stored = {c.kind: c for c in db.query(BrokerCredential).filter_by(broker_account_id=fresh["id"]).all()}
    db.close()
    assert "daily-access-token" not in stored["kite_access_token"].value_enc
    assert "the-secret-1" not in stored["kite_api_secret"].value_enc
    assert "the-secret-1" not in json.dumps(connected) and "daily-access-token" not in json.dumps(connected)
    # Another user cannot use this state, even with a valid request token.
    from tests.trading_helpers import Trader

    other = Trader("other@example.com")
    assert other.browser.post(
        "/broker-accounts/kite/session", json={"state": state, "request_token": "reqtoken1"}
    ).status_code in (400, 404)


def test_tokens_expire_at_six_ist_and_the_account_asks_for_a_new_login(monkeypatch):
    from datetime import UTC, datetime

    assert token_expiry(datetime(2026, 11, 13, 3, 0, tzinfo=UTC)).isoformat() == "2026-11-14T00:30:00+00:00"
    trader, account, strategy, hook, sim = setup_kite(monkeypatch)
    work(at=now() + timedelta(hours=9))
    assert trader.ok(trader.browser.get(f"/broker-accounts/{account['id']}"))["connection_status"] == "login_required"


def test_the_real_kite_adapter_sends_market_protection_and_classifies_failures():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/orders/regular":
            form = dict(httpx.QueryParams(request.content.decode()))
            if form.get("quantity") == "13":
                raise httpx.ReadTimeout("slow", request=request)
            if form.get("quantity") == "14":
                raise httpx.ConnectError("down", request=request)
            if form.get("quantity") == "15":
                return httpx.Response(
                    400, json={"status": "error", "message": "Insufficient funds", "error_type": "InputException"}
                )
            return httpx.Response(200, json={"status": "success", "data": {"order_id": "250101000001"}})
        return httpx.Response(404, json={"status": "error", "message": "nope"})

    client = httpx.Client(base_url="https://api.kite.trade", transport=httpx.MockTransport(handler))
    adapter = KiteAdapter("key", "tok", client=client)

    def place(quantity):
        return adapter.place_order(
            OrderRequest(
                client_order_id="TB123",
                symbol="SBIN",
                side="BUY",
                quantity=Decimal(quantity),
                exchange="NSE",
                product="MIS",
            )
        )

    accepted = place(10)
    assert accepted.outcome == Outcome.ACCEPTED and accepted.broker_order_id == "250101000001"
    form = dict(httpx.QueryParams(sent[0].content.decode()))
    assert form["market_protection"] == "-1" and form["tag"] == "TB123" and form["order_type"] == "MARKET"
    assert sent[0].headers["authorization"] == "token key:tok"
    assert place(13).outcome == Outcome.UNKNOWN
    assert place(14).outcome == Outcome.NOT_SENT
    rejected = place(15)
    assert rejected.outcome == Outcome.REJECTED and "Insufficient funds" in rejected.message
    assert "api_key=key" in login_url("key", "state1")
