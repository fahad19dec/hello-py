"""Pre-trade risk checks. Every order passes through `check` before it can be queued.

A failed check rejects the order and records the rule and the values compared.
Nothing is adjusted on the user's behalf.

Limits come from three scopes (the user's global limits, the account's and the
strategy's); for each limit the most restrictive value applies.

Entries (orders that open or add to a position) face every check. Exits
(closing, modifying stops, cancelling) face only the checks that mean "stop
trading": halts, kill switches, the circuit breaker, the live-trading gate,
the broker connection, signal age and market hours. Blocking an exit because
of a loss limit would leave the user unable to reduce risk.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.trading import (
    BRIDGE_BROKERS,
    BrokerAccount,
    Fill,
    Order,
    OrderState,
    PlatformControl,
    Position,
    RiskLimits,
    Strategy,
    TradingControl,
)

NUMERIC_LIMITS = (
    "max_order_quantity",
    "max_order_lots",
    "max_open_positions",
    "max_daily_loss",
    "max_daily_trades",
    "max_exposure",
    "max_slippage_pct",
    "max_signal_age_seconds",
    "max_consecutive_failures",
)
# min_available_margin: the highest requirement wins.

IST = ZoneInfo("Asia/Kolkata")
# Regular market hours when the user has not set sessions of their own.
# Exchange holidays are not known here; the broker rejects those orders.
DEFAULT_KITE_SESSIONS = {
    "MCX": (time(9, 0), time(23, 30)),
    "CDS": (time(9, 0), time(17, 0)),
    "BCD": (time(9, 0), time(17, 0)),
}
DEFAULT_KITE_SESSION = (time(9, 15), time(15, 30))


@dataclass
class EffectiveLimits:
    values: dict = field(default_factory=dict)
    allowed_symbols: set[str] | None = None
    sessions: list[list[dict]] = field(default_factory=list)  # every scope's sessions must allow the time

    def get(self, name: str):
        return self.values.get(name)


def load_limits(db: Session, user_id, account_id, strategy_id) -> EffectiveLimits:
    keys = ["global", f"account:{account_id}"]
    if strategy_id:
        keys.append(f"strategy:{strategy_id}")
    rows = db.scalars(select(RiskLimits).where(RiskLimits.user_id == user_id, RiskLimits.scope_key.in_(keys))).all()
    limits = EffectiveLimits()
    for row in rows:
        for name in NUMERIC_LIMITS:
            value = getattr(row, name)
            if value is not None:
                current = limits.values.get(name)
                limits.values[name] = value if current is None else min(current, value)
        if row.min_available_margin is not None:
            current = limits.values.get("min_available_margin")
            limits.values["min_available_margin"] = (
                row.min_available_margin if current is None else max(current, row.min_available_margin)
            )
        if row.allowed_symbols is not None:
            symbols = {s.upper() for s in row.allowed_symbols}
            limits.allowed_symbols = symbols if limits.allowed_symbols is None else limits.allowed_symbols & symbols
        if row.trading_sessions:
            limits.sessions.append(row.trading_sessions)
    return limits


@dataclass(frozen=True)
class Rejection:
    rule: str
    message: str
    detail: dict = field(default_factory=dict)


def _in_session(sessions: list[dict], now: datetime) -> bool:
    for session in sessions:
        zone = ZoneInfo(session.get("timezone") or "UTC")
        local = now.astimezone(zone)
        if local.weekday() not in session.get("days", [0, 1, 2, 3, 4, 5, 6]):
            continue
        start = time.fromisoformat(session["start"])
        end = time.fromisoformat(session["end"])
        if start <= local.time() <= end:
            return True
    return False


def start_of_day(now: datetime, zone: str) -> datetime:
    local = now.astimezone(ZoneInfo(zone))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def daily_realised_loss(db: Session, account_id, since: datetime) -> Decimal:
    """Today's realised loss on the account, including fees, as a positive number (0 if in profit)."""
    pnl, fees = db.execute(
        select(func.coalesce(func.sum(Fill.realized_pnl), 0), func.coalesce(func.sum(Fill.fee), 0)).where(
            Fill.broker_account_id == account_id, Fill.filled_at >= since
        )
    ).one()
    net = Decimal(pnl) - Decimal(fees)
    return -net if net < 0 else Decimal(0)


def trades_today(db: Session, account_id, since: datetime, exclude_order_id=None) -> int:
    rejected = (OrderState.REJECTED.value, OrderState.EXPIRED.value, OrderState.CANCELLED.value)
    query = select(func.count(Order.id)).where(
        Order.broker_account_id == account_id,
        Order.intent == "open",
        Order.created_at >= since,
        Order.state.not_in(rejected),
    )
    if exclude_order_id is not None:
        query = query.where(Order.id != exclude_order_id)
    return db.scalar(query)


def platform_halted(db: Session) -> PlatformControl | None:
    control = db.get(PlatformControl, 1)
    return control if control is not None and control.trading_halted else None


@dataclass
class OrderDraft:
    intent: str
    symbol: str  # broker symbol
    exchange: str | None
    quantity: Decimal | None
    price: Decimal | None  # limit price or reference price
    signal_time: datetime | None
    is_entry: bool


def stop_check(db: Session, *, account: BrokerAccount, strategy: Strategy | None, now: datetime) -> Rejection | None:
    """The checks that mean "stop trading now". Re-run just before anything is sent."""
    settings = get_settings()
    halted = platform_halted(db)
    if halted:
        return Rejection("platform_halted", f"trading is halted on the platform: {halted.reason or 'no reason given'}")
    control = db.get(TradingControl, account.user_id)
    if control is not None and control.kill_switch:
        return Rejection(
            "global_kill_switch", f"your kill switch is on: {control.kill_switch_reason or 'no reason given'}"
        )
    if account.status != "active":
        return Rejection("account_paused", f"account '{account.label}' is {account.status}")
    if account.kill_switch:
        return Rejection(
            "account_kill_switch",
            f"kill switch is on for '{account.label}': {account.kill_switch_reason or 'no reason given'}",
        )
    if strategy is not None and strategy.kill_switch:
        return Rejection("strategy_kill_switch", f"kill switch is on for strategy '{strategy.name}'")
    if account.circuit_open:
        return Rejection(
            "circuit_breaker",
            f"'{account.label}' stopped after {account.consecutive_failures} failed orders in a row; "
            "check the account, then reset it",
        )
    if account.broker != "paper":
        if not settings.live_trading_enabled:
            return Rejection(
                "live_trading_disabled", "live trading is switched off on this platform; use a paper account"
            )
        connected = _connection_problem(account, now)
        if connected:
            return Rejection("broker_not_connected", connected)
    elif not settings.paper_trading_enabled:
        return Rejection("paper_trading_disabled", "paper trading is switched off on this platform")
    return None


def check(
    db: Session,
    *,
    account: BrokerAccount,
    strategy: Strategy | None,
    draft: OrderDraft,
    user_timezone: str,
    now: datetime,
    skip_signal_age: bool = False,
    exclude_order_id=None,
) -> Rejection | None:
    settings = get_settings()
    stopped = stop_check(db, account=account, strategy=strategy, now=now)
    if stopped:
        return stopped

    limits = load_limits(db, account.user_id, account.id, strategy.id if strategy else None)

    if not skip_signal_age and draft.signal_time is not None:
        max_age = limits.get("max_signal_age_seconds") or settings.default_max_signal_age_seconds
        age = (now - draft.signal_time).total_seconds()
        if age > max_age:
            return Rejection("signal_too_old", f"the alert is {age:.0f}s old; the limit is {max_age}s", {"age": age})

    if account.broker == "kite":
        closed = kite_market_closed(draft.exchange, now)
        if closed:
            return Rejection("market_closed", closed)

    if not draft.is_entry:
        return None

    for sessions in limits.sessions:
        if not _in_session(sessions, now):
            return Rejection("outside_trading_session", "outside your allowed trading sessions")
    if limits.allowed_symbols is not None and draft.symbol.upper() not in limits.allowed_symbols:
        return Rejection("symbol_not_allowed", f"{draft.symbol} is not in your allowed instruments")

    quantity = draft.quantity or Decimal(0)
    if account.broker in BRIDGE_BROKERS:
        cap = limits.get("max_order_lots")
        if cap is not None and quantity > cap:
            return Rejection("max_order_lots", f"{quantity.normalize()} lots is above the limit of {cap.normalize()}")
    else:
        cap = limits.get("max_order_quantity")
        if cap is not None and quantity > cap:
            return Rejection(
                "max_order_quantity", f"quantity {quantity.normalize()} is above the limit of {cap.normalize()}"
            )

    open_positions = (
        db.execute(select(Position.symbol).where(Position.broker_account_id == account.id, Position.quantity != 0))
        .scalars()
        .all()
    )
    max_positions = limits.get("max_open_positions")
    if max_positions is not None and draft.symbol not in open_positions and len(open_positions) >= max_positions:
        return Rejection(
            "max_open_positions", f"{len(open_positions)} positions are open; the limit is {max_positions}"
        )

    since = start_of_day(now, user_timezone)
    max_trades = limits.get("max_daily_trades")
    if max_trades is not None:
        count = trades_today(db, account.id, since, exclude_order_id)
        if count >= max_trades:
            return Rejection("max_daily_trades", f"{count} entries today; the limit is {max_trades}")

    max_loss = limits.get("max_daily_loss")
    if max_loss is not None:
        loss = daily_realised_loss(db, account.id, since)
        if loss >= max_loss:
            return Rejection(
                "daily_loss_limit",
                f"today's realised loss {loss:.2f} has reached the limit of {max_loss:.2f}; new entries are locked",
            )

    max_exposure = limits.get("max_exposure")
    if max_exposure is not None:
        if draft.price is None:
            return Rejection("max_exposure", "the exposure limit cannot be checked without a price in the alert")
        positions = db.scalars(
            select(Position).where(Position.broker_account_id == account.id, Position.quantity != 0)
        ).all()
        current = sum(
            (abs(p.quantity) * (p.last_price or p.average_price or Decimal(0)) * p.contract_size for p in positions),
            Decimal(0),
        )
        added = quantity * draft.price
        if current + added > max_exposure:
            return Rejection(
                "max_exposure", f"exposure would be {current + added:.2f}; the limit is {max_exposure:.2f}"
            )

    min_margin = limits.get("min_available_margin")
    if min_margin is not None:
        if account.margin_available is None:
            return Rejection("min_available_margin", "available margin is not known yet for this account")
        if account.margin_available < min_margin:
            return Rejection(
                "min_available_margin",
                f"available margin {account.margin_available:.2f} is below the minimum {min_margin:.2f}",
            )
    return None


def kite_market_closed(exchange: str | None, now: datetime) -> str | None:
    """Why an Indian exchange is closed at `now`, or None if it is open (holidays are not known here)."""
    start, end = DEFAULT_KITE_SESSIONS.get(exchange or "", DEFAULT_KITE_SESSION)
    local = now.astimezone(IST)
    if local.weekday() >= 5 or not (start <= local.time() <= end):
        return f"{exchange or 'NSE'} is closed ({local:%a %H:%M} IST; open {start:%H:%M}-{end:%H:%M} Mon-Fri)"
    return None


def _connection_problem(account: BrokerAccount, now: datetime) -> str | None:
    settings = get_settings()
    if account.broker == "kite":
        if account.connection_status != "connected":
            return f"'{account.label}' is not logged in to Zerodha today"
        return None
    if account.broker in BRIDGE_BROKERS:
        if account.last_heartbeat_at is None:
            return f"the terminal for '{account.label}' has never connected"
        silent = (now - account.last_heartbeat_at).total_seconds()
        if silent > settings.bridge_offline_after_seconds:
            return f"the terminal for '{account.label}' has not been heard from for {silent:.0f}s"
    return None


def failure_threshold(db: Session, account: BrokerAccount, strategy_id=None) -> int:
    limits = load_limits(db, account.user_id, account.id, strategy_id)
    return int(limits.get("max_consecutive_failures") or get_settings().default_max_consecutive_failures)


def approval_deadline(now: datetime) -> datetime:
    return now + timedelta(seconds=get_settings().approval_window_seconds)


# Rules that still apply to orders that reduce risk (a manual close, a simulated stop loss).
HARD_RULES = {"live_trading_disabled", "paper_trading_disabled", "broker_not_connected"}


def blocks(source: str, rejection: Rejection | None) -> bool:
    """Whether a stop-trading rejection stops this order. Closing is always allowed unless it is impossible."""
    if rejection is None:
        return False
    if source in ("manual", "protective"):
        return rejection.rule in HARD_RULES
    return True
