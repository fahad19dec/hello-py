"""A stand-in for the MetaTrader5 module that records trade requests."""
from types import SimpleNamespace


class FakeMT5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE = 10009

    def __init__(self):
        self.symbols = {
            "XAUUSD": SimpleNamespace(
                visible=True, volume_min=0.01, volume_max=50.0, volume_step=0.01, filling_mode=2
            )
        }
        self.ticks = {"XAUUSD": SimpleNamespace(bid=2650.0, ask=2650.5)}
        self.positions = []
        self.sent = []
        self.refuse_with = None
        self.refuse_when = None  # optional: request -> retcode to refuse with, or None
        self.login_ok = True
        self._ticket = 1000

    def initialize(self, **kwargs):
        self.init_kwargs = kwargs
        return self.login_ok

    def shutdown(self):
        pass

    def last_error(self):
        return (-6, "authorization failed")

    def account_info(self):
        return SimpleNamespace(balance=1000.0, equity=1010.0, trade_allowed=True)

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=True)

    def symbol_info(self, symbol):
        return self.symbols.get(symbol)

    def symbol_select(self, symbol, enable):
        return True

    def symbol_info_tick(self, symbol):
        return self.ticks.get(symbol)

    def positions_get(self, symbol=None):
        return tuple(p for p in self.positions if p.symbol == symbol)

    def order_send(self, request):
        self.sent.append(request)
        refusal = self.refuse_with or (self.refuse_when(request) if self.refuse_when else None)
        if refusal:
            return SimpleNamespace(retcode=refusal, comment="No money", order=0, price=0.0)
        self._ticket += 1
        if "position" in request:
            self.positions = [p for p in self.positions if p.ticket != request["position"]]
        else:
            self.positions.append(
                SimpleNamespace(
                    ticket=self._ticket, symbol=request["symbol"], volume=request["volume"], type=request["type"]
                )
            )
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE, comment="done", order=self._ticket, price=request["price"]
        )
