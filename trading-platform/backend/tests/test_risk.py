"""Risk limits and kill switches. Every rule has a test that shows it rejecting, with its reason."""

from datetime import UTC, datetime, timedelta

from app.trading import risk
from tests.helpers import staff_member
from tests.trading_helpers import alert, now, order_rows, position_rows, setup_paper, work


def last_order():
    return order_rows()[-1]


def test_max_order_quantity_and_lots():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", max_order_quantity="5")
    alert(trader.browser, hook["url"], signal_id="1", quantity=6)
    alert(trader.browser, hook["url"], signal_id="2", quantity=5)
    work()
    big, ok = order_rows()
    assert big.state == "REJECTED" and big.rule == "max_order_quantity" and "above the limit of 5" in big.reason
    assert ok.state == "FILLED"


def test_most_restrictive_scope_wins():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", max_order_quantity="100")
    trader.limits(f"account:{account['id']}", max_order_quantity="50")
    trader.limits(f"strategy:{strategy['id']}", max_order_quantity="20")
    alert(trader.browser, hook["url"], signal_id="1", quantity=21)
    work()
    assert last_order().rule == "max_order_quantity" and "limit of 20" in last_order().reason


def test_max_open_positions_counts_new_symbols_only():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", max_open_positions=1)
    alert(trader.browser, hook["url"], signal_id="1", symbol="TCS", quantity=1)
    work()
    alert(trader.browser, hook["url"], signal_id="2", symbol="INFY", quantity=1)
    alert(trader.browser, hook["url"], signal_id="3", symbol="TCS", quantity=1)  # adding to an open one is fine
    work()
    _, infy, tcs = order_rows()
    assert infy.rule == "max_open_positions" and tcs.state == "FILLED"


def test_max_daily_trades():
    trader, account, strategy, hook = setup_paper()
    trader.limits(f"account:{account['id']}", max_daily_trades=2)
    for n in range(3):
        alert(trader.browser, hook["url"], signal_id=f"t{n}", quantity=1)
        work()
    assert [o.state for o in order_rows()] == ["FILLED", "FILLED", "REJECTED"]
    assert last_order().rule == "max_daily_trades"


def test_daily_loss_limit_locks_new_entries_but_never_blocks_closing():
    trader, account, strategy, hook = setup_paper()
    trader.ok(trader.browser.patch(f"/broker-accounts/{account['id']}", json={"settings": {"slippage_pct": "0"}}))
    trader.limits("global", max_daily_loss="400")
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=100)
    alert(trader.browser, hook["url"], signal_id="2", action="CLOSE", quantity=5, price=50)  # loses 250
    work()
    alert(trader.browser, hook["url"], signal_id="3", action="CLOSE", quantity=None, price=0.01)  # loses ~250 more
    work()
    assert position_rows()[0].quantity == 0
    alert(trader.browser, hook["url"], signal_id="4", quantity=1, price=100)
    work()
    locked = last_order()
    assert locked.state == "REJECTED" and locked.rule == "daily_loss_limit" and "locked" in locked.reason


def test_exposure_limit_and_it_needs_a_price():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", max_exposure="10000")
    alert(trader.browser, hook["url"], signal_id="1", quantity=50, price=100)  # 5,000
    work()
    alert(trader.browser, hook["url"], signal_id="2", quantity=60, price=100)  # would be 11,000
    work()
    assert order_rows()[0].state == "FILLED" and last_order().rule == "max_exposure"


def test_allowed_instruments():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", allowed_symbols=["nse:tcs", "INFY"])
    alert(trader.browser, hook["url"], signal_id="1", symbol="RELIANCE", quantity=1)
    alert(trader.browser, hook["url"], signal_id="2", symbol="TCS", quantity=1)
    work()
    reliance, tcs = order_rows()
    assert reliance.rule == "symbol_not_allowed" and tcs.state == "FILLED"


def test_trading_sessions():
    trader, account, strategy, hook = setup_paper()
    current = now()
    closed_hour = (current + timedelta(hours=3)).strftime("%H:00")
    closed_end = (current + timedelta(hours=4)).strftime("%H:00")
    trader.limits("global", trading_sessions=[{"days": [0, 1, 2, 3, 4, 5, 6], "start": closed_hour,
                                               "end": closed_end, "timezone": "UTC"}])
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    work()
    assert last_order().rule == "outside_trading_session"
    bad = trader.browser.put("/risk/limits/global", json={"trading_sessions": [{"days": [9], "start": "9", "end": "x"}]})
    assert bad.status_code == 422


def test_signal_age_limit_per_account_applies_when_orders_are_planned():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, timestamp=(now() - timedelta(seconds=20)).isoformat())
    trader.limits(f"account:{account['id']}", max_signal_age_seconds=10)
    work()
    assert last_order().rule == "signal_too_old"


def test_minimum_available_margin():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", min_available_margin="2000000")
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    work()
    assert last_order().rule == "min_available_margin" and "below the minimum" in last_order().reason


def test_slippage_limit_is_passed_to_execution():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", max_slippage_pct="0.01")  # the paper account simulates 0.05%
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    work()
    order = last_order()
    assert order.state == "REJECTED" and order.rule == "broker_rejected" and "slippage" in order.reason


def test_account_kill_switch_and_pause():
    trader, account, strategy, hook = setup_paper()
    trader.ok(trader.browser.post(f"/broker-accounts/{account['id']}/kill-switch",
                                  json={"enabled": True, "reason": "testing"}))
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    work()
    assert last_order().rule == "account_kill_switch" and "testing" in last_order().reason
    trader.ok(trader.browser.post(f"/broker-accounts/{account['id']}/kill-switch", json={"enabled": False}))
    trader.ok(trader.browser.patch(f"/broker-accounts/{account['id']}", json={"status": "paused"}))
    alert(trader.browser, hook["url"], signal_id="2", quantity=1)
    work()
    assert last_order().rule == "account_paused"


def test_strategy_kill_switch_and_pause():
    trader, account, strategy, hook = setup_paper()
    trader.ok(trader.browser.post(f"/strategies/{strategy['id']}/kill-switch", json={"enabled": True}))
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    work()
    assert last_order().rule == "strategy_kill_switch"
    trader.ok(trader.browser.post(f"/strategies/{strategy['id']}/kill-switch", json={"enabled": False}))
    trader.ok(trader.browser.patch(f"/strategies/{strategy['id']}", json={"status": "paused"}))
    alert(trader.browser, hook["url"], signal_id="2", quantity=1)
    work()
    signal = trader.ok(trader.browser.get("/signals"))[0]
    assert signal["status"] == "ignored" and "paused" in signal["reason"] and signal["order_count"] == 0


def test_global_kill_switch_stops_everything_including_queued_orders():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    from app.core.db import new_session
    from app.trading import engine

    db = new_session()
    engine.process_pending_signals(db)  # queued, not yet executed
    db.close()
    assert last_order().state == "QUEUED"
    trader.ok(trader.browser.post("/risk/kill-switch", json={"enabled": True, "reason": "stop now"}))
    work()
    assert last_order().state == "REJECTED" and last_order().rule == "global_kill_switch"
    alert(trader.browser, hook["url"], signal_id="2", quantity=1)
    work()
    assert last_order().rule == "global_kill_switch"
    assert trader.ok(trader.browser.get("/risk"))["kill_switch"]["enabled"] is True


def test_platform_halt_by_a_super_admin():
    trader, account, strategy, hook = setup_paper()
    support = staff_member("support@example.com", "support_admin")
    assert support.put("/admin/trading-halt", json={"halted": True}).status_code == 404  # super admins only
    root = staff_member("root@example.com", "super_admin")
    assert root.put("/admin/trading-halt", json={"halted": True, "reason": "exchange incident"}).status_code == 200
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    work()
    assert last_order().rule == "platform_halted" and "exchange incident" in last_order().reason
    assert trader.ok(trader.browser.get("/risk"))["platform_halted"] is True
    assert root.put("/admin/trading-halt", json={"halted": False}).status_code == 200
    alert(trader.browser, hook["url"], signal_id="2", quantity=1)
    work()
    assert last_order().state == "FILLED"


def test_live_accounts_are_refused_while_live_trading_is_off():
    trader, account, strategy, hook = setup_paper()
    live = trader.account("kite", "Zerodha", api_key="key12345")
    trader.mapping(live, alert_symbol="RELIANCE", broker_symbol="RELIANCE", exchange="NSE", product="MIS")
    trader.link(strategy, live)
    alert(trader.browser, hook["url"], signal_id="1", quantity=1)
    work()
    rules = {o.is_paper: (o.state, o.rule) for o in order_rows()}
    assert rules[True] == ("FILLED", None)
    assert rules[False] == ("REJECTED", "live_trading_disabled")


def test_kite_market_hours():
    ist = timedelta(hours=5, minutes=30)
    friday_morning = datetime(2026, 11, 13, 10, 0, tzinfo=UTC) - ist
    friday_evening = datetime(2026, 11, 13, 16, 0, tzinfo=UTC) - ist
    saturday = datetime(2026, 11, 14, 11, 0, tzinfo=UTC) - ist
    assert risk.kite_market_closed("NSE", friday_morning) is None
    assert "closed" in risk.kite_market_closed("NSE", friday_evening)
    assert risk.kite_market_closed("MCX", friday_evening) is None  # commodities trade into the evening
    assert "Sat" in risk.kite_market_closed("NFO", saturday)


def test_risk_changes_are_audited():
    trader, account, strategy, hook = setup_paper()
    trader.limits("global", max_daily_loss="1000")
    trader.ok(trader.browser.post("/risk/kill-switch", json={"enabled": True}))
    actions = [a["action"] for a in trader.ok(trader.browser.get("/users/me/activity"))]
    assert "risk.limits.updated" in actions and "risk.kill_switch.global" in actions
    assert trader.browser.put("/risk/limits/account:00000000-0000-0000-0000-000000000000", json={}).status_code == 404
