"""The interface every directly-connected broker implements.

Every call that sends an order returns one of four outcomes, and the
execution engine's retry rule depends only on this:

    ACCEPTED   the broker returned an order id: wait for fill evidence
    REJECTED   the broker definitely refused: record the reason, never retry
    NOT_SENT   provably never left (connection refused): safe to retry
    UNKNOWN    sent, but no answer: reconcile with the broker, never retry blindly

FILLED is ACCEPTED with the fills already known (the paper engine, or a broker
that answers synchronously).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.trading.ledger import FillReport


class Outcome(StrEnum):
    ACCEPTED = "accepted"
    FILLED = "filled"
    REJECTED = "rejected"
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"


@dataclass
class SubmitResult:
    outcome: Outcome
    message: str = ""
    broker_order_id: str | None = None
    fills: list[FillReport] = field(default_factory=list)
    # True when the broker said the credentials are no longer valid.
    auth_failed: bool = False
    # True when the order rests at the broker (a limit or stop not yet triggered).
    resting: bool = False


@dataclass
class OrderRequest:
    client_order_id: str
    symbol: str
    side: str  # BUY / SELL
    quantity: Decimal
    order_type: str = "MARKET"
    price: Decimal | None = None
    trigger_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    exchange: str | None = None
    product: str | None = None
    reference_price: Decimal | None = None
    max_slippage_pct: Decimal | None = None


@dataclass
class ExecutionStatus:
    """What the broker knows about an order, looked up by our client order id."""

    found: bool
    state: str | None = None  # open, partially_filled, filled, cancelled, rejected
    broker_order_id: str | None = None
    filled_quantity: Decimal = Decimal(0)
    average_price: Decimal | None = None
    message: str = ""
    reachable: bool = True


@dataclass
class AccountInfo:
    currency: str | None
    balance: Decimal | None
    equity: Decimal | None
    margin_available: Decimal | None
    at: datetime | None = None


@dataclass
class BrokerPosition:
    symbol: str
    quantity: Decimal  # signed: negative is short
    average_price: Decimal | None = None
    exchange: str | None = None
    product: str | None = None


@dataclass(frozen=True)
class Capabilities:
    attached_stop_loss: bool
    modify_position_stops: bool
    netting: bool  # one net position per instrument (False: separate tickets, MetaTrader hedging)


class BrokerError(RuntimeError):
    def __init__(self, message: str, *, auth_failed: bool = False, reachable: bool = True):
        super().__init__(message)
        self.auth_failed = auth_failed
        self.reachable = reachable


class BrokerAdapter(Protocol):
    capabilities: Capabilities

    def connect(self) -> None: ...
    def authenticate(self) -> bool: ...
    def disconnect(self) -> None: ...
    def get_account_info(self) -> AccountInfo: ...
    def get_balance(self) -> Decimal | None: ...
    def get_positions(self) -> list[BrokerPosition]: ...
    def get_orders(self) -> list[ExecutionStatus]: ...
    def place_order(self, request: OrderRequest) -> SubmitResult: ...
    def modify_order(
        self, broker_order_id: str, *, price: Decimal | None, trigger_price: Decimal | None
    ) -> SubmitResult: ...
    def cancel_order(self, broker_order_id: str) -> SubmitResult: ...
    def close_position(
        self,
        symbol: str,
        *,
        client_order_id: str,
        side_filter: str | None,
        quantity: Decimal | None,
        exchange: str | None,
        max_slippage_pct: Decimal | None,
    ) -> list[tuple[OrderRequest, SubmitResult]]: ...
    def close_all_positions(
        self, *, client_order_id: str, max_slippage_pct: Decimal | None
    ) -> list[tuple[OrderRequest, SubmitResult]]: ...
    def get_execution_status(self, client_order_id: str) -> ExecutionStatus: ...
