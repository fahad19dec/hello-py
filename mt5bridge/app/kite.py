"""Zerodha Kite Connect: daily login and order placement over its REST API."""
import hashlib
from urllib.parse import urlencode

import httpx

API_URL = "https://api.kite.trade"
LOGIN_URL = "https://kite.zerodha.com/connect/login"


class KiteError(Exception):
    def __init__(self, message: str, token_expired: bool = False):
        super().__init__(message)
        self.token_expired = token_expired


def new_client() -> httpx.Client:
    return httpx.Client(base_url=API_URL, timeout=10, headers={"X-Kite-Version": "3"})


def login_url(api_key: str) -> str:
    return f"{LOGIN_URL}?{urlencode({'v': 3, 'api_key': api_key})}"


def _data(response: httpx.Response):
    try:
        body = response.json()
    except ValueError:
        raise KiteError(f"Zerodha returned an unreadable reply (HTTP {response.status_code})")
    if response.status_code >= 400 or body.get("status") != "success":
        message = body.get("message") or f"HTTP {response.status_code}"
        raise KiteError(message, token_expired=body.get("error_type") == "TokenException")
    return body.get("data")


def exchange_token(client: httpx.Client, api_key: str, api_secret: str, request_token: str) -> str:
    """Swap the request token from the login redirect for the day's access token."""
    checksum = hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()
    data = _data(
        client.post(
            "/session/token",
            data={"api_key": api_key, "request_token": request_token, "checksum": checksum},
        )
    )
    return data["access_token"]


def _auth(api_key: str, access_token: str) -> dict:
    return {"Authorization": f"token {api_key}:{access_token}"}


def _place(client, auth, *, symbol, exchange, side, quantity, product, order_type="MARKET", price=None, tag="") -> str:
    form = {
        "tradingsymbol": symbol,
        "exchange": exchange,
        "transaction_type": side,
        "quantity": quantity,
        "product": product,
        "order_type": order_type,
        "validity": "DAY",
    }
    if order_type == "LIMIT":
        form["price"] = price
    else:
        # The exchanges reject API market orders that carry no market
        # protection; -1 asks Zerodha to apply its automatic limit.
        form["market_protection"] = -1
    tag = "".join(ch for ch in tag if ch.isalnum())[:20]  # Zerodha: alphanumeric, 20 chars
    if tag:
        form["tag"] = tag
    data = _data(client.post("/orders/regular", data=form, headers=auth))
    if not isinstance(data, dict) or "order_id" not in data:
        raise KiteError("Zerodha replied without an order id; check your Zerodha order book")
    return str(data["order_id"])


def _net_positions(client, auth, symbol: str, exchange: str) -> list[dict]:
    data = _data(client.get("/portfolio/positions", headers=auth)) or {}
    return [
        p
        for p in data.get("net", [])
        if p.get("tradingsymbol") == symbol and p.get("exchange") == exchange and p.get("quantity")
    ]


def _close(client, auth, positions: list[dict], tag: str, progress: dict) -> list[str]:
    ids = []
    for position in positions:
        quantity = position["quantity"]
        ids.append(
            _place(
                client,
                auth,
                symbol=position["tradingsymbol"],
                exchange=position["exchange"],
                side="SELL" if quantity > 0 else "BUY",
                quantity=abs(quantity),
                product=position["product"],
                tag=tag,
            )
        )
        progress["closed"] += 1
    return ids


def execute(client: httpx.Client, api_key: str, access_token: str, order: dict) -> dict:
    """Place one bridge order. Returns {ok, message, ticket, token_expired}.

    "ok" means Zerodha accepted the order, not that it has been filled.
    """
    auth = _auth(api_key, access_token)
    symbol, exchange, tag = order["symbol"], order["exchange"], order.get("comment", "")
    progress = {"closed": 0}  # closing orders already sent, so a failure can say so

    def fail(message: str, token_expired: bool = False) -> dict:
        if progress["closed"]:
            message = f"{progress['closed']} closing order(s) were sent, then this failed: {message}"
        return {"ok": False, "message": message, "ticket": None, "token_expired": token_expired}

    try:
        if order["action"] == "close":
            ids = _close(client, auth, _net_positions(client, auth, symbol, exchange), tag, progress)
            if not ids:
                return {"ok": True, "message": "no open position to close", "ticket": None}
            return {"ok": True, "message": f"closing {len(ids)} position(s)", "ticket": ids[-1]}

        is_buy = order["action"] == "buy"
        if order.get("reverse"):
            opposite = [
                p for p in _net_positions(client, auth, symbol, exchange) if (p["quantity"] < 0) == is_buy
            ]
            _close(client, auth, opposite, tag, progress)
        order_id = _place(
            client,
            auth,
            symbol=symbol,
            exchange=exchange,
            side="BUY" if is_buy else "SELL",
            quantity=order["quantity"],
            product=order["product"],
            order_type=order["order_type"],
            price=order.get("limit_price"),
            tag=tag,
        )
        return {
            "ok": True,
            "message": f"{order['action']} {order['quantity']} {exchange}:{symbol} sent to Zerodha",
            "ticket": order_id,
        }
    except KiteError as exc:
        return fail(str(exc), exc.token_expired)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return fail("could not connect to Zerodha; this order was not sent")
    except httpx.HTTPError:
        # The request left but no reply came back, so the outcome is unknown.
        return fail("lost the connection to Zerodha before it replied; check your Zerodha order book before retrying")
