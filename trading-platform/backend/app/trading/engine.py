"""The execution engine: signals become orders (planning), orders go to brokers (execution).

All of it runs in workers, in the trusted system context. The webhook endpoint
only stores the signal; nothing here runs inside a web request.

Safety properties, each covered by tests:
- A signal is planned once: it is locked while planned and marked processed in
  the same transaction that creates its orders.
- An account gets at most one order per signal and leg (a database constraint).
- Only one worker can claim an order (a conditional update with a lease).
- A broker reply that is lost leaves the order UNKNOWN. It is looked up at the
  broker by its client order id before anything else happens, and never re-sent
  blindly. Only orders that provably never left are retried.
- The stop-trading checks are re-run just before anything is sent.
- A failure on one account never touches another account's order.
"""

import logging
import os
import socket
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import Outcome, SubmitResult
from app.brokers.kite import KiteAdapter
from app.brokers.paper import PaperBroker
from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.db import SYSTEM, access
from app.models.identity import User
from app.models.trading import (
    BRIDGE_BROKERS,
    TERMINAL_STATES,
    BrokerAccount,
    BrokerCredential,
    InstrumentMapping,
    Order,
    OrderState,
    Position,
    Signal,
    Strategy,
    StrategyAccount,
)

from . import risk
from .ledger import FillReport, apply_fill, new_client_order_id, settle_after_fills, transition, utcnow
from .signal_format import SignalPayload
from .sizing import Instrument, SizingError, compute_quantity, validate_prices, validate_quantity

log = logging.getLogger("app.engine")
S = OrderState
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
DIRECT_BROKERS = ("paper", "kite")
UNKNOWN_GRACE_SECONDS = 30


def credential_aad(account_id, kind: str) -> str:
    return f"broker_account:{account_id}:{kind}"


def make_kite_adapter(db: Session, account: BrokerAccount) -> KiteAdapter:
    """Build the Zerodha adapter from stored credentials. Tests replace this function."""
    row = db.scalar(
        select(BrokerCredential).where(
            BrokerCredential.broker_account_id == account.id, BrokerCredential.kind == "kite_access_token"
        )
    )
    token = get_cipher().decrypt(row.value_enc, credential_aad(account.id, row.kind)) if row and row.value_enc else None
    return KiteAdapter(api_key=(account.settings or {}).get("api_key", ""), access_token=token)


# =============================================================== instruments


def resolve_instrument(db: Session, account: BrokerAccount, alert_symbol: str) -> Instrument | None:
    mapping = db.scalar(
        select(InstrumentMapping).where(
            InstrumentMapping.broker_account_id == account.id, InstrumentMapping.alert_symbol == alert_symbol
        )
    )
    if mapping is not None:
        return Instrument(
            alert_symbol=alert_symbol,
            broker_symbol=mapping.broker_symbol,
            exchange=mapping.exchange,
            product=mapping.product,
            lot_size=mapping.lot_size,
            quantity_step=mapping.quantity_step,
            min_quantity=mapping.min_quantity,
            max_quantity=mapping.max_quantity,
            tick_size=mapping.tick_size,
            contract_size=mapping.contract_size,
        )
    settings = account.settings or {}
    if account.broker == "paper":
        return Instrument(alert_symbol=alert_symbol, broker_symbol=alert_symbol, mapped=False)
    if account.broker in BRIDGE_BROKERS:
        # MetaTrader: the terminal knows the real symbol rules and checks them before sending.
        suffix = settings.get("symbol_suffix", "")
        return Instrument(
            alert_symbol=alert_symbol,
            broker_symbol=alert_symbol + suffix,
            quantity_step=Decimal("0.01"),
            mapped=False,
        )
    return None  # Zerodha: never guess a trading symbol


# =================================================================== planning


ACTION_INTENTS = {
    "BUY": ("open", "BUY", None),
    "SELL": ("open", "SELL", None),
    "CLOSE": ("close", None, None),
    "CLOSE_LONG": ("close", None, "long"),
    "CLOSE_SHORT": ("close", None, "short"),
    "CLOSE_ALL": ("close_all", None, None),
    "MODIFY_SL": ("modify_sl", None, None),
    "MODIFY_TP": ("modify_tp", None, None),
    "CANCEL_ORDER": ("cancel", None, None),
}


def process_pending_signals(db: Session, *, now: datetime | None = None, limit: int = 20) -> int:
    now = now or utcnow()
    with access(db, SYSTEM):
        signals = db.scalars(
            select(Signal)
            .where(Signal.status == "received")
            .order_by(Signal.received_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for signal in signals:
            plan_signal(db, signal, now)
        db.commit()
    return len(signals)


def plan_signal(db: Session, signal: Signal, now: datetime) -> list[Order]:
    payload = SignalPayload.model_validate(signal.payload)
    strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
    if strategy is None or strategy.status != "active":
        _finish_signal(signal, now, "ignored", f"strategy is {strategy.status if strategy else 'missing'}")
        return []
    links = db.execute(
        select(StrategyAccount, BrokerAccount)
        .join(BrokerAccount, BrokerAccount.id == StrategyAccount.broker_account_id)
        .where(
            StrategyAccount.strategy_id == strategy.id,
            StrategyAccount.enabled.is_(True),
            BrokerAccount.status != "archived",
        )
        .order_by(BrokerAccount.created_at)
    ).all()
    if not links:
        _finish_signal(signal, now, "ignored", "no enabled accounts are linked to this strategy")
        return []

    user = db.get(User, signal.user_id)
    orders: list[Order] = []
    for link, account in links:
        # Each account in its own savepoint: one account's problem never affects another's.
        savepoint = db.begin_nested()
        try:
            orders.extend(_plan_for_account(db, signal, payload, strategy, link, account, user, now))
            savepoint.commit()
        except Exception as exc:  # noqa: BLE001 - recorded on the order, never swallowed silently
            savepoint.rollback()
            log.exception("planning failed for signal %s on account %s", signal.id, account.id)
            order = _base_order(signal, payload, strategy, account, "open", None, None, payload.symbol or "*")
            transition(
                db,
                order,
                S.REJECTED,
                f"internal error while preparing the order ({type(exc).__name__})",
                rule="internal_error",
                now=now,
            )
            orders.append(order)
    if payload.price is not None and payload.symbol:
        observe_price(db, signal.user_id, payload.symbol, payload.price, now)
    _finish_signal(signal, now, "processed", f"{len(orders)} order(s) created")
    return orders


def _finish_signal(signal: Signal, now: datetime, status: str, reason: str) -> None:
    signal.status = status
    signal.reason = reason
    signal.processed_at = now


def _base_order(signal, payload, strategy, account, intent, side, position_side, symbol) -> Order:
    return Order(
        user_id=signal.user_id,
        signal_id=signal.id,
        strategy_id=strategy.id if strategy else None,
        broker_account_id=account.id,
        source="signal",
        leg=0,
        client_order_id=new_client_order_id(),
        is_paper=account.broker == "paper",
        intent=intent,
        side=side,
        position_side=position_side,
        symbol=symbol,
        broker_symbol=symbol,
        order_type=payload.order_type if intent == "open" else "MARKET",
        reference_price=payload.price,
        filled_quantity=Decimal(0),
        attempts=0,
    )


def _plan_for_account(db, signal, payload, strategy, link, account, user, now) -> list[Order]:
    intent, side, position_side = ACTION_INTENTS[signal.action]
    limits = risk.load_limits(db, account.user_id, account.id, strategy.id)
    max_age = limits.get("max_signal_age_seconds") or get_settings().default_max_signal_age_seconds

    if intent == "close_all":
        positions = db.scalars(
            select(Position)
            .where(Position.broker_account_id == account.id, Position.quantity != 0)
            .order_by(Position.symbol)
        ).all()
        if not positions:
            order = _base_order(signal, payload, strategy, account, "close_all", None, None, "*")
            transition(db, order, S.VALIDATED, now=now)
            transition(db, order, S.CANCELLED, "no open positions to close", now=now)
            return [order]
        planned = []
        for leg, position in enumerate(positions):
            order = _base_order(signal, payload, strategy, account, "close", None, None, position.symbol)
            order.leg = leg
            order.broker_symbol = position.symbol
            mapping = db.scalar(
                select(InstrumentMapping).where(
                    InstrumentMapping.broker_account_id == account.id,
                    InstrumentMapping.broker_symbol == position.symbol,
                )
            )
            if mapping is not None:
                order.symbol, order.exchange, order.product = mapping.alert_symbol, mapping.exchange, mapping.product
            planned.append(_check_and_queue(db, order, account, strategy, link, payload, user, now, max_age, limits))
        return planned

    symbol = payload.symbol
    order = _base_order(signal, payload, strategy, account, intent, side, position_side, symbol)
    instrument = resolve_instrument(db, account, symbol)
    if instrument is None:
        transition(
            db,
            order,
            S.REJECTED,
            f"{symbol} has no instrument mapping for '{account.label}'; add one before trading it",
            rule="instrument_unmapped",
            now=now,
        )
        return [order]
    order.broker_symbol = instrument.broker_symbol
    order.exchange = instrument.exchange
    order.product = instrument.product

    if intent in ("modify_sl", "modify_tp") and account.broker == "kite":
        transition(
            db,
            order,
            S.REJECTED,
            "Zerodha positions have no attached stop loss or take profit to modify",
            rule="not_supported",
            now=now,
        )
        return [order]

    if intent == "open":
        order.price = payload.price if payload.order_type in ("LIMIT", "STOP_LIMIT") else None
        order.trigger_price = payload.trigger_price
        order.stop_loss = payload.stop_loss
        order.take_profit = payload.take_profit
        reference = payload.price
        if reference is not None:
            if order.stop_loss is None and strategy.default_stop_loss_pct:
                distance = reference * strategy.default_stop_loss_pct / Decimal(100)
                order.stop_loss = reference - distance if side == "BUY" else reference + distance
            if order.take_profit is None and strategy.default_take_profit_pct:
                distance = reference * strategy.default_take_profit_pct / Decimal(100)
                order.take_profit = reference + distance if side == "BUY" else reference - distance
        if order.stop_loss is not None and instrument.tick_size:
            order.stop_loss = _round_to_tick(order.stop_loss, instrument.tick_size, side == "BUY")
        if order.take_profit is not None and instrument.tick_size:
            order.take_profit = _round_to_tick(order.take_profit, instrument.tick_size, side != "BUY")
        try:
            validate_prices(
                instrument,
                price=order.price,
                trigger_price=order.trigger_price,
                stop_loss=payload.stop_loss,
                take_profit=payload.take_profit,
            )
            capital = _capital(db, account)
            order.quantity = compute_quantity(
                link.sizing_mode,
                link.sizing_value,
                signal_quantity=payload.quantity,
                instrument=instrument,
                capital=capital,
                price=payload.price,
                stop_loss=order.stop_loss,
            )
        except SizingError as exc:
            transition(db, order, S.REJECTED, str(exc), rule="invalid_order", now=now)
            return [order]
    elif intent == "close" and payload.quantity is not None:
        try:
            order.quantity = validate_quantity(payload.quantity, instrument)
        except SizingError as exc:
            transition(db, order, S.REJECTED, str(exc), rule="invalid_order", now=now)
            return [order]
    elif intent == "modify_sl":
        order.stop_loss = payload.stop_loss
    elif intent == "modify_tp":
        order.take_profit = payload.take_profit
    return [_check_and_queue(db, order, account, strategy, link, payload, user, now, max_age, limits)]


def _round_to_tick(value: Decimal, tick: Decimal, down: bool) -> Decimal:
    """Default stops computed from a percentage are placed on a valid tick, never closer to the price."""
    from decimal import ROUND_CEILING, ROUND_FLOOR

    return (value / tick).to_integral_value(rounding=ROUND_FLOOR if down else ROUND_CEILING) * tick


def _capital(db: Session, account: BrokerAccount) -> Decimal | None:
    if account.broker == "paper":
        return PaperBroker(db, account).account_info().equity
    return account.equity


def _check_and_queue(db, order, account, strategy, link, payload, user, now, max_age, limits) -> Order:
    draft = risk.OrderDraft(
        intent=order.intent,
        symbol=order.broker_symbol,
        exchange=order.exchange,
        quantity=order.quantity,
        price=payload.price,
        signal_time=payload.timestamp,
        is_entry=order.intent == "open",
    )
    rejection = risk.check(
        db, account=account, strategy=strategy, draft=draft, user_timezone=user.timezone if user else "UTC", now=now
    )
    if rejection:
        transition(db, order, S.REJECTED, rejection.message, rule=rejection.rule, detail=rejection.detail, now=now)
        return order
    order.max_slippage_pct = limits.get("max_slippage_pct")
    order.expires_at = payload.timestamp + timedelta(seconds=max_age)
    transition(db, order, S.VALIDATED, "passed risk checks", now=now)
    if link.approval_required:
        order.approval_expires_at = risk.approval_deadline(now)
        transition(db, order, S.AWAITING_APPROVAL, "waiting for your approval", now=now)
    else:
        order.next_attempt_at = now
        transition(db, order, S.QUEUED, "queued for execution", now=now)
    return order


# ============================================================ paper prices


def observe_price(db: Session, user_id, alert_symbol: str, price: Decimal, now: datetime) -> None:
    """A price seen in an alert: trigger resting paper orders and paper stops for that symbol."""
    accounts = db.scalars(
        select(BrokerAccount).where(
            BrokerAccount.user_id == user_id, BrokerAccount.broker == "paper", BrokerAccount.status != "archived"
        )
    ).all()
    for account in accounts:
        instrument = resolve_instrument(db, account, alert_symbol)
        symbol = instrument.broker_symbol
        paper = PaperBroker(db, account)
        contract = instrument.contract_size
        for order in paper.resting_orders(symbol):
            fill_price = paper.fill_price(order, price)
            if fill_price is not None:
                fee = paper._fee(order.quantity - order.filled_quantity, fill_price, contract)
                apply_fill(
                    db,
                    order,
                    FillReport(
                        order.quantity - order.filled_quantity,
                        fill_price,
                        f"paper-{order.client_order_id}-{order.filled_quantity}",
                        fee,
                    ),
                    contract_size=contract,
                )
                settle_after_fills(db, order, "filled (simulated) when the price reached it")
        position = paper.position(symbol)
        if position is None or position.quantity == 0:
            continue
        position.last_price = price
        long = position.quantity > 0
        hit = None
        if position.stop_loss is not None and (price <= position.stop_loss if long else price >= position.stop_loss):
            hit = ("stop loss", position.stop_loss)
        elif position.take_profit is not None and (
            price >= position.take_profit if long else price <= position.take_profit
        ):
            hit = ("take profit", position.take_profit)
        if hit is None:
            continue
        label, level = hit
        # A gap through the level fills at the worse of the two prices.
        exit_price = min(level, price) if long else max(level, price)
        order = Order(
            user_id=account.user_id,
            broker_account_id=account.id,
            source="protective",
            leg=0,
            client_order_id=new_client_order_id(),
            is_paper=True,
            intent="close",
            side="SELL" if long else "BUY",
            symbol=alert_symbol,
            broker_symbol=symbol,
            order_type="MARKET",
            quantity=abs(position.quantity),
            reference_price=price,
            filled_quantity=Decimal(0),
            attempts=0,
        )
        transition(db, order, S.VALIDATED, f"{label} reached at {price}", now=now)
        transition(db, order, S.QUEUED, now=now)
        transition(db, order, S.EXECUTING, now=now)
        fee = paper._fee(order.quantity, exit_price, contract)
        apply_fill(
            db,
            order,
            FillReport(order.quantity, exit_price, f"paper-{order.client_order_id}-0", fee),
            contract_size=contract,
        )
        settle_after_fills(db, order, f"closed by {label} (simulated)")
    _refresh_paper_snapshots(db, accounts, now)


def _refresh_paper_snapshots(db: Session, accounts, now: datetime) -> None:
    for account in accounts:
        info = PaperBroker(db, account).account_info()
        account.currency, account.balance, account.equity = info.currency, info.balance, info.equity
        account.margin_available, account.snapshot_at = info.margin_available, now
        account.connection_status = "connected"


# ================================================================ execution


def claim_due_orders(db: Session, *, now: datetime, limit: int = 20) -> list:
    """Claim queued orders for directly-connected brokers. Only one worker can win each."""
    settings = get_settings()
    with access(db, SYSTEM):
        rows = db.scalars(
            select(Order)
            .join(BrokerAccount, BrokerAccount.id == Order.broker_account_id)
            .where(
                Order.state == S.QUEUED.value,
                Order.next_attempt_at <= now,
                BrokerAccount.broker.in_(DIRECT_BROKERS),
            )
            .order_by(Order.queued_at)
            .limit(limit)
            .with_for_update(of=Order, skip_locked=True)
        ).all()
        claimed = []
        for order in rows:
            if order.expires_at is not None and order.expires_at < now:
                transition(db, order, S.EXPIRED, "not executed in time; the signal is too old to trade now", now=now)
                continue
            order.attempts += 1
            transition(db, order, S.EXECUTING, f"attempt {order.attempts}", now=now)
            order.lease_owner = WORKER_ID
            order.lease_expires_at = now + timedelta(seconds=settings.execution_lease_seconds)
            claimed.append(order.id)
        db.commit()
    return claimed


def execute_order(db: Session, order_id, *, now: datetime | None = None) -> None:
    now = now or utcnow()
    with access(db, SYSTEM):
        order = db.get(Order, order_id, with_for_update=True)
        if order is None or order.state != S.EXECUTING.value or order.lease_owner != WORKER_ID:
            db.rollback()
            return  # someone else owns it now
        account = db.get(BrokerAccount, order.broker_account_id)
        strategy = db.get(Strategy, order.strategy_id) if order.strategy_id else None
        stopped = risk.stop_check(db, account=account, strategy=strategy, now=now)
        if risk.blocks(order.source, stopped):
            transition(db, order, S.REJECTED, stopped.message, rule=stopped.rule, now=now)
            db.commit()
            return
        if account.broker == "paper":
            _execute_paper(db, order, account, now)
        else:
            _execute_kite(db, order, account, now)
        db.commit()


def _close_quantity(db: Session, order: Order) -> tuple[str, Decimal] | None:
    """For a close order: the side and size that close the platform's position, or None if flat."""
    position = db.scalar(
        select(Position).where(
            Position.broker_account_id == order.broker_account_id, Position.symbol == order.broker_symbol
        )
    )
    held = position.quantity if position is not None else Decimal(0)
    if held == 0 or order.position_side == "long" and held < 0 or order.position_side == "short" and held > 0:
        return None
    size = abs(held) if order.quantity is None else min(order.quantity, abs(held))
    return ("SELL" if held > 0 else "BUY"), size


def _contract_size(db: Session, account: BrokerAccount, broker_symbol: str) -> Decimal:
    mapping = db.scalar(
        select(InstrumentMapping).where(
            InstrumentMapping.broker_account_id == account.id, InstrumentMapping.broker_symbol == broker_symbol
        )
    )
    return mapping.contract_size if mapping is not None else Decimal(1)


def _execute_paper(db: Session, order: Order, account: BrokerAccount, now: datetime) -> None:
    paper = PaperBroker(db, account)
    contract = _contract_size(db, account, order.broker_symbol)
    if order.intent == "close":
        closing = _close_quantity(db, order)
        if closing is None:
            transition(db, order, S.CANCELLED, "no open position to close", now=now)
            return
        order.side, order.quantity = closing
        if order.reference_price is None:
            position = paper.position(order.broker_symbol)
            order.reference_price = position.last_price if position else None
    if order.intent in ("open", "close"):
        result = paper.submit(order, contract)
        _record_result(db, order, account, result, now, contract)
    elif order.intent in ("modify_sl", "modify_tp"):
        position = paper.position(order.broker_symbol)
        if position is None or position.quantity == 0:
            transition(db, order, S.CANCELLED, "no open position to modify", now=now)
            return
        if order.intent == "modify_sl":
            position.stop_loss = order.stop_loss
        else:
            position.take_profit = order.take_profit
        transition(db, order, S.APPLIED, "updated on the simulated position", now=now)
    elif order.intent == "cancel":
        cancelled = 0
        for resting in paper.resting_orders(order.broker_symbol):
            transition(db, resting, S.CANCELLED, f"cancelled by signal {order.client_order_id}", now=now)
            cancelled += 1
        transition(db, order, S.APPLIED, f"cancelled {cancelled} working order(s)", now=now)
    _refresh_paper_snapshots(db, [account], now)


def _execute_kite(db: Session, order: Order, account: BrokerAccount, now: datetime) -> None:
    from app.brokers.base import OrderRequest

    adapter = make_kite_adapter(db, account)
    if order.intent == "close":
        closing = _close_quantity(db, order)
        if closing is None:
            transition(db, order, S.CANCELLED, "no open position to close", now=now)
            return
        order.side, order.quantity = closing
    if order.intent in ("open", "close"):
        request = OrderRequest(
            client_order_id=order.client_order_id,
            symbol=order.broker_symbol,
            side=order.side,
            quantity=order.quantity,
            order_type=order.order_type,
            price=order.price,
            trigger_price=order.trigger_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            exchange=order.exchange,
            product=order.product,
            reference_price=order.reference_price,
            max_slippage_pct=order.max_slippage_pct,
        )
        _record_result(db, order, account, adapter.place_order(request), now, Decimal(1))
    elif order.intent == "cancel":
        targets = db.scalars(
            select(Order).where(
                Order.broker_account_id == account.id,
                Order.broker_symbol == order.broker_symbol,
                Order.state.in_([S.SUBMITTED.value, S.PARTIALLY_FILLED.value]),
                Order.broker_order_id.is_not(None),
            )
        ).all()
        problems = []
        for target in targets:
            result = adapter.cancel_order(target.broker_order_id)
            if result.outcome not in (Outcome.ACCEPTED, Outcome.FILLED):
                problems.append(f"{target.client_order_id}: {result.message}")
        if problems:
            transition(db, order, S.FAILED, "; ".join(problems)[:500], now=now)
        else:
            transition(
                db,
                order,
                S.APPLIED,
                f"cancellation sent for {len(targets)} order(s); confirmed by status updates",
                now=now,
            )


def _record_result(
    db: Session, order: Order, account: BrokerAccount, result: SubmitResult, now: datetime, contract_size: Decimal
) -> None:
    settings = get_settings()
    if result.broker_order_id:
        order.broker_order_id = result.broker_order_id
    if result.outcome == Outcome.FILLED:
        for fill in result.fills:
            apply_fill(db, order, fill, contract_size=contract_size)
        settle_after_fills(db, order, result.message)
        _success(account)
    elif result.outcome == Outcome.ACCEPTED:
        transition(db, order, S.SUBMITTED, result.message or "accepted by the broker", now=now)
        _success(account)
    elif result.outcome == Outcome.REJECTED:
        transition(db, order, S.REJECTED, f"broker refused: {result.message}", rule="broker_rejected", now=now)
        if result.auth_failed:
            account.connection_status = "login_required"
            account.connection_message = "the broker no longer accepts the saved login; log in again"
        _failure(db, account, order)
    elif result.outcome == Outcome.NOT_SENT:
        if order.attempts < settings.max_not_sent_retries:
            order.next_attempt_at = now + timedelta(seconds=2**order.attempts)
            transition(db, order, S.QUEUED, f"{result.message}; not sent, retrying", now=now)
        else:
            transition(db, order, S.FAILED, f"{result.message}; gave up after {order.attempts} attempts", now=now)
            _failure(db, account, order)
    else:
        order.next_attempt_at = now + timedelta(seconds=UNKNOWN_GRACE_SECONDS)
        transition(db, order, S.UNKNOWN, result.message or "outcome unknown; checking with the broker", now=now)


def _success(account: BrokerAccount) -> None:
    account.consecutive_failures = 0


def _failure(db: Session, account: BrokerAccount, order: Order) -> None:
    account.consecutive_failures += 1
    if account.consecutive_failures >= risk.failure_threshold(db, account, order.strategy_id):
        account.circuit_open = True
        account.connection_message = f"stopped after {account.consecutive_failures} failed orders in a row"


# ============================================================ reconciliation


def reconcile(db: Session, *, now: datetime | None = None) -> None:
    now = now or utcnow()
    with access(db, SYSTEM):
        _expire(db, now)
        _recover_leases(db, now)
        db.commit()
        _resolve_unknown(db, now)
        _poll_submitted(db, now)
        _expire_kite_logins(db, now)
        db.commit()


def _expire(db: Session, now: datetime) -> None:
    for order in db.scalars(
        select(Order)
        .where(Order.state == S.AWAITING_APPROVAL.value, Order.approval_expires_at < now)
        .with_for_update(skip_locked=True)
    ).all():
        transition(db, order, S.EXPIRED, "not approved in time", now=now)
    for order in db.scalars(
        select(Order).where(Order.state == S.QUEUED.value, Order.expires_at < now).with_for_update(skip_locked=True)
    ).all():
        transition(db, order, S.EXPIRED, "not executed in time; the signal is too old to trade now", now=now)


def _recover_leases(db: Session, now: datetime) -> None:
    rows = db.execute(
        select(Order, BrokerAccount.broker)
        .join(BrokerAccount, BrokerAccount.id == Order.broker_account_id)
        .where(Order.state == S.EXECUTING.value, Order.lease_expires_at < now)
        .with_for_update(of=Order, skip_locked=True)
    ).all()
    for order, broker in rows:
        if broker == "paper":
            # The paper engine works inside one database transaction: a crash rolled everything back.
            order.next_attempt_at = now
            transition(db, order, S.QUEUED, "worker stopped; nothing was executed, so it is queued again", now=now)
        else:
            order.next_attempt_at = now + timedelta(seconds=UNKNOWN_GRACE_SECONDS)
            transition(
                db, order, S.UNKNOWN, "stopped mid-execution; checking with the broker before anything else", now=now
            )


def _resolve_unknown(db: Session, now: datetime) -> None:
    rows = db.execute(
        select(Order, BrokerAccount)
        .join(BrokerAccount, BrokerAccount.id == Order.broker_account_id)
        .where(Order.state == S.UNKNOWN.value, BrokerAccount.broker == "kite")
        .with_for_update(of=Order, skip_locked=True)
    ).all()
    for order, account in rows:
        status = make_kite_adapter(db, account).get_execution_status(order.client_order_id)
        if not status.reachable:
            continue  # stays UNKNOWN; tried again on the next pass
        if not status.found:
            # The broker's order book can lag; only conclude "never placed" after a grace period.
            if order.next_attempt_at is None or now >= order.next_attempt_at:
                transition(db, order, S.FAILED, "not found at Zerodha after checking; it was not executed", now=now)
                _failure(db, account, order)
            continue
        order.broker_order_id = status.broker_order_id
        _apply_status(db, order, account, status, now)


def _poll_submitted(db: Session, now: datetime) -> None:
    rows = db.execute(
        select(Order, BrokerAccount)
        .join(BrokerAccount, BrokerAccount.id == Order.broker_account_id)
        .where(Order.state.in_([S.SUBMITTED.value, S.PARTIALLY_FILLED.value]), BrokerAccount.broker == "kite")
        .with_for_update(of=Order, skip_locked=True)
    ).all()
    for order, account in rows:
        status = make_kite_adapter(db, account).get_execution_status(order.client_order_id)
        if status.reachable and status.found:
            _apply_status(db, order, account, status, now)


def _apply_status(db: Session, order: Order, account: BrokerAccount, status, now: datetime) -> None:
    """Bring an order in line with what the broker reports. Safe to call repeatedly."""
    if order.state in TERMINAL_STATES:
        return
    contract = _contract_size(db, account, order.broker_symbol)
    if order.state == S.UNKNOWN.value and status.state in ("open",) and status.filled_quantity == 0:
        transition(db, order, S.SUBMITTED, "found at the broker, working", now=now)
        return
    new_total = status.filled_quantity
    old_total = order.filled_quantity or Decimal(0)
    if new_total > old_total and status.average_price is not None:
        delta = new_total - old_total
        delta_price = (status.average_price * new_total - (order.average_price or Decimal(0)) * old_total) / delta
        apply_fill(
            db, order, FillReport(delta, delta_price, f"{status.broker_order_id}:{new_total}"), contract_size=contract
        )
    if status.state == "filled":
        if order.quantity is not None and order.filled_quantity < order.quantity:
            order.quantity = order.filled_quantity
        transition(db, order, S.FILLED, "filled at the broker", now=now)
    elif status.state == "partially_filled" and order.filled_quantity > 0:
        if order.state != S.PARTIALLY_FILLED.value or new_total > old_total:
            transition(db, order, S.PARTIALLY_FILLED, f"{order.filled_quantity} filled so far", now=now)
    elif status.state == "cancelled":
        transition(db, order, S.CANCELLED, status.message or "cancelled at the broker", now=now)
    elif status.state == "rejected":
        if order.state == S.UNKNOWN.value or order.state == S.SUBMITTED.value:
            transition(db, order, S.FAILED, f"rejected at the broker: {status.message}", now=now)
            _failure(db, account, order)


def _expire_kite_logins(db: Session, now: datetime) -> None:
    expired = db.execute(
        select(BrokerAccount, BrokerCredential)
        .join(BrokerCredential, BrokerCredential.broker_account_id == BrokerAccount.id)
        .where(
            BrokerAccount.broker == "kite",
            BrokerAccount.connection_status == "connected",
            BrokerCredential.kind == "kite_access_token",
            BrokerCredential.expires_at < now,
        )
    ).all()
    for account, credential in expired:
        account.connection_status = "login_required"
        account.connection_message = "Zerodha logins end at 6:00 IST; log in again for today"
        db.delete(credential)


# ==================================================================== worker


def run_once(db: Session, *, now: datetime | None = None) -> dict:
    """One pass of the worker loop. Returns counts, for logs and tests."""
    now = now or utcnow()
    planned = process_pending_signals(db, now=now)
    claimed = claim_due_orders(db, now=now)
    for order_id in claimed:
        try:
            execute_order(db, order_id, now=now)
        except Exception:  # noqa: BLE001 - the lease expires and reconciliation takes over
            db.rollback()
            log.exception("execution of order %s failed unexpectedly", order_id)
    reconcile(db, now=now)
    return {"signals": planned, "orders": len(claimed)}


def reset_circuit(account: BrokerAccount) -> None:
    account.circuit_open = False
    account.consecutive_failures = 0
    account.connection_message = None
