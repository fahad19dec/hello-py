"""Order state changes, fills and positions. The only code that changes them.

Every state change is checked against the allowed transitions and recorded as
an order event, so an order's history can always be replayed.
"""

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trading import Fill, Order, OrderEvent, OrderState, Position

S = OrderState
ALLOWED: dict[str | None, set[str]] = {
    None: {S.VALIDATED, S.REJECTED},
    S.VALIDATED: {S.AWAITING_APPROVAL, S.QUEUED, S.REJECTED, S.CANCELLED},
    S.AWAITING_APPROVAL: {S.QUEUED, S.REJECTED, S.EXPIRED, S.CANCELLED},
    S.QUEUED: {S.EXECUTING, S.EXPIRED, S.CANCELLED, S.REJECTED},
    # QUEUED again only when the order provably never reached the broker.
    S.EXECUTING: {
        S.QUEUED,
        S.SUBMITTED,
        S.PARTIALLY_FILLED,
        S.FILLED,
        S.FAILED,
        S.REJECTED,
        S.UNKNOWN,
        S.CANCELLED,
        S.APPLIED,
    },
    S.SUBMITTED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.FAILED, S.UNKNOWN},
    S.PARTIALLY_FILLED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED},
    S.UNKNOWN: {S.SUBMITTED, S.PARTIALLY_FILLED, S.FILLED, S.FAILED, S.CANCELLED, S.APPLIED},
}
FINAL = {S.FILLED, S.CANCELLED, S.REJECTED, S.FAILED, S.EXPIRED, S.APPLIED}


class TransitionError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


_ALPHABET = string.ascii_uppercase + string.digits


def new_client_order_id() -> str:
    """18 characters, letters and digits only: fits Zerodha's tag and MetaTrader's comment."""
    return "TB" + "".join(secrets.choice(_ALPHABET) for _ in range(16))


def transition(
    db: Session,
    order: Order,
    to: str,
    message: str | None = None,
    *,
    rule: str | None = None,
    detail: dict | None = None,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    current = order.state  # None for an order that has not been stored yet
    if to not in ALLOWED.get(current, set()):
        raise TransitionError(f"order {order.client_order_id}: {current} -> {to} is not allowed")
    order.state = to
    if message is not None:
        order.reason = message[:500]
    if rule is not None:
        order.rule = rule
    if to == S.QUEUED:
        order.queued_at = order.queued_at or now
    if to in (S.SUBMITTED, S.PARTIALLY_FILLED, S.FILLED) and order.submitted_at is None:
        order.submitted_at = now
    if to in FINAL:
        order.completed_at = now
    if to != S.EXECUTING:
        order.lease_owner = None
        order.lease_expires_at = None
    if current is None:
        db.add(order)
        db.flush()
    db.add(
        OrderEvent(
            user_id=order.user_id,
            order_id=order.id,
            from_state=current,
            to_state=to,
            message=message[:500] if message else None,
            detail=detail or ({"rule": rule} if rule else {}),
        )
    )


@dataclass(frozen=True)
class FillReport:
    quantity: Decimal
    price: Decimal
    trade_id: str
    fee: Decimal = Decimal(0)
    at: datetime | None = None


def apply_fill(db: Session, order: Order, fill: FillReport, *, contract_size: Decimal = Decimal(1)) -> bool:
    """Record one execution. Returns False if this trade was already recorded."""
    exists = db.scalar(select(Fill.id).where(Fill.order_id == order.id, Fill.broker_trade_id == fill.trade_id))
    if exists is not None or fill.quantity <= 0:
        return False
    side = order.side
    if side is None:
        raise ValueError("a fill needs the order's side")

    position = db.scalar(
        select(Position)
        .where(Position.broker_account_id == order.broker_account_id, Position.symbol == order.broker_symbol)
        .with_for_update()
    )
    if position is None:
        position = Position(
            user_id=order.user_id,
            broker_account_id=order.broker_account_id,
            symbol=order.broker_symbol,
            quantity=Decimal(0),
            realized_pnl=Decimal(0),
            contract_size=contract_size,
            is_paper=order.is_paper,
        )
        db.add(position)

    signed = fill.quantity if side == "BUY" else -fill.quantity
    held = position.quantity or Decimal(0)
    realized = Decimal(0)
    if held == 0 or (held > 0) == (signed > 0):
        # Opening or adding: weighted average entry price.
        total = held + signed
        position.average_price = ((position.average_price or Decimal(0)) * abs(held) + fill.price * abs(signed)) / abs(
            total
        )
        position.quantity = total
    else:
        closing = min(abs(held), abs(signed))
        direction = Decimal(1) if held > 0 else Decimal(-1)
        realized = (fill.price - (position.average_price or fill.price)) * closing * direction * position.contract_size
        remaining = held + signed
        position.quantity = remaining
        if remaining == 0:
            position.average_price = None
            position.stop_loss = None
            position.take_profit = None
        elif (remaining > 0) != (held > 0):
            # Reversed through zero: the rest opens a new position at the fill price.
            position.average_price = fill.price
            position.stop_loss = None
            position.take_profit = None
    position.realized_pnl = (position.realized_pnl or Decimal(0)) + realized
    position.last_price = fill.price

    db.add(
        Fill(
            user_id=order.user_id,
            order_id=order.id,
            broker_account_id=order.broker_account_id,
            symbol=order.broker_symbol,
            side=side,
            quantity=fill.quantity,
            price=fill.price,
            fee=fill.fee,
            realized_pnl=realized,
            broker_trade_id=fill.trade_id,
            is_paper=order.is_paper,
            filled_at=fill.at or utcnow(),
        )
    )
    previous = order.filled_quantity or Decimal(0)
    total_filled = previous + fill.quantity
    order.average_price = ((order.average_price or Decimal(0)) * previous + fill.price * fill.quantity) / total_filled
    order.filled_quantity = total_filled
    if order.stop_loss is not None or order.take_profit is not None:
        if position.quantity != 0 and order.intent == "open":
            position.stop_loss = order.stop_loss or position.stop_loss
            position.take_profit = order.take_profit or position.take_profit
    db.flush()
    return True


def settle_after_fills(db: Session, order: Order, message: str | None = None) -> None:
    """Move an order to FILLED or PARTIALLY_FILLED according to what has been recorded."""
    if order.quantity is not None and order.filled_quantity >= order.quantity:
        transition(db, order, S.FILLED, message or "filled")
    elif order.filled_quantity > 0:
        transition(db, order, S.PARTIALLY_FILLED, message or f"{order.filled_quantity} of {order.quantity} filled")
