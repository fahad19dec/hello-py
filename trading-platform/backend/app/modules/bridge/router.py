"""The protocol the MetaTrader 4 / 5 bridge Expert Advisor speaks.

The Expert Advisor runs in the customer's own terminal. It authenticates with a
device token (shown once when the account is paired), polls for work, executes
it in the terminal, and reports what happened. No broker password ever reaches
the platform.

Requests are form-encoded and replies are plain "key=value" lines, because
that is what MQL can build and parse reliably.

    POST /api/v1/bridge/poll     heartbeat + account snapshot; returns one instruction
    POST /api/v1/bridge/result   the outcome of an instruction

Instructions:

    type=order   execute this (open, close, modify_sl, modify_tp, cancel)
    type=lookup  a previous instruction's outcome was lost: search the terminal's
                 history for this id and report what happened
    type=idle    nothing to do
"""

import hashlib
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ratelimit, tokens
from app.core.config import get_settings
from app.core.db import SYSTEM, access, get_db
from app.models.trading import BRIDGE_BROKERS, BrokerAccount, BrokerCredential, Order, OrderState, Position, Strategy
from app.trading import risk
from app.trading.engine import _close_quantity, _contract_size, _failure, _success
from app.trading.ledger import FillReport, apply_fill, settle_after_fills, transition, utcnow

router = APIRouter(prefix="/bridge", tags=["terminal bridge"])
S = OrderState


def _text(lines: dict[str, object] | str, status_code: int = 200) -> PlainTextResponse:
    if isinstance(lines, str):
        return PlainTextResponse(lines + "\n", status_code=status_code)
    body = "\n".join(f"{key}={'' if value is None else value}" for key, value in lines.items())
    return PlainTextResponse(body + "\n", status_code=status_code)


def _form(raw: bytes) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(raw.decode("utf-8", "replace")).items()}


def _decimal(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def magic_number(account: BrokerAccount) -> int:
    """A stable magic number per account, so the Expert Advisor only touches its own trades."""
    configured = (account.settings or {}).get("magic")
    if configured:
        return int(configured)
    return int(hashlib.sha256(str(account.id).encode()).hexdigest()[:7], 16)


def _device(db: Session, request: Request) -> BrokerAccount | None:
    token = request.headers.get("x-device-token", "")
    if not token or len(token) > 200:
        return None
    credential = db.scalar(
        select(BrokerCredential).where(
            BrokerCredential.kind == "bridge_device_token", BrokerCredential.token_hash == tokens.hash_token(token)
        )
    )
    if credential is None:
        return None
    account = db.get(BrokerAccount, credential.broker_account_id)
    if account is None or account.broker not in BRIDGE_BROKERS or account.status == "archived":
        return None
    return account


def _fmt(value: Decimal | None) -> str:
    return "" if value is None else format(value.normalize(), "f")


def _instruction(order: Order, account: BrokerAccount) -> dict:
    return {
        "type": "order",
        "id": order.client_order_id,
        "intent": order.intent,
        "side": order.side or "",
        "symbol": order.broker_symbol,
        "volume": _fmt(order.quantity),
        "order_type": order.order_type,
        "price": _fmt(order.price),
        "trigger_price": _fmt(order.trigger_price),
        "sl": _fmt(order.stop_loss),
        "tp": _fmt(order.take_profit),
        "reference_price": _fmt(order.reference_price),
        "max_slippage_pct": _fmt(order.max_slippage_pct),
        "position_side": order.position_side or "",
        "magic": magic_number(account),
    }


@router.post("/poll")
async def poll(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    now = utcnow()
    body = await request.body()
    with access(db, SYSTEM):
        account = _device(db, request)
        if account is None:
            return _text({"type": "error", "message": "unknown device token"}, 401)
        ratelimit.enforce(f"bridge:{account.id}", limit=600, window_seconds=60)
        form = _form(body)

        account.last_heartbeat_at = now
        account.connection_status = "connected"
        account.connection_message = f"terminal {form.get('login', '?')} on {form.get('server', '?')}"[:300]
        account.last_connected_at = account.last_connected_at or now
        for field, key in (("balance", "balance"), ("equity", "equity"), ("margin_available", "margin_free")):
            value = _decimal(form.get(key))
            if value is not None:
                setattr(account, field, value)
        account.currency = form.get("currency") or account.currency
        account.snapshot_at = now
        if "positions" in form:
            _record_broker_positions(db, account, form["positions"], now)

        # 1. Outcomes we lost track of come first: ask the terminal what happened.
        lost = db.scalar(
            select(Order)
            .where(Order.broker_account_id == account.id, Order.state == S.UNKNOWN.value)
            .order_by(Order.updated_at)
            .limit(1)
        )
        if lost is not None:
            db.commit()
            return _text({"type": "lookup", "id": lost.client_order_id, "magic": magic_number(account)})

        # 2. Otherwise hand out the oldest queued order, if it is still valid.
        while True:
            order = db.scalar(
                select(Order)
                .where(Order.broker_account_id == account.id, Order.state == S.QUEUED.value)
                .order_by(Order.queued_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if order is None:
                db.commit()
                return _text({"type": "idle"})
            if order.expires_at is not None and order.expires_at < now:
                transition(
                    db,
                    order,
                    S.EXPIRED,
                    "the terminal was not reached in time; the signal is too old to trade",
                    now=now,
                )
                continue
            strategy = db.get(Strategy, order.strategy_id) if order.strategy_id else None
            stopped = risk.stop_check(db, account=account, strategy=strategy, now=now)
            if risk.blocks(order.source, stopped):
                transition(db, order, S.REJECTED, stopped.message, rule=stopped.rule, now=now)
                continue
            order.attempts += 1
            transition(db, order, S.EXECUTING, "sent to the terminal", now=now)
            if order.intent == "close":
                closing = _close_quantity(db, order)
                if closing is None:
                    transition(db, order, S.CANCELLED, "no open position to close", now=now)
                    continue
                order.side, order.quantity = closing
            order.lease_owner = f"bridge:{account.id}"
            order.lease_expires_at = now + timedelta(seconds=settings.execution_lease_seconds)
            instruction = _instruction(order, account)
            db.commit()
            return _text(instruction)


def _record_broker_positions(db: Session, account: BrokerAccount, raw: str, now) -> None:
    """Positions the terminal reports ("SYMBOL:signed_volume;..."), kept beside ours for reconciliation."""
    reported: dict[str, Decimal] = {}
    for item in raw.split(";"):
        if ":" in item:
            symbol, volume = item.split(":", 1)
            value = _decimal(volume)
            if symbol.strip() and value is not None:
                reported[symbol.strip()] = reported.get(symbol.strip(), Decimal(0)) + value
    for position in db.scalars(select(Position).where(Position.broker_account_id == account.id)).all():
        position.broker_quantity = reported.pop(position.symbol, Decimal(0))
        position.reconciled_at = now
    for symbol, volume in reported.items():
        db.add(
            Position(
                user_id=account.user_id,
                broker_account_id=account.id,
                symbol=symbol,
                quantity=Decimal(0),
                realized_pnl=Decimal(0),
                is_paper=False,
                broker_quantity=volume,
                reconciled_at=now,
            )
        )


RESULT_STATES = {S.EXECUTING.value, S.UNKNOWN.value, S.SUBMITTED.value, S.PARTIALLY_FILLED.value}


@router.post("/result")
async def result(request: Request, db: Session = Depends(get_db)):
    now = utcnow()
    form = _form(await request.body())
    with access(db, SYSTEM):
        account = _device(db, request)
        if account is None:
            return _text({"status": "error", "message": "unknown device token"}, 401)
        order = db.scalar(
            select(Order)
            .where(Order.broker_account_id == account.id, Order.client_order_id == form.get("id", ""))
            .with_for_update()
        )
        if order is None:
            return _text({"status": "error", "message": "unknown instruction id"}, 404)
        if order.state not in RESULT_STATES:
            return _text({"status": "ok", "message": f"already {order.state}"})

        outcome = form.get("status", "")
        message = form.get("message", "")[:300]
        ticket = form.get("ticket") or None
        if ticket:
            order.broker_order_id = ticket[:64]

        if outcome == "filled":
            price, volume = _decimal(form.get("price")), _decimal(form.get("volume"))
            if price is None or volume is None or volume <= 0:
                return _text({"status": "error", "message": "filled needs price and volume"}, 400)
            trade_id = form.get("deal") or ticket or f"{order.client_order_id}:{volume}"
            apply_fill(
                db,
                order,
                FillReport(volume, price, trade_id[:64]),
                contract_size=_contract_size(db, account, order.broker_symbol),
            )
            if order.quantity is None:
                order.quantity = order.filled_quantity
            settle_after_fills(db, order, message or "filled in the terminal")
            _success(account)
        elif outcome == "placed":
            if order.state != S.SUBMITTED.value:
                transition(db, order, S.SUBMITTED, message or "pending order placed in the terminal", now=now)
            _success(account)
        elif outcome == "applied":
            transition(db, order, S.APPLIED, message or "done in the terminal", now=now)
            _success(account)
        elif outcome == "cancelled":
            transition(db, order, S.CANCELLED, message or "cancelled in the terminal", now=now)
        elif outcome == "rejected":
            if order.state == S.EXECUTING.value:
                transition(db, order, S.REJECTED, f"terminal refused: {message}", rule="broker_rejected", now=now)
            else:
                transition(db, order, S.FAILED, f"terminal refused: {message}", now=now)
            _failure(db, account, order)
        elif outcome == "not_found":
            if order.state != S.UNKNOWN.value:
                return _text({"status": "error", "message": "not_found only answers a lookup"}, 400)
            transition(
                db, order, S.FAILED, "not in the terminal's history after checking; it was not executed", now=now
            )
            _failure(db, account, order)
        elif outcome == "error":
            if order.state == S.EXECUTING.value:
                order.next_attempt_at = now
                transition(db, order, S.UNKNOWN, f"terminal reported an error: {message}; checking", now=now)
            else:
                return _text({"status": "error", "message": "error only answers an instruction"}, 400)
        else:
            return _text({"status": "error", "message": f"unknown status '{outcome}'"}, 400)
        db.commit()
    return _text({"status": "ok"})
