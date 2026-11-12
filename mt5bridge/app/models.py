from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    webhook_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    accounts: Mapped[list["Account"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    label: Mapped[str] = mapped_column(String(50))
    broker: Mapped[str] = mapped_column(String(8), default="mt5")  # mt5 / kite
    # MT5 credentials (empty for Zerodha accounts).
    login: Mapped[str] = mapped_column(String(32), default="")
    server: Mapped[str] = mapped_column(String(100), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    # Zerodha Kite Connect credentials (empty for MT5 accounts). The access
    # token comes from the customer's daily Zerodha login and expires each morning.
    api_key: Mapped[str] = mapped_column(String(64), default="")
    api_secret_enc: Mapped[str] = mapped_column(Text, default="")
    access_token_enc: Mapped[str] = mapped_column(Text, default="")
    token_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    max_quantity: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_lots: Mapped[float] = mapped_column(Float, default=1.0)
    # Appended to alert symbols, e.g. "m" turns XAUUSD into XAUUSDm.
    symbol_suffix: Mapped[str] = mapped_column(String(16), default="")
    # pending -> connected / error, reported by the worker.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    status_message: Mapped[str] = mapped_column(String(255), default="")
    balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="accounts")


class Alert(Base):
    """One webhook call, kept so users can see why an alert did or did not trade."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    raw: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # accepted / rejected
    error: Mapped[str] = mapped_column(String(500), default="")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    action: Mapped[str] = mapped_column(String(8))  # buy / sell / close
    symbol: Mapped[str] = mapped_column(String(40))
    lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Zerodha only: quantity in shares/contracts, exchange, product, order type.
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exchange: Mapped[str] = mapped_column(String(8), default="")
    product: Mapped[str] = mapped_column(String(8), default="")
    order_type: Mapped[str] = mapped_column(String(8), default="")
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    reverse: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(String(31), default="")
    # MT5: queued -> claimed -> filled / failed; queued -> expired.
    # Zerodha: queued -> placed / failed. Either: rejected at intake.
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    message: Mapped[str] = mapped_column(String(500), default="")
    ticket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account: Mapped[Account] = relationship()
