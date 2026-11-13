"""The MetaTrader terminal bridge protocol, and tenant isolation for all trading data."""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import new_session, set_access_context, tenant_context
from tests.helpers import user_id
from tests.trading_helpers import Trader, alert, now, order_rows, position_rows, setup_paper, work


def bridge(trader: Trader, token: str, path: str, **form):
    response = trader.browser.http.post(
        f"/api/v1/bridge/{path}", content=urlencode(form),
        headers={"X-Device-Token": token, "Content-Type": "application/x-www-form-urlencoded"},
    )
    lines = dict(line.split("=", 1) for line in response.text.strip().splitlines() if "=" in line)
    return response.status_code, lines


@pytest.fixture
def mt5(monkeypatch):
    monkeypatch.setattr(get_settings(), "live_trading_enabled", True)
    trader = Trader("mt5@example.com")
    account = trader.account("mt5", "MT5 demo", symbol_suffix="m")
    strategy = trader.strategy()
    trader.link(strategy, account)
    hook = trader.webhook(strategy)
    paired = trader.ok(trader.browser.post(f"/broker-accounts/{account['id']}/bridge/pair"))
    return trader, account, hook, paired["device_token"]


def heartbeat(trader, token, **extra):
    return bridge(trader, token, "poll", login="5001", server="Broker-Demo", currency="USD", balance="10000",
                  equity="10050", margin_free="9000", **extra)


def test_pairing_shows_the_token_once_and_the_terminal_must_be_online(mt5):
    trader, account, hook, token = mt5
    assert token.startswith("tbd_")
    assert "device_token" not in trader.ok(trader.browser.get(f"/broker-accounts/{account['id']}"))
    alert(trader.browser, hook["url"], signal_id="offline", symbol="XAUUSD", quantity="0.10", price=2650)
    work()
    assert order_rows()[0].rule == "broker_not_connected"  # never heard from the terminal
    status, reply = heartbeat(trader, token)
    assert (status, reply) == (200, {"type": "idle"})
    details = trader.ok(trader.browser.get(f"/broker-accounts/{account['id']}"))
    assert details["connection_status"] == "connected" and details["terminal_online"] is True
    assert details["equity"] == 10050 and details["currency"] == "USD"
    assert bridge(trader, "tbd_wrong", "poll")[0] == 401


def test_an_order_goes_to_the_terminal_once_and_its_fill_is_recorded(mt5):
    trader, account, hook, token = mt5
    heartbeat(trader, token)
    alert(trader.browser, hook["url"], signal_id="1", symbol="XAUUSD", quantity="0.10", price=2650, stop_loss=2640)
    work()
    assert order_rows()[0].state == "QUEUED"  # waits for the terminal, not for a server worker
    status, instruction = heartbeat(trader, token)
    assert instruction["type"] == "order" and instruction["symbol"] == "XAUUSDm"
    assert (instruction["side"], instruction["volume"], instruction["sl"]) == ("BUY", "0.1", "2640")
    assert heartbeat(trader, token)[1] == {"type": "idle"}  # handed out once
    assert order_rows()[0].state == "EXECUTING"
    status, ack = bridge(trader, token, "result", id=instruction["id"], status="filled", ticket="9001", deal="777",
                         price="2650.35", volume="0.10")
    assert ack == {"status": "ok"}
    order = order_rows()[0]
    assert order.state == "FILLED" and order.broker_order_id == "9001" and order.average_price == Decimal("2650.35")
    assert position_rows()[0].quantity == Decimal("0.1") and position_rows()[0].stop_loss == 2640
    # A repeated report changes nothing.
    assert bridge(trader, token, "result", id=instruction["id"], status="filled", deal="777", price="2650.35",
                  volume="0.10")[1]["status"] == "ok"
    assert order_rows()[0].filled_quantity == Decimal("0.1")


def test_a_lost_terminal_reply_is_looked_up_in_the_terminal_history(mt5):
    trader, account, hook, token = mt5
    heartbeat(trader, token)
    alert(trader.browser, hook["url"], signal_id="1", symbol="EURUSD", quantity="0.20", price="1.08")
    work()
    _, instruction = heartbeat(trader, token)  # the terminal executes it, then crashes before reporting
    work(at=now() + timedelta(seconds=45))
    assert order_rows()[0].state == "UNKNOWN"
    _, lookup = heartbeat(trader, token)
    assert lookup == {"type": "lookup", "id": instruction["id"], "magic": lookup["magic"]}
    bridge(trader, token, "result", id=instruction["id"], status="filled", deal="88", price="1.0801", volume="0.20")
    assert order_rows()[0].state == "FILLED"


def test_a_lookup_that_finds_nothing_marks_the_order_failed_not_resent(mt5):
    trader, account, hook, token = mt5
    heartbeat(trader, token)
    alert(trader.browser, hook["url"], signal_id="1", symbol="EURUSD", quantity="0.20", price="1.08")
    work()
    _, instruction = heartbeat(trader, token)
    work(at=now() + timedelta(seconds=45))
    _, lookup = heartbeat(trader, token)
    assert bridge(trader, token, "result", id=lookup["id"], status="not_found")[1]["status"] == "ok"
    order = order_rows()[0]
    assert order.state == "FAILED" and "not executed" in order.reason
    assert heartbeat(trader, token)[1] == {"type": "idle"}


def test_terminal_rejections_and_stale_orders(mt5):
    trader, account, hook, token = mt5
    heartbeat(trader, token)
    alert(trader.browser, hook["url"], signal_id="1", symbol="XAUUSD", quantity="0.10", price=2650)
    work()
    _, instruction = heartbeat(trader, token)
    bridge(trader, token, "result", id=instruction["id"], status="rejected", message="Market is closed (10018)")
    order = order_rows()[0]
    assert order.state == "REJECTED" and "Market is closed" in order.reason
    alert(trader.browser, hook["url"], signal_id="2", symbol="XAUUSD", quantity="0.10", price=2650)
    work()
    # The terminal is busy for two minutes; the queued order is too old to trade by then.
    work(at=now() + timedelta(minutes=2))
    assert order_rows()[-1].state == "EXPIRED"


def test_terminal_positions_are_kept_for_reconciliation(mt5):
    trader, account, hook, token = mt5
    heartbeat(trader, token)
    alert(trader.browser, hook["url"], signal_id="1", symbol="XAUUSD", quantity="0.10", price=2650)
    work()
    _, instruction = heartbeat(trader, token)
    bridge(trader, token, "result", id=instruction["id"], status="filled", deal="1", price="2650", volume="0.10")
    heartbeat(trader, token, positions="XAUUSDm:0.10;GBPUSDm:-0.50")
    positions = {p["symbol"]: p for p in trader.ok(trader.browser.get("/positions"))}
    assert positions["XAUUSDm"]["mismatch"] is False
    assert positions["GBPUSDm"]["mismatch"] is True and positions["GBPUSDm"]["broker_quantity"] == -0.5


# ------------------------------------------------------------- isolation


def test_one_user_cannot_see_or_touch_another_users_trading_data():
    alice, account, strategy, hook = setup_paper("alice@example.com")
    alert(alice.browser, hook["url"], signal_id="1", quantity=1, price=100)
    work()
    order = order_rows()[0]
    bob = Trader("bob@example.com")
    bob_strategy = bob.strategy("BOB")
    for path in (f"/broker-accounts/{account['id']}", f"/strategies/{strategy['id']}", f"/orders/{order.id}",
                 f"/signals/{order.signal_id}", f"/broker-accounts/{account['id']}/instruments"):
        assert bob.browser.get(path).status_code == 404, path
    for method, path, body in (
        ("POST", f"/broker-accounts/{account['id']}/kill-switch", {"enabled": True}),
        ("POST", f"/broker-accounts/{account['id']}/close-all", None),
        ("PATCH", f"/broker-accounts/{account['id']}", {"label": "mine now"}),
        ("POST", f"/webhooks/{hook['webhook']['id']}/rotate", None),
        ("POST", f"/orders/{order.id}/cancel", None),
        ("PUT", f"/strategies/{bob_strategy['id']}/accounts/{account['id']}", {"sizing_mode": "signal"}),
        ("PUT", f"/risk/limits/account:{account['id']}", {"max_order_quantity": "1"}),
    ):
        assert bob.browser.request(method, path, json=body).status_code == 404, path
    assert bob.ok(bob.browser.get("/orders")) == [] and bob.ok(bob.browser.get("/positions")) == []
    assert bob.ok(bob.browser.get("/signals")) == [] and bob.ok(bob.browser.get("/webhooks")) == []
    assert bob.ok(bob.browser.get("/overview"))["paper"]["orders_today"] == 0
    assert alice.ok(alice.browser.get(f"/broker-accounts/{account['id']}"))["kill_switch"] is False


def test_the_database_refuses_cross_tenant_trading_rows():
    alice, account, strategy, hook = setup_paper("alice@example.com")
    alert(alice.browser, hook["url"], signal_id="1", quantity=1, price=100)
    work()
    bob = Trader("bob@example.com")
    bob_strategy = bob.strategy("BOB")
    alice_id, bob_id = user_id("alice@example.com"), user_id("bob@example.com")
    db = new_session()
    try:
        set_access_context(db, tenant_context(bob_id))
        for table in ("broker_accounts", "orders", "signals", "fills", "positions", "webhooks", "order_events"):
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
        assert db.execute(text("UPDATE broker_accounts SET kill_switch = true")).rowcount == 0
        db.rollback()
        # Bob cannot link his strategy to Alice's account, even with raw SQL.
        with pytest.raises(DBAPIError):
            db.execute(text("INSERT INTO strategy_accounts (user_id, strategy_id, broker_account_id) "
                            "VALUES (:u, :s, :a)"), {"u": bob_id, "s": bob_strategy["id"], "a": account["id"]})
        db.rollback()
        # Nor write fills or signals at all: only the trusted system code does that.
        with pytest.raises(DBAPIError, match="row-level security|same user"):
            db.execute(text("INSERT INTO positions (user_id, broker_account_id, symbol, is_paper) "
                            "VALUES (:u, :a, 'X', true)"), {"u": bob_id, "a": account["id"]})
        db.rollback()
        set_access_context(db, tenant_context(alice_id))
        with pytest.raises(DBAPIError, match="same user"):
            db.execute(text("INSERT INTO strategy_accounts (user_id, strategy_id, broker_account_id) "
                            "VALUES (:u, :s, :a)"), {"u": alice_id, "s": bob_strategy["id"], "a": account["id"]})
        db.rollback()
        with pytest.raises(DBAPIError, match="owner of a row cannot be changed"):
            db.execute(text("UPDATE orders SET user_id = :b"), {"b": bob_id})
        db.rollback()
        with pytest.raises(DBAPIError, match="append-only|permission denied"):
            db.execute(text("DELETE FROM order_events"))
    finally:
        db.rollback()
        db.close()


def test_staff_can_see_trading_status_but_never_broker_credentials():
    from app.core.db import staff_context
    from tests.helpers import staff_member

    alice, account, strategy, hook = setup_paper("alice@example.com")
    zerodha = alice.account("kite", "Zerodha", api_key="abcd1234")
    alice.ok(alice.browser.put(f"/broker-accounts/{zerodha['id']}/kite/credentials",
                               json={"api_key": "abcd1234", "api_secret": "very-secret"}))
    staff_member("support@example.com", "support_admin")
    db = new_session()
    try:
        set_access_context(db, staff_context(user_id("support@example.com")))
        assert db.execute(text("SELECT count(*) FROM broker_accounts")).scalar() == 2
        assert db.execute(text("SELECT count(*) FROM broker_credentials")).scalar() == 0
        assert db.execute(text("UPDATE broker_accounts SET kill_switch = true")).rowcount == 0
    finally:
        db.rollback()
        db.close()


def test_a_webhook_token_only_ever_reaches_its_own_strategy_and_accounts():
    alice, alice_account, alice_strategy, alice_hook = setup_paper("alice@example.com")
    bob = Trader("bob@example.com")
    bob_account = bob.account("paper", "Bob paper")
    bob_strategy = bob.strategy("STRAT_1")  # same code as Alice's
    bob.link(bob_strategy, bob_account)
    alert(alice.browser, alice_hook["url"], signal_id="1", quantity=1, price=100)
    work()
    [order] = order_rows()
    assert str(order.broker_account_id) == alice_account["id"]
