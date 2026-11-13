"""Helpers for the trading tests: set-up shortcuts, alerts, the worker, and a scriptable broker."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.brokers.base import (
    AccountInfo,
    BrokerPosition,
    Capabilities,
    ExecutionStatus,
    OrderRequest,
    Outcome,
    SubmitResult,
)
from app.core.config import get_settings
from app.core.db import SYSTEM, access, new_session
from app.models.trading import Fill, Order, Position, Signal
from app.trading import engine, risk
from tests.helpers import Browser, enable_mfa, signed_in


def now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat().replace("+00:00", "Z")


class Trader:
    """A signed-in user with shortcuts for the trading API."""

    def __init__(self, email: str):
        self.browser: Browser = signed_in(email)
        self.email = email

    def ok(self, response, status=(200, 201)):
        assert response.status_code in ((status,) if isinstance(status, int) else status), response.text
        return response.json()

    def account(self, broker="paper", label=None, **settings):
        label = label or f"{broker}-{len(self.ok(self.browser.get('/broker-accounts')))}"
        return self.ok(
            self.browser.post("/broker-accounts", json={"broker": broker, "label": label, "settings": settings})
        )

    def strategy(self, code="STRAT_1", **extra):
        return self.ok(self.browser.post("/strategies", json={"code": code, "name": code.title(), **extra}))

    def link(self, strategy, account, **extra):
        return self.ok(
            self.browser.put(
                f"/strategies/{strategy['id']}/accounts/{account['id']}", json={"sizing_mode": "signal", **extra}
            )
        )

    def webhook(self, strategy, **extra):
        return self.ok(self.browser.post("/webhooks", json={"strategy_id": strategy["id"], "name": "TV", **extra}))

    def limits(self, scope_key, **limits):
        return self.ok(self.browser.put(f"/risk/limits/{scope_key}", json=limits))

    def mapping(self, account, **fields):
        return self.ok(self.browser.put(f"/broker-accounts/{account['id']}/instruments", json=fields))

    def orders(self, **query):
        return self.ok(self.browser.get("/orders", params=query))

    def positions(self):
        return self.ok(self.browser.get("/positions"))


def setup_paper(email="trader@example.com", **link_extra):
    """A user with one paper account, one strategy linked to it, and a webhook."""
    trader = Trader(email)
    account = trader.account("paper", "Paper")
    strategy = trader.strategy()
    trader.link(strategy, account, **link_extra)
    hook = trader.webhook(strategy)
    return trader, account, strategy, hook


def alert(browser: Browser, url: str, headers: dict | None = None, raw: bytes | None = None, **fields):
    """POST an alert the way TradingView does: a JSON text body to the webhook URL."""
    body = {"symbol": "RELIANCE", "action": "BUY", "quantity": 10, "price": 2500, "timestamp": iso()}
    body.update(fields)
    body = {k: v for k, v in body.items() if v is not None}
    path = "/api/v1" + url.split("/api/v1", 1)[1]
    content = raw if raw is not None else json.dumps(body).encode()
    return browser.http.post(path, content=content, headers={"Content-Type": "text/plain", **(headers or {})})


def work(at: datetime | None = None, passes: int = 1) -> None:
    for _ in range(passes):
        db = new_session()
        try:
            engine.run_once(db, now=at)
        finally:
            db.close()


def rows(model, *where):
    db = new_session()
    try:
        with access(db, SYSTEM):
            return list(db.scalars(select(model).where(*where)).all())
    finally:
        db.close()


def signal_rows():
    return rows(Signal)


def order_rows(*where):
    """Orders in the order their signals arrived (orders planned in one pass share a timestamp)."""
    received = {s.id: s.received_at for s in rows(Signal)}
    return sorted(
        rows(Order, *where),
        key=lambda o: (received.get(o.signal_id, o.created_at), o.created_at, o.leg, str(o.broker_account_id)),
    )


def fill_rows(*where):
    return rows(Fill, *where)


def position_rows(*where):
    return rows(Position, *where)


# ------------------------------------------------------------- simulated broker


@dataclass
class SimOrder:
    request: OrderRequest
    order_id: str
    status: str = "open"
    filled: Decimal = Decimal(0)
    price: Decimal | None = None


@dataclass
class SimulatedBroker:
    """A broker scripted per test. `script` holds the outcomes of the next place_order calls:

    "accept"   accepted, working          "fill"     accepted and filled at once (by status)
    "reject"   refused with a reason      "timeout"  accepted, but the reply is lost
    "refused"  connection refused          "lost"     reply lost and never placed
    "auth"     the login is no longer valid
    """

    script: list[str] = field(default_factory=list)
    fill_price: Decimal = Decimal("2500")
    orders: dict[str, SimOrder] = field(default_factory=dict)
    placed: list[OrderRequest] = field(default_factory=list)
    positions: list[BrokerPosition] = field(default_factory=list)
    reachable: bool = True
    capabilities = Capabilities(attached_stop_loss=False, modify_position_stops=False, netting=True)
    counter: int = 0

    def adapter(self, db, account):
        return self

    # BrokerAdapter
    def connect(self):
        return None

    def disconnect(self):
        return None

    def authenticate(self):
        return True

    def get_account_info(self):
        return AccountInfo("INR", Decimal(100000), Decimal(100000), Decimal(100000), now())

    def get_balance(self):
        return Decimal(100000)

    def get_positions(self):
        return list(self.positions)

    def get_orders(self):
        return [self.get_execution_status(cid) for cid in self.orders]

    def place_order(self, request: OrderRequest) -> SubmitResult:
        self.placed.append(request)
        step = self.script.pop(0) if self.script else "fill"
        self.counter += 1
        order_id = f"SIM{self.counter}"
        if step == "reject":
            return SubmitResult(Outcome.REJECTED, "Insufficient funds")
        if step == "auth":
            return SubmitResult(Outcome.REJECTED, "Incorrect api_key or access_token", auth_failed=True)
        if step == "refused":
            return SubmitResult(Outcome.NOT_SENT, "could not connect to Zerodha (ConnectError)")
        if step == "lost":
            return SubmitResult(Outcome.UNKNOWN, "no reply from Zerodha (ReadTimeout)")
        sim = SimOrder(request, order_id)
        self.orders[request.client_order_id] = sim
        if step == "fill":
            sim.status, sim.filled, sim.price = "filled", request.quantity, self.fill_price
        if step == "timeout":
            sim.status, sim.filled, sim.price = "filled", request.quantity, self.fill_price
            return SubmitResult(Outcome.UNKNOWN, "no reply from Zerodha (ReadTimeout)")
        return SubmitResult(Outcome.ACCEPTED, "accepted", broker_order_id=order_id)

    def fill(self, client_order_id: str, quantity: Decimal, price: Decimal | None = None):
        sim = self.orders[client_order_id]
        sim.filled = quantity
        sim.price = price or self.fill_price
        sim.status = "filled" if quantity >= sim.request.quantity else "partially_filled"

    def get_execution_status(self, client_order_id: str) -> ExecutionStatus:
        if not self.reachable:
            return ExecutionStatus(found=False, reachable=False, message="unreachable")
        sim = self.orders.get(client_order_id)
        if sim is None:
            return ExecutionStatus(found=False, message="no such order")
        return ExecutionStatus(
            found=True,
            state=sim.status,
            broker_order_id=sim.order_id,
            filled_quantity=sim.filled,
            average_price=sim.price,
            message=sim.status,
        )

    def cancel_order(self, broker_order_id):
        for sim in self.orders.values():
            if sim.order_id == broker_order_id:
                sim.status = "cancelled"
        return SubmitResult(Outcome.ACCEPTED, "cancelled", broker_order_id=broker_order_id)

    def modify_order(self, broker_order_id, *, price, trigger_price):
        return SubmitResult(Outcome.ACCEPTED, "modified", broker_order_id=broker_order_id)


def setup_kite(monkeypatch, email="kite@example.com", script=None, **link_extra):
    """A user with a connected Zerodha account (simulated broker), market open, live trading on."""
    sim = SimulatedBroker(script=list(script or []))
    monkeypatch.setattr(engine, "make_kite_adapter", sim.adapter)
    monkeypatch.setattr(risk, "kite_market_closed", lambda exchange, at: None)
    monkeypatch.setattr(get_settings(), "live_trading_enabled", True)
    trader = Trader(email)
    account = trader.account("kite", "Zerodha", api_key="kitekey1")
    trader.mapping(
        account, alert_symbol="RELIANCE", broker_symbol="RELIANCE", exchange="NSE", product="MIS", tick_size="0.05"
    )
    strategy = trader.strategy()
    trader.link(strategy, account, **link_extra)
    hook = trader.webhook(strategy)
    connect_kite(account["id"])
    return trader, account, strategy, hook, sim


def connect_kite(account_id) -> None:
    """Mark a Zerodha account logged in, as the login flow would (that flow has its own test)."""
    from app.core.crypto import get_cipher
    from app.models.trading import BrokerAccount, BrokerCredential

    db = new_session()
    try:
        with access(db, SYSTEM):
            account = db.get(BrokerAccount, account_id)
            account.connection_status = "connected"
            db.add(
                BrokerCredential(
                    user_id=account.user_id,
                    broker_account_id=account.id,
                    kind="kite_access_token",
                    value_enc=get_cipher().encrypt("token", engine.credential_aad(account.id, "kite_access_token")),
                    expires_at=now() + timedelta(hours=8),
                )
            )
            db.commit()
    finally:
        db.close()


def staff_pair(trader: Trader) -> None:
    enable_mfa(trader.browser)
