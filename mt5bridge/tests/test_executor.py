from tests.fake_mt5 import FakeMT5
from worker.executor import MT5Executor


def make():
    mt5 = FakeMT5()
    return mt5, MT5Executor(mt5)


def order(**kw):
    base = {"id": 1, "action": "buy", "symbol": "XAUUSD", "lots": 0.1, "sl": None, "tp": None,
            "reverse": False, "comment": ""}
    base.update(kw)
    return base


def test_buy_uses_ask_and_broker_filling_mode():
    mt5, ex = make()
    result = ex.execute(order(sl=2640.0, tp=2700.0))
    assert result["ok"] and result["price"] == 2650.5 and result["ticket"] == "1001"
    request = mt5.sent[0]
    assert request["type"] == mt5.ORDER_TYPE_BUY
    assert request["type_filling"] == mt5.ORDER_FILLING_IOC
    assert request["sl"] == 2640.0 and request["tp"] == 2700.0


def test_sell_uses_bid_and_omits_missing_stops():
    mt5, ex = make()
    assert ex.execute(order(action="sell"))["price"] == 2650.0
    assert "sl" not in mt5.sent[0] and "tp" not in mt5.sent[0]


def test_volume_is_rounded_to_step_and_checked_against_minimum():
    mt5, ex = make()
    ex.execute(order(lots=0.127))
    assert mt5.sent[0]["volume"] == 0.13
    result = ex.execute(order(lots=0.001))
    assert not result["ok"] and "below the broker minimum" in result["message"]
    assert len(mt5.sent) == 1


def test_unknown_symbol_fails_without_sending():
    mt5, ex = make()
    result = ex.execute(order(symbol="NOPE"))
    assert not result["ok"] and "does not exist" in result["message"]
    assert mt5.sent == []


def test_broker_refusal_is_reported():
    mt5, ex = make()
    mt5.refuse_with = 10019
    result = ex.execute(order())
    assert not result["ok"] and "No money" in result["message"]


def test_close_closes_every_position_on_the_symbol():
    mt5, ex = make()
    ex.execute(order())
    ex.execute(order(action="sell", lots=0.2))
    result = ex.execute(order(action="close", lots=None))
    assert result["ok"] and "closed 2" in result["message"]
    assert mt5.positions == []
    closes = mt5.sent[2:]
    # the long is closed by selling at bid, the short by buying at ask
    assert [(c["type"], c["price"]) for c in closes] == [(mt5.ORDER_TYPE_SELL, 2650.0), (mt5.ORDER_TYPE_BUY, 2650.5)]


def test_close_with_nothing_open_is_ok():
    mt5, ex = make()
    assert ex.execute(order(action="close", lots=None)) == {"ok": True, "message": "no open position to close"}


def test_reverse_closes_only_the_opposite_side_first():
    mt5, ex = make()
    ex.execute(order(action="sell"))
    ex.execute(order(action="buy", reverse=True))
    assert len(mt5.positions) == 1 and mt5.positions[0].type == mt5.POSITION_TYPE_BUY


def test_reverse_does_not_open_if_the_close_is_refused():
    mt5, ex = make()
    ex.execute(order(action="sell"))
    mt5.refuse_with = 10018
    result = ex.execute(order(action="buy", reverse=True))
    assert not result["ok"]
    assert len(mt5.sent) == 2  # the sell, then the refused close; no new buy


def test_health_reports_disabled_algo_trading():
    mt5, ex = make()
    assert ex.health()["status"] == "connected"
    mt5.terminal_info = lambda: __import__("types").SimpleNamespace(connected=True, trade_allowed=False)
    assert ex.health()["status"] == "error"


def test_reverse_says_so_when_the_close_worked_but_the_new_order_was_refused():
    mt5, ex = make()
    ex.execute(order(action="sell"))
    mt5.refuse_when = lambda request: None if "position" in request else 10019
    result = ex.execute(order(action="buy", reverse=True))
    assert not result["ok"]
    assert "closed 1 opposite position(s), then the new order failed" in result["message"]
    assert mt5.positions == []


def test_reverse_with_a_bad_size_closes_nothing():
    mt5, ex = make()
    ex.execute(order(action="sell"))
    result = ex.execute(order(action="buy", lots=0.001, reverse=True))
    assert not result["ok"] and len(mt5.sent) == 1 and len(mt5.positions) == 1
