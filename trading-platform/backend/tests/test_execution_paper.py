"""Order placement on paper accounts: fills, positions, sizing, approval, multi-account and recovery."""

from datetime import timedelta
from decimal import Decimal

from app.core.db import SYSTEM, access, new_session
from app.models.trading import Order
from app.trading import engine
from tests.trading_helpers import alert, fill_rows, now, order_rows, position_rows, setup_paper, work


def D(value) -> Decimal:
    return Decimal(str(value))


def test_market_buy_fills_at_the_alert_price_plus_slippage_and_opens_a_position():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="b1", quantity=10, price=2500)
    work()
    [order] = order_rows()
    assert order.state == "FILLED" and order.side == "BUY" and order.quantity == 10
    # default paper slippage is 0.05% against you
    assert order.average_price == D("2501.25")
    [fill] = fill_rows()
    assert fill.is_paper and fill.quantity == 10
    [position] = position_rows()
    assert position.quantity == 10 and position.average_price == D("2501.25")
    events = [e["to"] for e in trader.ok(trader.browser.get(f"/orders/{order.id}"))["events"]]
    assert events == ["VALIDATED", "QUEUED", "EXECUTING", "FILLED"]


def test_close_realises_profit_and_fees_reduce_the_paper_balance():
    trader, account, strategy, hook = setup_paper()
    trader.ok(
        trader.browser.patch(
            f"/broker-accounts/{account['id']}", json={"settings": {"slippage_pct": "0", "fee_per_order": "20"}}
        )
    )
    alert(trader.browser, hook["url"], signal_id="b1", quantity=10, price=100)
    alert(trader.browser, hook["url"], signal_id="c1", action="CLOSE", quantity=None, price=110)
    work()
    buy, close = order_rows()
    assert close.intent == "close" and close.side == "SELL" and close.quantity == 10 and close.state == "FILLED"
    assert fill_rows(close.id == close.id)
    sell_fill = [f for f in fill_rows() if f.order_id == close.id][0]
    assert sell_fill.realized_pnl == D(100)  # (110 - 100) * 10
    assert position_rows()[0].quantity == 0
    refreshed = trader.ok(trader.browser.get(f"/broker-accounts/{account['id']}"))
    assert D(refreshed["balance"]) == D(1000000) + D(100) - D(40)


def test_partial_close_and_reversal_through_zero():
    trader, account, strategy, hook = setup_paper()
    trader.ok(trader.browser.patch(f"/broker-accounts/{account['id']}", json={"settings": {"slippage_pct": "0"}}))
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=100)
    alert(trader.browser, hook["url"], signal_id="2", action="CLOSE", quantity=4, price=105)
    work()
    assert position_rows()[0].quantity == 6
    alert(trader.browser, hook["url"], signal_id="3", action="SELL", quantity=10, price=90)
    work()
    [position] = position_rows()
    assert position.quantity == -4 and position.average_price == 90  # reversed: the rest opened short at 90
    assert position.realized_pnl == D(20) + D(-60)  # +5*4, then -10*6


def test_close_with_no_position_is_cancelled_and_exit_side_filters_apply():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", action="CLOSE", quantity=None)
    work()
    assert order_rows()[0].state == "CANCELLED" and "no open position" in order_rows()[0].reason
    alert(trader.browser, hook["url"], signal_id="2", quantity=5, price=100)
    alert(trader.browser, hook["url"], signal_id="3", action="EXIT_SHORT", quantity=None, price=100)
    work()
    assert order_rows()[-1].state == "CANCELLED"  # the position is long, EXIT_SHORT does nothing
    assert position_rows()[0].quantity == 5


def test_limit_order_rests_until_a_later_alert_price_reaches_it():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", order_type="LIMIT", price=95, quantity=3)
    work()
    [limit] = order_rows()
    assert limit.state == "SUBMITTED"  # 95 is a limit price; no market price seen yet below it
    alert(
        trader.browser,
        hook["url"],
        signal_id="2",
        action="MODIFY_TP",
        symbol="RELIANCE",
        take_profit=200,
        price=97,
        quantity=None,
    )
    work()
    assert order_rows(Order.id == limit.id)[0].state == "SUBMITTED"
    alert(trader.browser, hook["url"], signal_id="3", action="MODIFY_TP", take_profit=200, price=94, quantity=None)
    work()
    filled = order_rows(Order.id == limit.id)[0]
    assert filled.state == "FILLED" and filled.average_price == 94


def test_cancel_order_alert_cancels_resting_orders():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", order_type="LIMIT", price=90, quantity=3)
    alert(trader.browser, hook["url"], signal_id="2", action="CANCEL_ORDER", quantity=None, price=None)
    work()
    limit, cancel = order_rows()
    assert (limit.state, cancel.state) == ("CANCELLED", "APPLIED")


def test_stop_loss_on_the_position_triggers_on_a_later_price_and_gaps_fill_at_the_worse_price():
    trader, account, strategy, hook = setup_paper()
    trader.ok(trader.browser.patch(f"/broker-accounts/{account['id']}", json={"settings": {"slippage_pct": "0"}}))
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=100, stop_loss=95, take_profit=120)
    work()
    assert position_rows()[0].stop_loss == 95
    # The next alert for the symbol carries price 93: below the stop, so the stop fills at 93.
    alert(trader.browser, hook["url"], signal_id="2", action="MODIFY_TP", take_profit=125, price=93, quantity=None)
    work()
    protective = [o for o in order_rows() if o.source == "protective"]
    assert len(protective) == 1 and protective[0].state == "FILLED" and protective[0].average_price == 93
    assert position_rows()[0].quantity == 0


def test_modify_stop_loss_updates_the_simulated_position():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=10, price=100, stop_loss=90)
    alert(trader.browser, hook["url"], signal_id="2", action="MODIFY_SL", stop_loss=98, quantity=None, price=None)
    work()
    assert order_rows()[-1].state == "APPLIED"
    assert position_rows()[0].stop_loss == 98


def test_close_all_creates_one_close_order_per_open_position():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", symbol="TCS", quantity=2, price=3900)
    alert(trader.browser, hook["url"], signal_id="2", symbol="INFY", quantity=3, price=1500, action="SELL")
    work()
    alert(trader.browser, hook["url"], signal_id="3", action="CLOSE_ALL", symbol=None, quantity=None, price=None)
    work()
    closes = [o for o in order_rows() if o.intent == "close"]
    assert sorted((o.broker_symbol, o.side, o.state) for o in closes) == [
        ("INFY", "BUY", "FILLED"),
        ("TCS", "SELL", "FILLED"),
    ]
    assert {o.leg for o in closes} == {0, 1}
    assert all(p.quantity == 0 for p in position_rows())


def test_sizing_modes():
    trader, account, strategy, hook = setup_paper(sizing_mode="fixed", sizing_value="7")
    alert(trader.browser, hook["url"], signal_id="1", quantity=999, price=100)
    work()
    assert order_rows()[-1].quantity == 7  # the account's fixed size, not the alert's

    trader.link(strategy, account, sizing_mode="percent_capital", sizing_value="1")
    alert(trader.browser, hook["url"], signal_id="2", quantity=None, price=1000)
    work()
    equity = D(trader.ok(trader.browser.get(f"/broker-accounts/{account['id']}"))["equity"])
    assert equity > 0 and order_rows()[-1].quantity == 10  # 1% of 1,000,000 equity at 1000 per share

    trader.link(strategy, account, sizing_mode="risk_per_trade", sizing_value="500")
    alert(trader.browser, hook["url"], signal_id="3", quantity=None, price=100, stop_loss=96)
    work()
    assert order_rows()[-1].quantity == 125  # 500 risk / 4 per unit

    alert(trader.browser, hook["url"], signal_id="4", quantity=None, price=100)
    work()
    rejected = order_rows()[-1]
    assert rejected.state == "REJECTED" and "needs a stop_loss" in rejected.reason


def test_lot_size_and_tick_size_are_enforced_without_changing_the_order():
    trader, account, strategy, hook = setup_paper()
    trader.mapping(account, alert_symbol="NIFTY", broker_symbol="NIFTY24DECFUT", lot_size="75", tick_size="0.05")
    alert(trader.browser, hook["url"], signal_id="1", symbol="NIFTY", quantity=100, price=24000)
    alert(trader.browser, hook["url"], signal_id="2", symbol="NIFTY", quantity=150, price=24000, order_type="LIMIT")
    alert(
        trader.browser, hook["url"], signal_id="3", symbol="NIFTY", quantity=150, price="24000.03", order_type="LIMIT"
    )
    work()
    wrong_lot, good, wrong_tick = order_rows()
    assert wrong_lot.state == "REJECTED" and "not a multiple of the step 75" in wrong_lot.reason
    assert good.broker_symbol == "NIFTY24DECFUT" and good.quantity == 150 and good.state != "REJECTED"
    assert wrong_tick.state == "REJECTED" and "tick size 0.05" in wrong_tick.reason


def test_default_stop_loss_from_the_strategy_is_applied_on_a_valid_tick():
    trader, account, strategy, hook = setup_paper()
    trader.mapping(account, alert_symbol="RELIANCE", broker_symbol="RELIANCE", tick_size="0.05")
    trader.ok(trader.browser.patch(f"/strategies/{strategy['id']}", json={"default_stop_loss_pct": "1.5"}))
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, price="2501.10")
    work()
    order = order_rows()[0]
    assert order.stop_loss == D("2463.55")  # 2501.10 - 1.5% = 2463.5835, moved down to a 0.05 tick


def test_semi_automatic_orders_wait_for_approval():
    trader, account, strategy, hook = setup_paper(approval_required=True)
    alert(trader.browser, hook["url"], signal_id="1", quantity=2, price=100)
    alert(trader.browser, hook["url"], signal_id="2", quantity=3, price=100)
    work()
    first, second = order_rows()
    assert first.state == second.state == "AWAITING_APPROVAL"
    assert trader.ok(trader.browser.get("/overview"))["awaiting_approval"] == 2
    trader.ok(trader.browser.post(f"/orders/{first.id}/approve"))
    trader.ok(trader.browser.post(f"/orders/{second.id}/reject"))
    work()
    first, second = order_rows()
    assert first.state == "FILLED" and second.state == "REJECTED" and second.rule == "user_rejected"
    assert trader.browser.post(f"/orders/{first.id}/approve").status_code == 409


def test_unapproved_orders_expire():
    trader, account, strategy, hook = setup_paper(approval_required=True)
    alert(trader.browser, hook["url"], signal_id="1", quantity=2, price=100)
    work()
    work(at=now() + timedelta(minutes=10))
    assert order_rows()[0].state == "EXPIRED"


def test_one_signal_trades_every_linked_account_independently():
    trader, account, strategy, hook = setup_paper(sizing_mode="fixed", sizing_value="1")
    second = trader.account("paper", "Paper 2")
    third = trader.account("paper", "Paper 3")
    trader.link(strategy, second, sizing_mode="fixed", sizing_value="5")
    trader.link(strategy, third, sizing_mode="fixed", sizing_value="3")
    trader.limits(f"account:{third['id']}", max_order_quantity="2")  # only the third account refuses
    alert(trader.browser, hook["url"], signal_id="1", price=100)
    work()
    by_account = {str(o.broker_account_id): o for o in order_rows()}
    assert by_account[account["id"]].state == "FILLED" and by_account[account["id"]].quantity == 1
    assert by_account[second["id"]].state == "FILLED" and by_account[second["id"]].quantity == 5
    assert by_account[third["id"]].state == "REJECTED" and by_account[third["id"]].rule == "max_order_quantity"
    signal = trader.ok(trader.browser.get(f"/signals/{order_rows()[0].signal_id}"))
    assert len(signal["orders"]) == 3


def test_paper_orders_need_a_price_and_say_so():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, price=None)
    work()
    order = order_rows()[0]
    assert order.state == "REJECTED" and "price" in order.reason


def test_two_workers_cannot_both_claim_an_order():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, price=100)
    first, second = new_session(), new_session()
    try:
        engine.process_pending_signals(first)
        # Worker A locks the queued order inside an open transaction...
        with access(first, SYSTEM):
            from sqlalchemy import select

            first.execute(select(Order).with_for_update())
            # ...while worker B looks for work at the same moment: it must skip the locked row.
            assert engine.claim_due_orders(second, now=now()) == []
        first.rollback()
        claimed = engine.claim_due_orders(first, now=now())
        assert len(claimed) == 1
        assert engine.claim_due_orders(second, now=now()) == []  # already EXECUTING
    finally:
        first.close()
        second.close()
    # Another worker process cannot execute an order it does not own.
    from app.trading import engine as engine_module

    original = engine_module.WORKER_ID
    try:
        engine_module.WORKER_ID = "someone-else"
        db = new_session()
        engine.execute_order(db, claimed[0])
        db.close()
        assert order_rows()[0].state == "EXECUTING"
    finally:
        engine_module.WORKER_ID = original
    db = new_session()
    engine.execute_order(db, claimed[0])
    db.close()
    assert order_rows()[0].state == "FILLED" and len(fill_rows()) == 1


def test_a_worker_that_dies_mid_execution_leaves_a_paper_order_safe_to_run_again():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, price=100)
    db = new_session()
    engine.process_pending_signals(db)
    engine.claim_due_orders(db, now=now())  # claimed, then the process "dies"
    db.close()
    assert order_rows()[0].state == "EXECUTING"
    work(at=now() + timedelta(seconds=45))  # lease expired: nothing was executed, so it runs once
    order = order_rows()[0]
    assert order.state == "FILLED" and len(fill_rows()) == 1
    assert [e["to"] for e in trader.ok(trader.browser.get(f"/orders/{order.id}"))["events"]] == [
        "VALIDATED",
        "QUEUED",
        "EXECUTING",
        "QUEUED",
        "EXECUTING",
        "FILLED",
    ]


def test_queued_orders_past_their_deadline_expire_instead_of_trading_late():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=1, price=100)
    db = new_session()
    engine.process_pending_signals(db)
    db.close()
    work(at=now() + timedelta(minutes=5))
    order = order_rows()[0]
    assert order.state == "EXPIRED" and "too old" in order.reason


def test_manual_close_all_from_the_dashboard():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=4, price=100)
    work()
    trader.ok(trader.browser.post(f"/broker-accounts/{account['id']}/kill-switch", json={"enabled": True}))
    result = trader.ok(trader.browser.post(f"/broker-accounts/{account['id']}/close-all"))
    assert len(result["orders"]) == 1
    work()
    manual = [o for o in order_rows() if o.source == "manual"][0]
    assert manual.state == "FILLED"  # closing is allowed even with the kill switch on
    assert position_rows()[0].quantity == 0


def test_overview_figures_come_from_stored_fills_and_separate_paper_from_live():
    trader, account, strategy, hook = setup_paper()
    alert(trader.browser, hook["url"], signal_id="1", quantity=2, price=100)
    work()
    overview = trader.ok(trader.browser.get("/overview"))
    assert overview["paper"]["successful_executions"] == 1 and overview["paper"]["open_positions"] == 1
    assert overview["live"]["orders_today"] == 0 and overview["live"]["success_rate"] is None
    assert overview["signals_today"]["processed"] == 1
    trades = trader.ok(trader.browser.get("/trades", params={"paper": True}))
    assert len(trades) == 1 and trades[0]["is_paper"] is True
