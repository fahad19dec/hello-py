"""Zerodha Kite Connect over its REST API.

Checked against Zerodha's documentation in November 2026:
- Requests are signed with "Authorization: token api_key:access_token".
- The access token comes from a daily login and expires at 6:00 IST the next day.
- Market and SL-M orders must carry market_protection (-1 means Zerodha's automatic limit).
- An order id in the reply means accepted, not executed.
- Since April 2026 orders are only accepted from a static IP registered to the
  developer account (see docs/compliance-notes.md). Other calls work from anywhere.
- At most 10 orders per second per client.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings

from .base import (
    AccountInfo,
    BrokerError,
    BrokerPosition,
    Capabilities,
    ExecutionStatus,
    OrderRequest,
    Outcome,
    SubmitResult,
)

KITE_ORDER_TYPES = {"MARKET": "MARKET", "LIMIT": "LIMIT", "STOP": "SL-M", "STOP_LIMIT": "SL"}
IST = ZoneInfo("Asia/Kolkata")


def login_url(api_key: str, state: str) -> str:
    query = urlencode({"v": 3, "api_key": api_key, "redirect_params": urlencode({"state": state})})
    return f"{get_settings().kite_login_url}?{query}"


def token_expiry(now: datetime) -> datetime:
    """Zerodha tokens end at 6:00 IST on the morning after the login."""
    local = now.astimezone(IST)
    expiry = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if local >= expiry:
        expiry += timedelta(days=1)
    return expiry.astimezone(UTC)


def postback_checksum(order_id: str, order_timestamp: str, api_secret: str) -> str:
    return hashlib.sha256((order_id + order_timestamp + api_secret).encode()).hexdigest()


def new_http_client() -> httpx.Client:
    return httpx.Client(base_url=get_settings().kite_api_url, timeout=10, headers={"X-Kite-Version": "3"})


class KiteAdapter:
    capabilities = Capabilities(attached_stop_loss=False, modify_position_stops=False, netting=True)

    def __init__(self, api_key: str, access_token: str | None, client: httpx.Client | None = None):
        self.api_key = api_key
        self.access_token = access_token
        self._client = client

    # ------------------------------------------------------------- plumbing

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = new_http_client()
        return self._client

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"token {self.api_key}:{self.access_token}"}

    @staticmethod
    def _data(response: httpx.Response):
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerError(f"Zerodha returned an unreadable reply (HTTP {response.status_code})") from exc
        if response.status_code >= 400 or body.get("status") != "success":
            raise BrokerError(
                body.get("message") or f"HTTP {response.status_code}",
                auth_failed=body.get("error_type") == "TokenException",
            )
        return body.get("data")

    def _get(self, path: str):
        try:
            return self._data(self.client.get(path, headers=self._auth()))
        except httpx.HTTPError as exc:
            raise BrokerError(f"could not reach Zerodha: {type(exc).__name__}", reachable=False) from exc

    # ---------------------------------------------------------- session

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()

    def exchange_request_token(self, request_token: str, api_secret: str) -> str:
        checksum = hashlib.sha256((self.api_key + request_token + api_secret).encode()).hexdigest()
        try:
            data = self._data(
                self.client.post(
                    "/session/token",
                    data={"api_key": self.api_key, "request_token": request_token, "checksum": checksum},
                )
            )
        except httpx.HTTPError as exc:
            raise BrokerError(f"could not reach Zerodha: {type(exc).__name__}", reachable=False) from exc
        self.access_token = data["access_token"]
        return self.access_token

    def authenticate(self) -> bool:
        try:
            self._get("/user/profile")
        except BrokerError as exc:
            if exc.auth_failed:
                return False
            raise
        return True

    # ----------------------------------------------------------- reading

    def get_account_info(self) -> AccountInfo:
        data = self._get("/user/margins") or {}
        equity = data.get("equity") or {}
        available = equity.get("available") or {}
        net = equity.get("net")
        return AccountInfo(
            currency="INR",
            balance=Decimal(str(available.get("opening_balance")))
            if available.get("opening_balance") is not None
            else None,
            equity=Decimal(str(net)) if net is not None else None,
            margin_available=Decimal(str(net)) if net is not None else None,
            at=datetime.now(UTC),
        )

    def get_balance(self) -> Decimal | None:
        return self.get_account_info().equity

    def get_positions(self) -> list[BrokerPosition]:
        data = self._get("/portfolio/positions") or {}
        return [
            BrokerPosition(
                symbol=p["tradingsymbol"],
                quantity=Decimal(str(p["quantity"])),
                average_price=Decimal(str(p.get("average_price") or 0)) or None,
                exchange=p.get("exchange"),
                product=p.get("product"),
            )
            for p in data.get("net", [])
            if p.get("quantity")
        ]

    def get_orders(self) -> list[ExecutionStatus]:
        return [self._status_of(o) for o in (self._get("/orders") or [])]

    @staticmethod
    def _status_of(order: dict) -> ExecutionStatus:
        status = (order.get("status") or "").upper()
        filled = Decimal(str(order.get("filled_quantity") or 0))
        state = {
            "COMPLETE": "filled",
            "REJECTED": "rejected",
            "CANCELLED": "cancelled",
        }.get(status, "partially_filled" if filled > 0 else "open")
        average = order.get("average_price")
        return ExecutionStatus(
            found=True,
            state=state,
            broker_order_id=str(order.get("order_id")),
            filled_quantity=filled,
            average_price=Decimal(str(average)) if average else None,
            message=order.get("status_message") or status.lower(),
        )

    def get_execution_status(self, client_order_id: str) -> ExecutionStatus:
        try:
            orders = self._get("/orders") or []
        except BrokerError as exc:
            return ExecutionStatus(found=False, reachable=False, message=str(exc))
        # Our client order id travels in the tag. The newest order with the tag wins.
        matches = [o for o in orders if o.get("tag") == client_order_id]
        if not matches:
            return ExecutionStatus(found=False, message="no order with this tag at Zerodha today")
        return self._status_of(matches[-1])

    # ----------------------------------------------------------- writing

    def place_order(self, request: OrderRequest) -> SubmitResult:
        if request.stop_loss is not None or request.take_profit is not None:
            return SubmitResult(
                Outcome.REJECTED,
                "Zerodha orders cannot carry an attached stop loss or take profit; send a separate exit alert",
            )
        form = {
            "tradingsymbol": request.symbol,
            "exchange": request.exchange or "NSE",
            "transaction_type": request.side,
            "quantity": str(int(request.quantity))
            if request.quantity == request.quantity.to_integral()
            else str(request.quantity),
            "product": request.product or "MIS",
            "order_type": KITE_ORDER_TYPES[request.order_type],
            "validity": "DAY",
            "tag": request.client_order_id[:20],
        }
        if request.order_type in ("LIMIT", "STOP_LIMIT"):
            form["price"] = str(request.price)
        if request.order_type in ("STOP", "STOP_LIMIT"):
            form["trigger_price"] = str(request.trigger_price)
        if request.order_type in ("MARKET", "STOP"):
            # Exchanges reject API market orders without protection. A configured
            # slippage limit becomes the protection percentage; otherwise automatic.
            form["market_protection"] = str(request.max_slippage_pct) if request.max_slippage_pct else "-1"
        return self._send("POST", "/orders/regular", form)

    def _send(self, method: str, path: str, form: dict | None = None) -> SubmitResult:
        try:
            response = self.client.request(method, path, data=form, headers=self._auth())
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return SubmitResult(Outcome.NOT_SENT, f"could not connect to Zerodha ({type(exc).__name__})")
        except httpx.HTTPError as exc:
            return SubmitResult(
                Outcome.UNKNOWN,
                f"no reply from Zerodha ({type(exc).__name__}); checking the order book before anything else",
            )
        try:
            data = self._data(response)
        except BrokerError as exc:
            if response.status_code >= 500:
                return SubmitResult(Outcome.UNKNOWN, f"Zerodha error {response.status_code}: {exc}")
            return SubmitResult(Outcome.REJECTED, str(exc), auth_failed=exc.auth_failed)
        order_id = (data or {}).get("order_id") if isinstance(data, dict) else None
        if not order_id:
            return SubmitResult(Outcome.UNKNOWN, "Zerodha replied without an order id")
        return SubmitResult(Outcome.ACCEPTED, "accepted by Zerodha", broker_order_id=str(order_id))

    def modify_order(
        self, broker_order_id: str, *, price: Decimal | None, trigger_price: Decimal | None
    ) -> SubmitResult:
        form = {}
        if price is not None:
            form["price"] = str(price)
        if trigger_price is not None:
            form["trigger_price"] = str(trigger_price)
        return self._send("PUT", f"/orders/regular/{broker_order_id}", form)

    def cancel_order(self, broker_order_id: str) -> SubmitResult:
        return self._send("DELETE", f"/orders/regular/{broker_order_id}")

    def close_position(self, symbol, *, client_order_id, side_filter, quantity, exchange, max_slippage_pct):
        results = []
        for position in self.get_positions():
            if position.symbol != symbol or (exchange and position.exchange != exchange):
                continue
            if side_filter == "long" and position.quantity <= 0 or side_filter == "short" and position.quantity >= 0:
                continue
            size = abs(position.quantity) if quantity is None else min(quantity, abs(position.quantity))
            request = OrderRequest(
                client_order_id=client_order_id,
                symbol=symbol,
                side="SELL" if position.quantity > 0 else "BUY",
                quantity=size,
                exchange=position.exchange,
                product=position.product,
                max_slippage_pct=max_slippage_pct,
            )
            results.append((request, self.place_order(request)))
        return results

    def close_all_positions(self, *, client_order_id, max_slippage_pct):
        results = []
        for position in self.get_positions():
            request = OrderRequest(
                client_order_id=client_order_id,
                symbol=position.symbol,
                side="SELL" if position.quantity > 0 else "BUY",
                quantity=abs(position.quantity),
                exchange=position.exchange,
                product=position.product,
                max_slippage_pct=max_slippage_pct,
            )
            results.append((request, self.place_order(request)))
        return results
