import hashlib
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app import kite
from app.db import SessionLocal
from app.main import kite_session_valid
from app.models import Account, Order, utcnow
from tests.test_app import account_id, add_account, signup


class FakeKite:
    """Simulates the parts of api.kite.trade the bridge calls."""

    def __init__(self):
        self.orders = []
        self.positions = []
        self.fail_with = None
        self.fail_when = None  # optional: form -> error message, or None
        self.raise_on_order = None  # optional: exception class raised for order requests

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if request.url.path == "/session/token":
            expected = hashlib.sha256(b"key123" + b"reqtoken" + b"secret456").hexdigest()
            if form["checksum"] != expected:
                return httpx.Response(403, json={"status": "error", "message": "Invalid checksum",
                                                 "error_type": "TokenException"})
            return httpx.Response(200, json={"status": "success", "data": {"access_token": "day-token"}})
        if request.headers.get("authorization") != "token key123:day-token":
            return httpx.Response(403, json={"status": "error", "message": "Incorrect api_key or access_token",
                                             "error_type": "TokenException"})
        if self.fail_with:
            return httpx.Response(400, json={"status": "error", "message": self.fail_with,
                                             "error_type": "InputException"})
        if request.url.path == "/portfolio/positions":
            return httpx.Response(200, json={"status": "success", "data": {"net": self.positions, "day": []}})
        if request.url.path == "/orders/regular":
            if self.raise_on_order:
                raise self.raise_on_order("boom", request=request)
            message = self.fail_when(form) if self.fail_when else None
            if message:
                return httpx.Response(400, json={"status": "error", "message": message,
                                                 "error_type": "OrderException"})
            self.orders.append(form)
            return httpx.Response(200, json={"status": "success", "data": {"order_id": f"2511{len(self.orders)}"}})
        return httpx.Response(404, json={"status": "error", "message": "not found"})


@pytest.fixture
def fake_kite(monkeypatch):
    fake = FakeKite()
    monkeypatch.setattr(
        kite, "new_client",
        lambda: httpx.Client(base_url=kite.API_URL, transport=httpx.MockTransport(fake.handler)),
    )
    return fake


def add_kite(client, label="Zerodha", max_quantity="100"):
    r = client.post("/accounts/kite", data={"label": label, "api_key": "key123", "api_secret": "secret456",
                                            "max_quantity": max_quantity}, follow_redirects=False)
    assert r.status_code == 303


def kite_login(client, label="Zerodha", callback="/kite/callback?request_token=reqtoken&status=success"):
    aid = account_id(label)
    r = client.get(f"/kite/{aid}/login", follow_redirects=False)
    assert r.headers["location"] == "https://kite.zerodha.com/connect/login?v=3&api_key=key123"
    r = client.get(callback, follow_redirects=False)
    assert r.status_code == 303
    return aid


def last_order():
    with SessionLocal() as db:
        return db.scalars(select(Order).order_by(Order.id.desc())).first()


def test_secret_and_token_are_encrypted_and_login_connects(client, fake_kite):
    signup(client)
    add_kite(client)
    assert "login needed" in client.get("/").text
    aid = kite_login(client)
    with SessionLocal() as db:
        account = db.get(Account, aid)
        assert account.status == "connected"
        assert "secret456" not in account.api_secret_enc and "day-token" not in account.access_token_enc
    page = client.get("/").text
    assert "secret456" not in page and "day-token" not in page and "login needed" not in page


def test_callback_without_started_login_or_with_cancel_does_nothing(client, fake_kite):
    signup(client)
    add_kite(client)
    client.get("/kite/callback?request_token=reqtoken&status=success")
    aid = account_id("Zerodha")
    client.get(f"/kite/{aid}/login", follow_redirects=False)
    client.get("/kite/callback?status=cancelled")
    with SessionLocal() as db:
        assert db.get(Account, aid).access_token_enc == ""


def test_alert_places_a_zerodha_order(client, fake_kite):
    token = signup(client)
    add_kite(client)
    kite_login(client)
    r = client.post(f"/hook/{token}", content='{"action":"buy","symbol":"NSE:RELIANCE","quantity":10,"product":"cnc"}')
    assert r.status_code == 200
    assert fake_kite.orders == [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "transaction_type": "BUY",
                                 "quantity": "10", "product": "CNC", "order_type": "MARKET", "validity": "DAY",
                                 "market_protection": "-1"}]
    order = last_order()
    assert (order.status, order.ticket) == ("placed", "25111")
    assert "placed" in client.get("/").text


def test_limit_order_and_exchange_field(client, fake_kite):
    token = signup(client)
    add_kite(client)
    kite_login(client)
    client.post(f"/hook/{token}", content='{"action":"sell","symbol":"NIFTY25NOVFUT","exchange":"nfo",'
                                          '"quantity":75,"product":"NRML","order_type":"limit","price":24500.5}')
    sent = fake_kite.orders[0]
    assert (sent["exchange"], sent["order_type"], sent["price"], sent["transaction_type"]) == \
           ("NFO", "LIMIT", "24500.5", "SELL")
    assert "market_protection" not in sent  # only market orders carry it
    r = client.post(f"/hook/{token}", content='{"action":"buy","symbol":"X","quantity":1,"order_type":"LIMIT"}')
    assert "price is required" in r.json()["error"]


def test_zerodha_rejections(client, fake_kite):
    token = signup(client)
    add_kite(client, max_quantity="5")
    post = lambda body: client.post(f"/hook/{token}", content=body).json()["orders"][0]
    assert "login needed for today" in post('{"action":"buy","symbol":"SBIN","quantity":1}')["message"]
    kite_login(client)
    assert "above this account's limit" in post('{"action":"buy","symbol":"SBIN","quantity":6}')["message"]
    r = client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","lots":1}')
    assert r.status_code == 400 and "no enabled account" in r.json()["error"]
    assert "sl and tp are not supported" in post('{"action":"buy","symbol":"SBIN","quantity":1,"sl":700}')["message"]
    assert fake_kite.orders == []


def test_broker_error_and_expired_token_are_reported(client, fake_kite):
    token = signup(client)
    add_kite(client)
    aid = kite_login(client)
    fake_kite.fail_with = "Insufficient funds"
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":1}')
    order = last_order()
    assert order.status == "failed" and "Insufficient funds" in order.message
    # Zerodha invalidates the token (e.g. the customer logged in elsewhere)
    fake_kite.fail_with = None
    with SessionLocal() as db:
        from app.security import encrypt
        db.get(Account, aid).access_token_enc = encrypt("stale")
        db.commit()
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":1}')
    assert last_order().status == "failed"
    assert "login needed" in client.get("/").text


def test_close_and_reverse_use_net_positions(client, fake_kite):
    token = signup(client)
    add_kite(client)
    kite_login(client)
    client.post(f"/hook/{token}", content='{"action":"close","symbol":"SBIN"}')
    assert fake_kite.orders == [] and "no open position" in last_order().message
    fake_kite.positions = [
        {"tradingsymbol": "SBIN", "exchange": "NSE", "product": "MIS", "quantity": -20},
        {"tradingsymbol": "INFY", "exchange": "NSE", "product": "MIS", "quantity": 5},
        {"tradingsymbol": "SBIN", "exchange": "NSE", "product": "CNC", "quantity": 0},
    ]
    client.post(f"/hook/{token}", content='{"action":"close","symbol":"SBIN"}')
    assert [(o["tradingsymbol"], o["transaction_type"], o["quantity"], o["product"]) for o in fake_kite.orders] == \
           [("SBIN", "BUY", "20", "MIS")]
    fake_kite.orders.clear()
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":10,"reverse":true}')
    assert [(o["transaction_type"], o["quantity"]) for o in fake_kite.orders] == [("BUY", "20"), ("BUY", "10")]
    fake_kite.orders.clear()
    client.post(f"/hook/{token}", content='{"action":"sell","symbol":"SBIN","quantity":10,"reverse":true}')
    assert [(o["transaction_type"], o["quantity"]) for o in fake_kite.orders] == [("SELL", "10")]


def test_one_webhook_routes_by_size_field_across_both_brokers(client, fake_kite, worker_headers):
    token = signup(client)
    add_account(client, "Main")
    add_kite(client)
    kite_aid = kite_login(client)
    mt5_aid = account_id("Main")
    r = client.post(f"/hook/{token}", content='{"action":"buy","symbol":"XAUUSD","lots":0.1}').json()["orders"]
    assert {o["account"]: o["status"] for o in r} == {"Main": "queued"}
    assert fake_kite.orders == []
    r = client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":1}')
    assert [o["account"] for o in r.json()["orders"]] == ["Zerodha"]
    # the Windows worker never sees the Zerodha account
    assert client.get("/worker/accounts", headers=worker_headers).json() == {"account_ids": [mt5_aid]}
    assert client.get(f"/worker/accounts/{kite_aid}", headers=worker_headers).status_code == 404
    assert client.post(f"/worker/accounts/{kite_aid}/claim", headers=worker_headers).status_code == 404


def test_token_expires_at_six_in_the_morning_ist():
    account = Account(access_token_enc="x", token_at=datetime(2026, 11, 12, 3, 0))  # 08:30 IST
    assert kite_session_valid(account, now=datetime(2026, 11, 12, 10, 0))            # 15:30 IST same day
    assert kite_session_valid(account, now=datetime(2026, 11, 13, 0, 0))             # 05:30 IST next day
    assert not kite_session_valid(account, now=datetime(2026, 11, 13, 1, 0))         # 06:30 IST next day
    assert not kite_session_valid(Account(access_token_enc="", token_at=None))


def test_callback_with_only_a_request_token_connects(client, fake_kite):
    # Zerodha's docs only promise request_token on the redirect.
    signup(client)
    add_kite(client)
    aid = kite_login(client, callback="/kite/callback?request_token=reqtoken")
    with SessionLocal() as db:
        assert db.get(Account, aid).status == "connected"


def test_wrong_secret_fails_the_login_without_storing_a_token(client, fake_kite):
    signup(client)
    client.post("/accounts/kite", data={"label": "Zerodha", "api_key": "key123", "api_secret": "wrong",
                                        "max_quantity": "1"})
    aid = kite_login(client)
    with SessionLocal() as db:
        account = db.get(Account, aid)
        assert account.access_token_enc == "" and "Invalid checksum" in account.status_message


def test_order_tag_is_made_alphanumeric(client, fake_kite):
    token = signup(client)
    add_kite(client)
    kite_login(client)
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":1,"comment":"my strat #2!"}')
    assert fake_kite.orders[0]["tag"] == "mystrat2"


def test_no_reply_from_zerodha_is_reported_as_unknown_not_as_unsent(client, fake_kite):
    token = signup(client)
    add_kite(client)
    kite_login(client)
    fake_kite.raise_on_order = httpx.ReadTimeout
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":1}')
    order = last_order()
    assert order.status == "failed" and "check your Zerodha order book" in order.message
    fake_kite.raise_on_order = httpx.ConnectError
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":1}')
    assert "this order was not sent" in last_order().message


def test_reverse_says_so_when_the_close_was_sent_but_the_new_order_failed(client, fake_kite):
    token = signup(client)
    add_kite(client)
    kite_login(client)
    fake_kite.positions = [{"tradingsymbol": "SBIN", "exchange": "NSE", "product": "MIS", "quantity": -20}]
    fake_kite.fail_when = lambda form: "Insufficient funds" if form["quantity"] == "10" else None
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"SBIN","quantity":10,"reverse":true}')
    order = last_order()
    assert order.status == "failed"
    assert order.message == "1 closing order(s) were sent, then this failed: Insufficient funds"
    assert len(fake_kite.orders) == 1


def test_orders_nobody_picked_up_stop_showing_as_queued(client, fake_kite):
    token = signup(client)
    add_account(client, "Main")
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"XAUUSD","lots":0.1}')
    assert "queued" in client.get("/").text
    with SessionLocal() as db:
        order = db.scalar(select(Order))
        order.created_at = utcnow() - timedelta(minutes=10)
        db.commit()
    assert "not executed in time" in client.get("/").text
    assert last_order().status == "expired"
