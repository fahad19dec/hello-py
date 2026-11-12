"""Turns one bridge order into MT5 trade requests.

The MetaTrader5 module is passed in so this logic can be tested without a
terminal (the real package only exists on Windows).
"""

# Bits of symbol_info().filling_mode (SYMBOL_FILLING_FOK / SYMBOL_FILLING_IOC).
_FILLING_FOK_BIT = 1
_FILLING_IOC_BIT = 2


class MT5Error(Exception):
    pass


class MT5Executor:
    def __init__(self, mt5, magic: int = 520520, deviation: int = 20):
        self.mt5 = mt5
        self.magic = magic
        self.deviation = deviation

    # ------------------------------------------------------------ session

    def connect(self, terminal_path: str, login: str, password: str, server: str) -> None:
        ok = self.mt5.initialize(
            path=terminal_path,
            login=int(login),
            password=password,
            server=server,
            portable=True,
            timeout=60000,
        )
        if not ok:
            raise MT5Error(f"could not log in: {self.mt5.last_error()}")

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def health(self) -> dict:
        """Status report for the dashboard."""
        account = self.mt5.account_info()
        terminal = self.mt5.terminal_info()
        if account is None or terminal is None:
            return {"status": "error", "message": f"terminal not responding: {self.mt5.last_error()}"}
        report = {"status": "connected", "message": "", "balance": account.balance, "equity": account.equity}
        if not terminal.connected:
            report.update(status="error", message="terminal lost connection to the broker")
        elif not terminal.trade_allowed:
            report.update(status="error", message="algo trading is disabled in the terminal")
        elif not account.trade_allowed:
            report.update(status="error", message="trading is disabled for this account (investor password?)")
        return report

    # ------------------------------------------------------------- orders

    def execute(self, order: dict) -> dict:
        try:
            return self._execute(order)
        except MT5Error as exc:
            return {"ok": False, "message": str(exc)}

    def _execute(self, order: dict) -> dict:
        mt5 = self.mt5
        symbol = order["symbol"]
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol {symbol} does not exist at this broker")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise MT5Error(f"symbol {symbol} could not be enabled")

        action = order["action"]
        if action == "close":
            closed = self._close_positions(symbol, info)
            if not closed:
                return {"ok": True, "message": "no open position to close"}
            return {
                "ok": True,
                "message": f"closed {len(closed)} position(s)",
                "ticket": str(closed[-1].order),
                "price": closed[-1].price,
            }

        is_buy = action == "buy"
        if order.get("reverse"):
            opposite = mt5.POSITION_TYPE_SELL if is_buy else mt5.POSITION_TYPE_BUY
            self._close_positions(symbol, info, only_type=opposite)

        volume = self._normalise_volume(order["lots"], info)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": self._price(symbol, is_buy),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": order.get("comment") or "mt5bridge",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(info),
        }
        if order.get("sl"):
            request["sl"] = float(order["sl"])
        if order.get("tp"):
            request["tp"] = float(order["tp"])
        result = self._send(request)
        return {
            "ok": True,
            "message": f"{action} {volume} {symbol}",
            "ticket": str(result.order),
            "price": result.price,
        }

    def _close_positions(self, symbol: str, info, only_type=None) -> list:
        mt5 = self.mt5
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            raise MT5Error(f"could not read positions: {mt5.last_error()}")
        results = []
        for position in positions:
            if only_type is not None and position.type != only_type:
                continue
            closing_buy = position.type == mt5.POSITION_TYPE_SELL  # buy to close a short
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": position.volume,
                "type": mt5.ORDER_TYPE_BUY if closing_buy else mt5.ORDER_TYPE_SELL,
                "position": position.ticket,
                "price": self._price(symbol, closing_buy),
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": "mt5bridge close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }
            results.append(self._send(request))
        return results

    def _send(self, request: dict):
        mt5 = self.mt5
        result = mt5.order_send(request)
        if result is None:
            raise MT5Error(f"order was not sent: {mt5.last_error()}")
        if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            raise MT5Error(f"broker refused the order: {result.comment} (code {result.retcode})")
        return result

    def _price(self, symbol: str, is_buy: bool) -> float:
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"no price available for {symbol}")
        price = tick.ask if is_buy else tick.bid
        if not price:
            raise MT5Error(f"no price available for {symbol} (market closed?)")
        return price

    def _filling(self, info) -> int:
        mt5 = self.mt5
        if info.filling_mode & _FILLING_FOK_BIT:
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & _FILLING_IOC_BIT:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    @staticmethod
    def _normalise_volume(lots: float, info) -> float:
        step = info.volume_step or 0.01
        volume = round(round(lots / step) * step, 8)
        if volume < info.volume_min:
            raise MT5Error(f"{lots} lots is below the broker minimum of {info.volume_min}")
        if volume > info.volume_max:
            raise MT5Error(f"{lots} lots is above the broker maximum of {info.volume_max}")
        return volume
