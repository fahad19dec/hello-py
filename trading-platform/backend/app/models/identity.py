"""Accounts, credentials, sessions and single-use tokens."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserRole(StrEnum):
    USER = "user"
    SUPPORT_ADMIN = "support_admin"
    SUPER_ADMIN = "super_admin"


STAFF_ROLES = {UserRole.SUPPORT_ADMIN.value, UserRole.SUPER_ADMIN.value}


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class EmailTokenPurpose(StrEnum):
    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"  # noqa: S105 (a token kind, not a password)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    """An account. One user is one tenant: everything they own carries their id."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'support_admin', 'super_admin')", name="role_valid"),
        CheckConstraint("status IN ('active', 'suspended')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user", server_default="user")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mirror of "two-factor is on" for display. The source of truth that sign-in
    # checks is user_credentials.totp_enabled_at, which staff cannot read.
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserCredential(Base):
    """Sign-in secrets, kept apart from the profile so staff access never reaches them."""

    __tablename__ = "user_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)  # encrypted; set during setup
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last accepted time step, so a one-time code cannot be used twice.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)


class AuthSession(Base):
    """A signed-in browser. Only a hash of the cookie value is stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(40))
    # True between a correct password and a correct second factor. A pending
    # session can only finish or abandon the sign-in.
    pending_mfa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    mfa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_failed_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class EmailToken(Base):
    """Single-use link token for email verification and password reset."""

    __tablename__ = "email_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('verify_email', 'reset_password')", name="purpose_valid"),
        Index("ix_email_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryCode(Base):
    """Backup code for signing in when the authenticator app is unavailable."""

    __tablename__ = "recovery_codes"
    __table_args__ = (UniqueConstraint("user_id", "code_hash", name="uq_recovery_codes_user_id_code_hash"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
