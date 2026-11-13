"""The paper-trading engine: simulated execution. It never contacts a broker.

Prices come from the alerts themselves (the "price" field, e.g. {{close}}), so
the simulation is only as good as the prices the alerts carry:

- Market orders fill at the alert's price, moved against you by the configured
  slippage, plus the configured fees.
- Limit and stop orders fill at once if the alert's price allows it; otherwise
  they rest and are checked against every later price seen for that symbol.
- Stop loss and take profit on open positions are checked the same way.

Settings (per paper account): starting_balance, fee_per_order, fee_pct,
slippage_pct, currency.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.trading import BrokerAccount, Fill, Order, OrderState, Position
from app.trading.ledger import FillReport, utcnow

from .base import AccountInfo, Outcome, SubmitResult

DEFAULTS = {
    "starting_balance": "1000000",
    "fee_per_order": "0",
    "fee_pct": "0",
    "slippage_pct": "0.05",
    "currency": "INR",
}


@dataclass(frozen=True)
class PaperSettings:
    starting_balance: Decimal
    fee_per_order: Decimal
    fee_pct: Decimal
    slippage_pct: Decimal
    currency: str

    @classmethod
    def of(cls, account: BrokerAccount) -> "PaperSettings":
        merged = {**DEFAULTS, **(account.settings or {})}
        return cls(
            starting_balance=Decimal(str(merged["starting_balance"])),
            fee_per_order=Decimal(str(merged["fee_per_order"])),
            fee_pct=Decimal(str(merged["fee_pct"])),
            slippage_pct=Decimal(str(merged["slippage_pct"])),
            currency=str(merged["currency"]),
        )


class PaperBroker:
    def __init__(self, db: Session, account: BrokerAccount):
        self.db = db
        self.account = account
        self.settings = PaperSettings.of(account)

    def _fee(self, quantity: Decimal, price: Decimal, contract_size: Decimal) -> Decimal:
        return self.settings.fee_per_order + quantity * price * contract_size * self.settings.fee_pct / Decimal(100)

    def _slipped(self, side: str, price: Decimal) -> Decimal:
        move = price * self.settings.slippage_pct / Decimal(100)
        return price + move if side == "BUY" else price - move

    def _trade_id(self, order: Order) -> str:
        return f"paper-{order.client_order_id}-{(order.filled_quantity or 0)}"

    def fill_price(self, order: Order, market_price: Decimal) -> Decimal | None:
        """The price this order fills at given a market price, or None if it does not fill."""
        side = order.side
        if order.order_type == "MARKET":
            return self._slipped(side, market_price)
        if order.order_type == "LIMIT":
            if side == "BUY" and market_price <= order.price:
                return min(order.price, market_price)
            if side == "SELL" and market_price >= order.price:
                return max(order.price, market_price)
            return None
        triggered = (side == "BUY" and market_price >= order.trigger_price) or (
            side == "SELL" and market_price <= order.trigger_price
        )
        if not triggered:
            return None
        if order.order_type == "STOP":
            return self._slipped(side, market_price)
        # STOP_LIMIT: once triggered it behaves as a limit order.
        if side == "BUY" and market_price <= order.price or side == "SELL" and market_price >= order.price:
            return order.price
        return None

    def submit(self, order: Order, contract_size: Decimal) -> SubmitResult:
        if order.max_slippage_pct is not None and order.order_type in ("MARKET", "STOP"):
            if self.settings.slippage_pct > order.max_slippage_pct:
                return SubmitResult(
                    Outcome.REJECTED,
                    f"simulated slippage {self.settings.slippage_pct}% is above your limit of "
                    f"{order.max_slippage_pct}%",
                )
        market = order.reference_price
        if market is None:
            if order.order_type == "MARKET":
                return SubmitResult(
                    Outcome.REJECTED, 'paper orders need the market price in the alert (add "price": {{close}})'
                )
            return SubmitResult(Outcome.ACCEPTED, "resting until a price reaches it", resting=True)
        price = self.fill_price(order, market)
        if price is None:
            return SubmitResult(Outcome.ACCEPTED, "resting until a price reaches it", resting=True)
        fee = self._fee(order.quantity, price, contract_size)
        return SubmitResult(
            Outcome.FILLED,
            "filled (simulated)",
            broker_order_id=f"paper-{order.client_order_id}",
            fills=[FillReport(quantity=order.quantity, price=price, trade_id=self._trade_id(order), fee=fee)],
        )

    def position(self, symbol: str) -> Position | None:
        return self.db.scalar(
            select(Position).where(Position.broker_account_id == self.account.id, Position.symbol == symbol)
        )

    def open_positions(self) -> list[Position]:
        return list(
            self.db.scalars(
                select(Position).where(Position.broker_account_id == self.account.id, Position.quantity != 0)
            ).all()
        )

    def resting_orders(self, symbol: str) -> list[Order]:
        return list(
            self.db.scalars(
                select(Order)
                .where(
                    Order.broker_account_id == self.account.id,
                    Order.broker_symbol == symbol,
                    Order.state == OrderState.SUBMITTED.value,
                    Order.intent == "open",
                )
                .order_by(Order.created_at)
                .with_for_update()
            ).all()
        )

    def account_info(self) -> AccountInfo:
        realized, fees = self.db.execute(
            select(func.coalesce(func.sum(Fill.realized_pnl), 0), func.coalesce(func.sum(Fill.fee), 0)).where(
                Fill.broker_account_id == self.account.id
            )
        ).one()
        balance = self.settings.starting_balance + Decimal(realized) - Decimal(fees)
        unrealized = sum(
            (
                (p.last_price - p.average_price) * p.quantity * p.contract_size
                for p in self.open_positions()
                if p.last_price is not None and p.average_price is not None
            ),
            Decimal(0),
        )
        equity = balance + unrealized
        return AccountInfo(
            currency=self.settings.currency, balance=balance, equity=equity, margin_available=equity, at=utcnow()
        )
