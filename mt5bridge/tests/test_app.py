from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import MT5Account, Order, User, utcnow
from tests.fake_mt5 import FakeMT5
from worker.executor import MT5Executor
from worker.runner import Api, process_once, run


def signup(client, email="a@example.com"):
    r = client.post("/signup", data={"email": email, "password": "password123"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.email == email)).webhook_token


def add_account(client, label="Main", max_lots="1", suffix=""):
    r = client.post(
        "/accounts",
        data={"label": label, "login": "12345678", "server": "Broker-Demo", "password": "mt5pass",
              "max_lots": max_lots, "symbol_suffix": suffix},
        follow_redirects=False,
    )
    assert r.status_code == 303


def account_id(label="Main"):
    with SessionLocal() as db:
        return db.scalar(select(MT5Account).where(MT5Account.label == label)).id


def test_dashboard_requires_login(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_signup_login_and_dashboard(client):
    token = signup(client)
    page = client.get("/")
    assert page.status_code == 200 and token in page.text
    client.post("/logout")
    assert client.post("/login", data={"email": "a@example.com", "password": "wrong"},
                       follow_redirects=False).headers["location"] == "/login"
    assert client.post("/login", data={"email": "a@example.com", "password": "password123"},
                       follow_redirects=False).headers["location"] == "/"


def test_mt5_password_is_stored_encrypted_and_never_shown(client):
    signup(client)
    add_account(client)
    with SessionLocal() as db:
        stored = db.scalar(select(MT5Account)).password_enc
    assert "mt5pass" not in stored
    assert "mt5pass" not in client.get("/").text


def test_webhook_queues_an_order_per_enabled_account(client):
    token = signup(client)
    add_account(client, "Main", suffix="m")
    add_account(client, "Second")
    client.post(f"/accounts/{account_id('Second')}/toggle")
    # TradingView sends the message as text, with an exchange prefix on {{ticker}}
    r = client.post(f"/hook/{token}", content='{"action":"BUY","symbol":"OANDA:XAUUSD","lots":0.1}',
                    headers={"content-type": "text/plain"})
    assert r.status_code == 200
    orders = r.json()["orders"]
    assert len(orders) == 1 and orders[0]["account"] == "Main" and orders[0]["status"] == "queued"
    with SessionLocal() as db:
        assert db.scalar(select(Order)).symbol == "XAUUSDm"


def test_webhook_rejections_are_explained(client):
    token = signup(client)
    assert client.post("/hook/nope", content="{}").status_code == 404
    r = client.post(f"/hook/{token}", content='{"action":"buy","symbol":"XAUUSD","lots":0.1}')
    assert r.status_code == 400 and "no enabled MT5 account" in r.json()["error"]
    add_account(client)
    assert "not valid JSON" in client.post(f"/hook/{token}", content="buy gold").json()["error"]
    assert "lots is required" in client.post(
        f"/hook/{token}", content='{"action":"buy","symbol":"XAUUSD"}').json()["error"]
    assert "action" in client.post(
        f"/hook/{token}", content='{"action":"hold","symbol":"XAUUSD","lots":1}').json()["error"]
    assert "Alerts that were refused" in client.get("/").text


def test_lot_limit_rejects_the_order(client):
    token = signup(client)
    add_account(client, max_lots="0.5")
    r = client.post(f"/hook/{token}", content='{"action":"sell","symbol":"XAUUSD","lots":2}')
    assert r.json()["orders"][0]["status"] == "rejected"


def test_account_filter_and_other_users_accounts(client):
    token = signup(client)
    add_account(client, "Main")
    add_account(client, "Second")
    r = client.post(f"/hook/{token}", content='{"action":"close","symbol":"XAUUSD","account":"second"}')
    assert [o["account"] for o in r.json()["orders"]] == ["Second"]
    main_id = account_id("Main")
    client.post("/logout")
    signup(client, "b@example.com")
    assert client.post(f"/accounts/{main_id}/delete").status_code == 404


def test_worker_api_needs_the_key(client):
    assert client.get("/worker/accounts").status_code == 401
    assert client.get("/worker/accounts", headers={"X-Worker-Key": "wrong"}).status_code == 401


def test_orders_are_claimed_once_and_stale_ones_expire(client, worker_headers):
    token = signup(client)
    add_account(client)
    aid = account_id()
    for _ in range(2):
        client.post(f"/hook/{token}", content='{"action":"buy","symbol":"XAUUSD","lots":0.1}')
    with SessionLocal() as db:
        old = db.scalars(select(Order).order_by(Order.id)).first()
        old.created_at = utcnow() - timedelta(minutes=10)
        db.commit()
        old_id = old.id
    first = client.post(f"/worker/accounts/{aid}/claim", headers=worker_headers).json()["orders"]
    assert len(first) == 1 and first[0]["id"] != old_id
    assert client.post(f"/worker/accounts/{aid}/claim", headers=worker_headers).json()["orders"] == []
    with SessionLocal() as db:
        assert db.get(Order, old_id).status == "expired"
    done = {"ok": True, "message": "ok", "ticket": "1", "price": 1.0}
    assert client.post(f"/worker/orders/{first[0]['id']}/result", json=done, headers=worker_headers).status_code == 200
    assert client.post(f"/worker/orders/{first[0]['id']}/result", json=done, headers=worker_headers).status_code == 409


def test_alert_to_fill_end_to_end_with_the_runner(client, worker_headers):
    token = signup(client)
    add_account(client)
    aid = account_id()
    api = Api(client, "", "test-worker-key")
    mt5 = FakeMT5()
    executor = MT5Executor(mt5)

    creds = api.account(aid)
    assert creds["password"] == "mt5pass" and creds["login"] == "12345678"
    api.status(aid, executor.health())

    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"XAUUSD","lots":0.1,"sl":2640}')
    client.post(f"/hook/{token}", content='{"action":"buy","symbol":"NOPE","lots":0.1}')
    assert process_once(api, executor, aid) == 2
    assert process_once(api, executor, aid) == 0

    with SessionLocal() as db:
        filled, failed = db.scalars(select(Order).order_by(Order.id)).all()
        assert (filled.status, filled.price, filled.ticket) == ("filled", 2650.5, "1001")
        assert failed.status == "failed" and "does not exist" in failed.message
    page = client.get("/").text
    assert "connected" in page and "1000.00" in page and "filled" in page


def test_runner_stops_for_paused_account_and_reports_login_failure(client, worker_headers):
    signup(client)
    add_account(client)
    aid = account_id()
    api = Api(client, "", "test-worker-key")
    mt5 = FakeMT5()
    mt5.login_ok = False
    assert run(api, MT5Executor(mt5), aid, "C:/t/terminal64.exe") == 1
    assert mt5.init_kwargs["login"] == 12345678 and mt5.init_kwargs["portable"] is True
    with SessionLocal() as db:
        account = db.get(MT5Account, aid)
        assert account.status == "error" and "could not log in" in account.status_message
    client.post(f"/accounts/{aid}/toggle")
    assert run(api, MT5Executor(FakeMT5()), aid, "x") == 0
    assert client.get("/worker/accounts", headers=worker_headers).json() == {"account_ids": []}
