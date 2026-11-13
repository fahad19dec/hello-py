"""Database engine, ORM base, and the access context that drives row-level security.

Tenant isolation has two layers:

1. Application code filters every query by the owning user.
2. PostgreSQL row-level security (RLS) enforces the same rule again. Every
   transaction tells the database who it is acting for through two
   transaction-local settings, `app.user_id` and `app.access_mode`; the
   policies created in the migrations read them.

A session starts with no context, which matches no rows at all, so code that
forgets to establish a context fails closed instead of leaking data.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


AccessMode = Literal["none", "tenant", "staff", "system"]


@dataclass(frozen=True)
class AccessContext:
    """Who a transaction acts for.

    - none:   no access (the default; matches no rows)
    - tenant: an authenticated user acting on their own data
    - staff:  an admin acting through the admin API; never sees credential tables
    - system: trusted internal code (sign-in, token flows, background workers)
    """

    mode: AccessMode = "none"
    user_id: uuid.UUID | None = None


NO_ACCESS = AccessContext()
SYSTEM = AccessContext(mode="system")


def tenant_context(user_id: uuid.UUID) -> AccessContext:
    return AccessContext(mode="tenant", user_id=user_id)


def staff_context(staff_user_id: uuid.UUID) -> AccessContext:
    return AccessContext(mode="staff", user_id=staff_user_id)


def _apply(connection: Connection, ctx: AccessContext) -> None:
    # set_config(..., true) scopes the value to the current transaction, so it
    # can never leak to another request through the connection pool.
    connection.execute(
        text("SELECT set_config('app.user_id', :uid, true), set_config('app.access_mode', :mode, true)"),
        {"uid": str(ctx.user_id) if ctx.user_id else "", "mode": ctx.mode},
    )


def set_access_context(session: Session, ctx: AccessContext) -> None:
    """Set the context for the rest of this session, including later transactions."""
    session.info["access_ctx"] = ctx
    if session.in_transaction():
        _apply(session.connection(), ctx)


def current_access_context(session: Session) -> AccessContext:
    return session.info.get("access_ctx", NO_ACCESS)


@contextmanager
def access(session: Session, ctx: AccessContext) -> Iterator[None]:
    """Temporarily switch context, restoring the previous one afterwards."""
    previous = current_access_context(session)
    set_access_context(session, ctx)
    try:
        yield
    finally:
        set_access_context(session, previous)


@event.listens_for(Session, "after_begin")
def _reapply_context(session: Session, transaction, connection: Connection) -> None:
    # A commit ends the transaction and with it the transaction-local settings;
    # re-apply them whenever the session starts a new transaction.
    _apply(connection, current_access_context(session))


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def new_session() -> Session:
    return _session_factory()()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, starting with no access."""
    db = new_session()
    try:
        yield db
    finally:
        db.close()
