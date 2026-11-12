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

    accounts: Mapped[list["MT5Account"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class MT5Account(Base):
    __tablename__ = "mt5_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    label: Mapped[str] = mapped_column(String(50))
    login: Mapped[str] = mapped_column(String(32))
    server: Mapped[str] = mapped_column(String(100))
    password_enc: Mapped[str] = mapped_column(Text)
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
    account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), index=True)
    action: Mapped[str] = mapped_column(String(8))  # buy / sell / close
    symbol: Mapped[str] = mapped_column(String(40))
    lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    reverse: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(String(31), default="")
    # queued -> claimed -> filled / failed; queued -> expired; rejected at intake.
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    message: Mapped[str] = mapped_column(String(500), default="")
    ticket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account: Mapped[MT5Account] = relationship()
