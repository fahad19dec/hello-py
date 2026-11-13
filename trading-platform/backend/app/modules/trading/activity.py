"""What happened: signals, orders (with approval), positions, trades and the overview figures.

Every figure is computed from stored rows. Paper and live are always separated.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.http import request_meta
from app.models.trading import (
    BrokerAccount,
    Fill,
    Order,
    OrderEvent,
    OrderState,
    Position,
    Signal,
    Strategy,
    StrategyAccount,
    Webhook,
)
from app.modules.auth.deps import AuthContext, current_user
from app.trading import risk
from app.trading.ledger import transition, utcnow

from .common import owned, record

router = APIRouter(tags=["activity"])
S = OrderState


def _labels(db: Session, user_id) -> tuple[dict, dict]:
    accounts = {a.id: a for a in db.scalars(select(BrokerAccount).where(BrokerAccount.user_id == user_id)).all()}
    strategies = {s.id: s for s in db.scalars(select(Strategy).where(Strategy.user_id == user_id)).all()}
    return accounts, strategies


def order_out(order: Order, accounts: dict, strategies: dict) -> dict:
    account = accounts.get(order.broker_account_id)
    strategy = strategies.get(order.strategy_id)
    latency = None
    if order.submitted_at is not None:
        latency = round((order.submitted_at - order.created_at).total_seconds() * 1000)
    return {
        "id": order.id,
        "client_order_id": order.client_order_id,
        "signal_id": order.signal_id,
        "source": order.source,
        "account_id": order.broker_account_id,
        "account_label": account.label if account else None,
        "broker": account.broker if account else None,
        "strategy_code": strategy.code if strategy else None,
        "is_paper": order.is_paper,
        "intent": order.intent,
        "side": order.side,
        "symbol": order.symbol,
        "broker_symbol": order.broker_symbol,
        "exchange": order.exchange,
        "order_type": order.order_type,
        "quantity": order.quantity,
        "price": order.price,
        "trigger_price": order.trigger_price,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "reference_price": order.reference_price,
        "state": order.state,
        "reason": order.reason,
        "rule": order.rule,
        "broker_order_id": order.broker_order_id,
        "filled_quantity": order.filled_quantity,
        "average_price": order.average_price,
        "approval_expires_at": order.approval_expires_at,
        "created_at": order.created_at,
        "submitted_at": order.submitted_at,
        "completed_at": order.completed_at,
        "latency_ms": latency,
    }


# ------------------------------------------------------------------ signals


@router.get("/signals")
def list_signals(
    status: Literal["received", "processed", "ignored", "rejected"] | None = None,
    strategy_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = None,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Signal).where(Signal.user_id == ctx.user.id).order_by(Signal.received_at.desc()).limit(limit)
    if status:
        query = query.where(Signal.status == status)
    if strategy_id:
        query = query.where(Signal.strategy_id == strategy_id)
    if before:
        query = query.where(Signal.received_at < before)
    signals = db.scalars(query).all()
    webhooks = {w.id: w for w in db.scalars(select(Webhook).where(Webhook.user_id == ctx.user.id)).all()}
    counts = (
        dict(
            db.execute(
                select(Order.signal_id, func.count(Order.id))
                .where(Order.signal_id.in_([s.id for s in signals]))
                .group_by(Order.signal_id)
            ).all()
        )
        if signals
        else {}
    )
    return [
        {
            "id": s.id,
            "client_signal_id": s.client_signal_id,
            "webhook_name": webhooks[s.webhook_id].name if s.webhook_id in webhooks else None,
            "received_at": s.received_at,
            "signal_time": s.signal_time,
            "action": s.action,
            "symbol": s.symbol,
            "status": s.status,
            "reason": s.reason,
            "duplicate_count": s.duplicate_count,
            "order_count": counts.get(s.id, 0),
            "payload": s.payload,
        }
        for s in signals
    ]


@router.get("/signals/{signal_id}")
def get_signal(signal_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    signal = owned(db, Signal, signal_id, ctx.user, "Signal")
    accounts, strategies = _labels(db, ctx.user.id)
    orders = db.scalars(select(Order).where(Order.signal_id == signal.id).order_by(Order.created_at, Order.leg)).all()
    return {
        "id": signal.id,
        "client_signal_id": signal.client_signal_id,
        "received_at": signal.received_at,
        "signal_time": signal.signal_time,
        "status": signal.status,
        "reason": signal.reason,
        "action": signal.action,
        "symbol": signal.symbol,
        "payload": signal.payload,
        "duplicate_count": signal.duplicate_count,
        "orders": [order_out(o, accounts, strategies) for o in orders],
    }


# ------------------------------------------------------------------- orders


@router.get("/orders")
def list_orders(
    state: str | None = Query(None, max_length=40, description="A state, or 'open' for unfinished orders"),
    account_id: uuid.UUID | None = None,
    paper: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = None,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Order).where(Order.user_id == ctx.user.id).order_by(Order.created_at.desc()).limit(limit)
    if state == "open":
        query = query.where(
            Order.state.in_(
                [
                    "AWAITING_APPROVAL",
                    "VALIDATED",
                    *sorted({S.QUEUED, S.EXECUTING, S.SUBMITTED, S.PARTIALLY_FILLED, S.UNKNOWN}),
                ]
            )
        )
    elif state:
        query = query.where(Order.state == state.upper())
    if account_id:
        query = query.where(Order.broker_account_id == account_id)
    if paper is not None:
        query = query.where(Order.is_paper.is_(paper))
    if before:
        query = query.where(Order.created_at < before)
    accounts, strategies = _labels(db, ctx.user.id)
    return [order_out(o, accounts, strategies) for o in db.scalars(query).all()]


@router.get("/orders/{order_id}")
def get_order(order_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    order = owned(db, Order, order_id, ctx.user, "Order")
    accounts, strategies = _labels(db, ctx.user.id)
    events = db.scalars(select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.id)).all()
    fills = db.scalars(select(Fill).where(Fill.order_id == order.id).order_by(Fill.filled_at)).all()
    return {
        **order_out(order, accounts, strategies),
        "events": [
            {"at": e.created_at, "from": e.from_state, "to": e.to_state, "message": e.message, "detail": e.detail}
            for e in events
        ],
        "fills": [
            {
                "at": f.filled_at,
                "quantity": f.quantity,
                "price": f.price,
                "fee": f.fee,
                "trade_id": f.broker_trade_id,
                "realized_pnl": f.realized_pnl,
            }
            for f in fills
        ],
    }


@router.post("/orders/{order_id}/approve")
def approve_order(
    order_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Approve a semi-automatic order. The risk checks are run again first."""
    order = owned(db, Order, order_id, ctx.user, "Order")
    if order.state != S.AWAITING_APPROVAL.value:
        raise AppError(409, "not_awaiting_approval", f"This order is {order.state.lower()}, not waiting for approval.")
    now = utcnow()
    if order.approval_expires_at and order.approval_expires_at < now:
        transition(db, order, S.EXPIRED, "not approved in time", now=now)
        db.commit()
        raise AppError(409, "approval_expired", "The approval window has passed; the order expired.")
    account = db.get(BrokerAccount, order.broker_account_id)
    strategy = db.get(Strategy, order.strategy_id) if order.strategy_id else None
    draft = risk.OrderDraft(
        intent=order.intent,
        symbol=order.broker_symbol,
        exchange=order.exchange,
        quantity=order.quantity,
        price=order.reference_price,
        signal_time=None,
        is_entry=order.intent == "open",
    )
    rejection = risk.check(
        db,
        account=account,
        strategy=strategy,
        draft=draft,
        user_timezone=ctx.user.timezone,
        now=now,
        skip_signal_age=True,
        exclude_order_id=order.id,
    )
    meta = request_meta(request)
    if rejection:
        transition(db, order, S.REJECTED, rejection.message, rule=rejection.rule, now=now)
        record(db, ctx.user, meta, "order.approval_refused_by_risk", "order", order.id, rule=rejection.rule)
        db.commit()
        raise AppError(409, "risk_rejected", f"Risk check failed on approval: {rejection.message}")
    order.next_attempt_at = now
    # An approved order gets a fresh, short execution window instead of the signal's age limit.
    order.expires_at = now + timedelta(seconds=get_settings().default_max_signal_age_seconds)
    transition(db, order, S.QUEUED, "approved by you", now=now)
    record(db, ctx.user, meta, "order.approved", "order", order.id)
    db.commit()
    accounts, strategies = _labels(db, ctx.user.id)
    return order_out(order, accounts, strategies)


@router.post("/orders/{order_id}/reject")
def reject_order(
    order_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    order = owned(db, Order, order_id, ctx.user, "Order")
    if order.state != S.AWAITING_APPROVAL.value:
        raise AppError(409, "not_awaiting_approval", f"This order is {order.state.lower()}, not waiting for approval.")
    transition(db, order, S.REJECTED, "rejected by you", rule="user_rejected")
    record(db, ctx.user, request_meta(request), "order.rejected_by_user", "order", order.id)
    db.commit()
    accounts, strategies = _labels(db, ctx.user.id)
    return order_out(order, accounts, strategies)


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Cancel an order that has not reached a broker yet, or a resting paper order."""
    order = owned(db, Order, order_id, ctx.user, "Order")
    cancellable = {S.AWAITING_APPROVAL.value, S.QUEUED.value}
    if order.is_paper and order.state == S.SUBMITTED.value:
        cancellable.add(S.SUBMITTED.value)
    if order.state not in cancellable:
        raise AppError(
            409,
            "not_cancellable",
            f"This order is {order.state.lower()}. Orders already at a broker are cancelled with a "
            "CANCEL_ORDER alert or in the broker's own app.",
        )
    transition(db, order, S.CANCELLED, "cancelled by you")
    record(db, ctx.user, request_meta(request), "order.cancelled_by_user", "order", order.id)
    db.commit()
    accounts, strategies = _labels(db, ctx.user.id)
    return order_out(order, accounts, strategies)


# ---------------------------------------------------------- positions, trades


@router.get("/positions")
def list_positions(
    account_id: uuid.UUID | None = None, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    accounts, _ = _labels(db, ctx.user.id)
    query = select(Position).where(Position.user_id == ctx.user.id).order_by(Position.symbol)
    if account_id:
        query = query.where(Position.broker_account_id == account_id)
    out = []
    for p in db.scalars(query).all():
        mismatch = p.broker_quantity is not None and p.broker_quantity != p.quantity
        if p.quantity == 0 and not mismatch:
            continue
        unrealized = None
        if p.last_price is not None and p.average_price is not None:
            unrealized = (p.last_price - p.average_price) * p.quantity * p.contract_size
        account = accounts.get(p.broker_account_id)
        out.append(
            {
                "id": p.id,
                "account_id": p.broker_account_id,
                "account_label": account.label if account else None,
                "broker": account.broker if account else None,
                "symbol": p.symbol,
                "quantity": p.quantity,
                "average_price": p.average_price,
                "last_price": p.last_price,
                "unrealized_pnl": unrealized,
                "realized_pnl": p.realized_pnl,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "is_paper": p.is_paper,
                "broker_quantity": p.broker_quantity,
                "reconciled_at": p.reconciled_at,
                "mismatch": mismatch,
                "updated_at": p.updated_at,
            }
        )
    return out


@router.get("/trades")
def list_trades(
    account_id: uuid.UUID | None = None,
    paper: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    before: datetime | None = None,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    accounts, _ = _labels(db, ctx.user.id)
    query = select(Fill).where(Fill.user_id == ctx.user.id).order_by(Fill.filled_at.desc()).limit(limit)
    if account_id:
        query = query.where(Fill.broker_account_id == account_id)
    if paper is not None:
        query = query.where(Fill.is_paper.is_(paper))
    if before:
        query = query.where(Fill.filled_at < before)
    return [
        {
            "id": f.id,
            "order_id": f.order_id,
            "account_label": accounts[f.broker_account_id].label if f.broker_account_id in accounts else None,
            "symbol": f.symbol,
            "side": f.side,
            "quantity": f.quantity,
            "price": f.price,
            "fee": f.fee,
            "realized_pnl": f.realized_pnl,
            "is_paper": f.is_paper,
            "trade_id": f.broker_trade_id,
            "filled_at": f.filled_at,
        }
        for f in db.scalars(query).all()
    ]


# ------------------------------------------------------------------ overview


@router.get("/overview")
def overview(ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    """Today's figures, each computed from stored rows and split into paper and live."""
    since = risk.start_of_day(utcnow(), ctx.user.timezone)
    uid = ctx.user.id
    accounts = db.scalars(
        select(BrokerAccount).where(BrokerAccount.user_id == uid, BrokerAccount.status != "archived")
    ).all()

    def split(paper: bool) -> dict:
        orders_today = db.execute(
            select(Order.state, func.count(Order.id))
            .where(Order.user_id == uid, Order.is_paper.is_(paper), Order.created_at >= since)
            .group_by(Order.state)
        ).all()
        by_state = {state: count for state, count in orders_today}
        done = by_state.get("FILLED", 0) + by_state.get("APPLIED", 0)
        failed = sum(by_state.get(s, 0) for s in ("FAILED", "REJECTED", "EXPIRED"))
        realized, fees, volume = db.execute(
            select(
                func.coalesce(func.sum(Fill.realized_pnl), 0),
                func.coalesce(func.sum(Fill.fee), 0),
                func.coalesce(func.sum(Fill.quantity * Fill.price), 0),
            ).where(Fill.user_id == uid, Fill.is_paper.is_(paper), Fill.filled_at >= since)
        ).one()
        positions = db.scalars(
            select(Position).where(Position.user_id == uid, Position.is_paper.is_(paper), Position.quantity != 0)
        ).all()
        unrealized = sum(
            (
                (p.last_price - p.average_price) * p.quantity * p.contract_size
                for p in positions
                if p.last_price is not None and p.average_price is not None
            ),
            Decimal(0),
        )
        latency = db.scalar(
            select(func.avg(func.extract("epoch", Order.submitted_at - Order.created_at))).where(
                Order.user_id == uid,
                Order.is_paper.is_(paper),
                Order.submitted_at.is_not(None),
                Order.created_at >= since,
            )
        )
        attempted = done + failed
        return {
            "orders_today": sum(by_state.values()),
            "successful_executions": done,
            "failed_executions": failed,
            "orders_by_state": by_state,
            "open_positions": len(positions),
            "realized_pnl_today": Decimal(realized) - Decimal(fees),
            "fees_today": Decimal(fees),
            "unrealized_pnl": unrealized,
            "volume_today": Decimal(volume),
            "success_rate": round(done / attempted * 100, 1) if attempted else None,
            "avg_signal_to_submission_ms": round(float(latency) * 1000) if latency is not None else None,
        }

    signals = dict(
        db.execute(
            select(Signal.status, func.count(Signal.id))
            .where(Signal.user_id == uid, Signal.received_at >= since)
            .group_by(Signal.status)
        ).all()
    )
    awaiting = db.scalar(
        select(func.count(Order.id)).where(Order.user_id == uid, Order.state == S.AWAITING_APPROVAL.value)
    )
    active_strategies = db.scalar(
        select(func.count(Strategy.id)).where(Strategy.user_id == uid, Strategy.status == "active")
    )
    active_webhooks = db.scalar(
        select(func.count(Webhook.id)).where(
            Webhook.user_id == uid, Webhook.enabled.is_(True), Webhook.revoked_at.is_(None)
        )
    )
    linked = db.scalar(
        select(func.count(StrategyAccount.id)).where(StrategyAccount.user_id == uid, StrategyAccount.enabled.is_(True))
    )
    return {
        "since": since,
        "accounts": {
            "total": len(accounts),
            "connected": sum(1 for a in accounts if a.connection_status == "connected"),
            "paper": sum(1 for a in accounts if a.broker == "paper"),
            "live": sum(1 for a in accounts if a.broker != "paper"),
            "kill_switched": sum(1 for a in accounts if a.kill_switch or a.circuit_open),
        },
        "active_strategies": active_strategies,
        "active_webhooks": active_webhooks,
        "linked_accounts": linked,
        "signals_today": {"total": sum(signals.values()), **signals},
        "awaiting_approval": awaiting,
        "paper": split(True),
        "live": split(False),
    }
