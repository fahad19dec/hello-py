"""Broker accounts, strategies, webhooks, signals, orders, positions and risk limits.

Every table carries `user_id` (the tenant) and is protected by row-level
security in migration 0002. Quantities and prices are `Numeric`, never float.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

QTY = Numeric(24, 8)
PRICE = Numeric(24, 8)
MONEY = Numeric(24, 4)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def _owner() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Broker(StrEnum):
    PAPER = "paper"
    KITE = "kite"
    MT5 = "mt5"
    MT4 = "mt4"


BRIDGE_BROKERS = {Broker.MT5.value, Broker.MT4.value}


class OrderState(StrEnum):
    VALIDATED = "VALIDATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    QUEUED = "QUEUED"
    EXECUTING = "EXECUTING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    EXPIRED = "EXPIRED"
    APPLIED = "APPLIED"  # a modify or cancel instruction that took effect


TERMINAL_STATES = {
    OrderState.FILLED.value,
    OrderState.CANCELLED.value,
    OrderState.REJECTED.value,
    OrderState.FAILED.value,
    OrderState.EXPIRED.value,
    OrderState.APPLIED.value,
}
# Orders whose outcome at the broker is not settled yet.
OPEN_STATES = {
    OrderState.QUEUED.value,
    OrderState.EXECUTING.value,
    OrderState.SUBMITTED.value,
    OrderState.PARTIALLY_FILLED.value,
    OrderState.UNKNOWN.value,
}

_STATES_SQL = ", ".join(f"'{s.value}'" for s in OrderState)


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_broker_accounts_user_id_label"),
        CheckConstraint("broker IN ('paper', 'kite', 'mt5', 'mt4')", name="broker_valid"),
        CheckConstraint("status IN ('active', 'paused', 'archived')", name="status_valid"),
        CheckConstraint(
            "connection_status IN ('not_connected', 'connected', 'login_required', 'error')",
            name="connection_status_valid",
        ),
        Index("ix_broker_accounts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    broker: Mapped[str] = mapped_column(String(10), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", server_default="active")
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    kill_switch_reason: Mapped[str | None] = mapped_column(String(300))
    # Non-secret settings: Zerodha api_key, paper costs, MetaTrader symbol suffix, ...
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    connection_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_connected", server_default="not_connected"
    )
    connection_message: Mapped[str | None] = mapped_column(String(300))
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Latest account snapshot reported by the broker (or computed, for paper).
    currency: Mapped[str | None] = mapped_column(String(10))
    balance: Mapped[Decimal | None] = mapped_column(MONEY)
    equity: Mapped[Decimal | None] = mapped_column(MONEY)
    margin_available: Mapped[Decimal | None] = mapped_column(MONEY)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Circuit breaker: consecutive execution failures since the last success or reset.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    circuit_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class BrokerCredential(Base):
    """Secrets for a broker account. Encrypted (or hashed) and never visible to staff."""

    __tablename__ = "broker_credentials"
    __table_args__ = (
        UniqueConstraint("broker_account_id", "kind", name="uq_broker_credentials_broker_account_id_kind"),
        CheckConstraint("kind IN ('kite_api_secret', 'kite_access_token', 'bridge_device_token')", name="kind_valid"),
        Index("ix_broker_credentials_token_hash", "token_hash"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    value_enc: Mapped[str | None] = mapped_column(Text)  # secrets that must be read back
    token_hash: Mapped[str | None] = mapped_column(String(64))  # secrets that are only checked
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (
        UniqueConstraint("user_id", "code", name="uq_strategies_user_id_code"),
        CheckConstraint("status IN ('active', 'paused', 'archived')", name="status_valid"),
        Index("ix_strategies_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    # The strategy_id that alerts carry, chosen by the user.
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", server_default="active")
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Alert action names mapped to standard ones, e.g. {"LONG": "BUY", "EXIT_LONG": "CLOSE_LONG"}.
    action_aliases: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    default_order_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default="MARKET", server_default="MARKET"
    )
    default_stop_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    default_take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class StrategyVersion(Base):
    """A snapshot of a strategy's configuration after every change."""

    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_id_version"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _owner()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()


class StrategyAccount(Base):
    """Which accounts a strategy trades, and how each one sizes and approves orders."""

    __tablename__ = "strategy_accounts"
    __table_args__ = (
        UniqueConstraint("strategy_id", "broker_account_id", name="uq_strategy_accounts_strategy_id_broker_account_id"),
        CheckConstraint(
            "sizing_mode IN ('signal', 'fixed', 'percent_capital', 'risk_per_trade')", name="sizing_mode_valid"
        ),
        Index("ix_strategy_accounts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    # Semi-automatic: each order waits for the user's approval.
    approval_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sizing_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="signal", server_default="signal")
    sizing_value: Mapped[Decimal | None] = mapped_column(QTY)
    created_at: Mapped[datetime] = _created_at()


class InstrumentMapping(Base):
    """How an alert's symbol maps to a broker instrument, with its trading rules."""

    __tablename__ = "instrument_mappings"
    __table_args__ = (
        UniqueConstraint(
            "broker_account_id", "alert_symbol", name="uq_instrument_mappings_broker_account_id_alert_symbol"
        ),
        Index("ix_instrument_mappings_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    alert_symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(10))
    product: Mapped[str | None] = mapped_column(String(10))
    lot_size: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal(1), server_default=text("1"))
    quantity_step: Mapped[Decimal | None] = mapped_column(QTY)
    min_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    max_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    tick_size: Mapped[Decimal | None] = mapped_column(PRICE)
    # Value of a one-unit price move for one quantity unit (one lot on MetaTrader).
    contract_size: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal(1), server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()


class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_webhooks_token_hash"),
        Index("ix_webhooks_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    passphrase_hash: Mapped[str | None] = mapped_column(String(64))
    # Optional HMAC key for senders that can sign requests (TradingView cannot).
    signing_secret_enc: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class Signal(Base):
    """Every alert that reached a valid webhook, accepted or not."""

    __tablename__ = "signals"
    __table_args__ = (
        # The idempotency guarantee: one accepted signal per signal id per webhook. Rejected
        # attempts (bad signature, stale, malformed) do not claim the id, so they cannot
        # block the genuine alert.
        Index(
            "uq_signals_webhook_id_client_signal_id",
            "webhook_id",
            "client_signal_id",
            unique=True,
            postgresql_where=text("status <> 'rejected'"),
        ),
        CheckConstraint("status IN ('received', 'processed', 'ignored', 'rejected')", name="status_valid"),
        Index("ix_signals_user_id_received_at", "user_id", "received_at"),
        Index("ix_signals_status", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL")
    )
    client_signal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    signal_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action: Mapped[str | None] = mapped_column(String(20))
    symbol: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_duplicate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_ip: Mapped[str | None] = mapped_column(String(45))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        # One order per signal, account and leg: a signal can never be executed twice on an account.
        UniqueConstraint("signal_id", "broker_account_id", "leg", name="uq_orders_signal_id_broker_account_id_leg"),
        UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        CheckConstraint(f"state IN ({_STATES_SQL})", name="state_valid"),
        CheckConstraint(
            "intent IN ('open', 'close', 'close_all', 'modify_sl', 'modify_tp', 'cancel')", name="intent_valid"
        ),
        CheckConstraint("side IS NULL OR side IN ('BUY', 'SELL')", name="side_valid"),
        CheckConstraint("order_type IN ('MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT')", name="order_type_valid"),
        CheckConstraint("source IN ('signal', 'manual', 'protective')", name="source_valid"),
        Index("ix_orders_user_id_created_at", "user_id", "created_at"),
        Index("ix_orders_state_next_attempt_at", "state", "next_attempt_at"),
        Index("ix_orders_broker_account_id_state", "broker_account_id", "state"),
        Index("ix_orders_broker_order_id", "broker_order_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.id", ondelete="SET NULL")
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL")
    )
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="signal", server_default="signal")
    leg: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    # Sent to the broker (Zerodha tag, MetaTrader comment) so a lost reply can be traced.
    client_order_id: Mapped[str] = mapped_column(String(20), nullable=False)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False)
    intent: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str | None] = mapped_column(String(4))
    position_side: Mapped[str | None] = mapped_column(String(5))  # for close: 'long', 'short' or None (both)
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(10))
    product: Mapped[str | None] = mapped_column(String(10))
    order_type: Mapped[str] = mapped_column(String(10), nullable=False, default="MARKET", server_default="MARKET")
    quantity: Mapped[Decimal | None] = mapped_column(QTY)
    price: Mapped[Decimal | None] = mapped_column(PRICE)
    trigger_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit: Mapped[Decimal | None] = mapped_column(PRICE)
    reference_price: Mapped[Decimal | None] = mapped_column(PRICE)
    max_slippage_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    rule: Mapped[str | None] = mapped_column(String(40))  # the risk rule that rejected it
    broker_order_id: Mapped[str | None] = mapped_column(String(64))
    filled_quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal(0), server_default=text("0"))
    average_price: Mapped[Decimal | None] = mapped_column(PRICE)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # do not execute after this
    created_at: Mapped[datetime] = _created_at()
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated_at()


class OrderEvent(Base):
    """Every state change of an order. Append-only."""

    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_order_id", "order_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _owner()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(String(500))
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class Fill(Base):
    """An execution reported by a broker (or by the paper engine)."""

    __tablename__ = "fills"
    __table_args__ = (
        UniqueConstraint("order_id", "broker_trade_id", name="uq_fills_order_id_broker_trade_id"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="side_valid"),
        Index("ix_fills_user_id_filled_at", "user_id", "filled_at"),
        Index("ix_fills_broker_account_id_filled_at", "broker_account_id", "filled_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0), server_default=text("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0), server_default=text("0"))
    broker_trade_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Position(Base):
    """Net position per account and instrument, maintained from fills."""

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("broker_account_id", "symbol", name="uq_positions_broker_account_id_symbol"),
        Index("ix_positions_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)  # the broker symbol
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal(0), server_default=text("0"))
    average_price: Mapped[Decimal | None] = mapped_column(PRICE)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0), server_default=text("0"))
    last_price: Mapped[Decimal | None] = mapped_column(PRICE)
    contract_size: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal(1), server_default=text("1"))
    stop_loss: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit: Mapped[Decimal | None] = mapped_column(PRICE)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False)
    broker_quantity: Mapped[Decimal | None] = mapped_column(QTY)  # as last reported by the broker
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated_at()


class RiskLimits(Base):
    """Limits at one scope. Empty columns mean "no limit at this scope".

    scope_key is 'global', 'account:<uuid>' or 'strategy:<uuid>'. When several
    scopes apply to an order, the most restrictive value of each limit wins.
    """

    __tablename__ = "risk_limits"
    __table_args__ = (
        UniqueConstraint("user_id", "scope_key", name="uq_risk_limits_user_id_scope_key"),
        CheckConstraint(
            "scope_key = 'global' OR scope_key LIKE 'account:%' OR scope_key LIKE 'strategy:%'",
            name="scope_key_valid",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    scope_key: Mapped[str] = mapped_column(String(50), nullable=False)
    max_order_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    max_order_lots: Mapped[Decimal | None] = mapped_column(QTY)
    max_open_positions: Mapped[int | None] = mapped_column(Integer)
    max_daily_loss: Mapped[Decimal | None] = mapped_column(MONEY)
    max_daily_trades: Mapped[int | None] = mapped_column(Integer)
    max_exposure: Mapped[Decimal | None] = mapped_column(MONEY)
    max_slippage_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    max_signal_age_seconds: Mapped[int | None] = mapped_column(Integer)
    max_consecutive_failures: Mapped[int | None] = mapped_column(Integer)
    min_available_margin: Mapped[Decimal | None] = mapped_column(MONEY)
    allowed_symbols: Mapped[list[str] | None] = mapped_column(ARRAY(String(60)))
    # [{"days": [0,1,2,3,4], "start": "09:15", "end": "15:30", "timezone": "Asia/Kolkata"}]
    trading_sessions: Mapped[list | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _updated_at()


class TradingControl(Base):
    """The user's own emergency stop for all of their accounts."""

    __tablename__ = "trading_controls"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    kill_switch_reason: Mapped[str | None] = mapped_column(String(300))
    kill_switch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated_at()


class PlatformControl(Base):
    """Platform-wide emergency halt, controlled by super admins. A single row (id = 1)."""

    __tablename__ = "platform_controls"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    trading_halted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(String(300))
    # Who last changed it (no foreign key: the row must outlive any one account; the audit log has the details).
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = _updated_at()
