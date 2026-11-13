"""Test setup: a real PostgreSQL schema built by the migrations, cleaned between tests.

The application connects as the restricted runtime role, exactly as in
production, so the row-level security and privilege tests are meaningful.
"""

import base64
import os

# Settings are read at import time, so the environment is prepared first.
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", "postgresql+psycopg://tp_app:tp_app_dev@127.0.0.1:5432/tradeplatform_test"
        ),
        "MIGRATIONS_DATABASE_URL": os.environ.get(
            "TEST_MIGRATIONS_DATABASE_URL",
            "postgresql+psycopg://tp_owner:tp_owner_dev@127.0.0.1:5432/tradeplatform_test",
        ),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/15"),
        "SECRET_KEY": "test-secret-key-0123456789-0123456789-0123456789",
        "DATA_ENCRYPTION_KEYS": "k1:" + base64.b64encode(b"\x01" * 32).decode(),
        "DATA_ENCRYPTION_ACTIVE_KEY": "k1",
        "COOKIE_SECURE": "false",
        "EMAIL_BACKEND": "memory",
        "RATE_LIMIT_BACKEND": "memory",
        # Cheapest valid Argon2 settings: the tests exercise logic, not hashing strength.
        "ARGON2_TIME_COST": "1",
        "ARGON2_MEMORY_KIB": "8",
        "ARGON2_PARALLELISM": "1",
        "FRONTEND_ORIGIN": "http://localhost:3000",
        "LIVE_TRADING_ENABLED": "false",
        "LIVE_TRADING_COMPLIANCE_APPROVAL": "",
        "SENTRY_DSN": "",
    }
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.db import Base  # noqa: E402
from app.core.email import get_email_sender  # noqa: E402
from app.core.ratelimit import get_limiter  # noqa: E402
from app.main import app  # noqa: E402
from tests import helpers  # noqa: E402


@pytest.fixture(scope="session")
def owner_engine():
    engine = create_engine(os.environ["MIGRATIONS_DATABASE_URL"], poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema(owner_engine):
    with owner_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini")), "head")
    with owner_engine.begin() as conn:
        # Lets the fixture below empty the audit table. Production keeps this
        # trigger on; a test re-enables it to prove it works.
        conn.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_truncate"))
    helpers.OWNER_ENGINE = owner_engine


@pytest.fixture(autouse=True)
def _clean(owner_engine, monkeypatch):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with owner_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    get_limiter().clear()
    get_email_sender().outbox.clear()
    helpers.pin_totp_clock(monkeypatch)


@pytest.fixture
def browser():
    """A fresh browser (cookie jar + CSRF token), not signed in."""
    return helpers.Browser(app)
