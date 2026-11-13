"""Tenant isolation, tested at the API and again underneath it at the database.

The database tests bypass the application's own filters on purpose: they run
raw SQL as the application role to prove that PostgreSQL itself refuses
cross-tenant access, whatever the code above it does.
"""

import pathlib
import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import NO_ACCESS, SYSTEM, AccessContext, new_session, set_access_context, staff_context, tenant_context
from app.main import app
from tests.helpers import Browser, enable_mfa, owner_execute, signed_in, staff_member, system_scalar, user_id

ALICE = "alice@example.com"
BOB = "bob@example.com"
TENANT_TABLES = ("sessions", "user_credentials", "email_tokens", "recovery_codes", "audit_logs")


def run(ctx: AccessContext, sql: str, **params):
    """Execute raw SQL as the application role in the given access context."""
    db = new_session()
    try:
        set_access_context(db, ctx)
        result = db.execute(text(sql), params)
        rows = result.all() if result.returns_rows else result.rowcount
        db.commit()
        return rows
    finally:
        db.close()


@pytest.fixture
def two_users():
    alice, bob = signed_in(ALICE), signed_in(BOB)
    enable_mfa(alice)
    enable_mfa(bob)
    return alice, bob, user_id(ALICE), user_id(BOB)


# ----------------------------------------------------------------------- API


def test_api_a_user_sees_only_their_own_sessions_and_activity(two_users):
    alice, bob, alice_id, bob_id = two_users
    assert len(alice.get("/auth/sessions").json()) == 1
    assert alice.get("/auth/me").json()["user"]["id"] == str(alice_id)
    bob.patch("/users/me", json={"display_name": "Bob"})
    actions = [a["action"] for a in alice.get("/users/me/activity").json()]
    assert "user.profile.updated" not in actions and actions


def test_api_a_user_cannot_end_someone_elses_session(two_users):
    alice, bob, _, _ = two_users
    bob_session = bob.get("/auth/me").json()["session_id"]
    response = alice.delete(f"/auth/sessions/{bob_session}")
    assert response.status_code == 404  # indistinguishable from an id that does not exist
    assert alice.delete(f"/auth/sessions/{uuid.uuid4()}").status_code == 404
    assert bob.get("/auth/me").status_code == 200


def test_api_every_route_requires_a_session_unless_explicitly_public():
    public = {
        ("GET", "/api/v1/health/live"),
        ("GET", "/api/v1/health/ready"),
        ("GET", "/api/v1/system/status"),
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/verify-email"),
        ("POST", "/api/v1/auth/resend-verification"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/password/forgot"),
        ("POST", "/api/v1/auth/password/reset"),
        # Machine endpoints with their own credential (webhook token, Zerodha checksum, device token):
        ("POST", "/api/v1/hooks/{token}"),
        ("POST", "/api/v1/hooks/kite/postback"),
        ("POST", "/api/v1/bridge/poll"),
        ("POST", "/api/v1/bridge/result"),
    }
    visitor = Browser()
    checked = 0
    # The OpenAPI document lists every route the application serves.
    for template, operations in app.openapi()["paths"].items():
        path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), template)
        for method in operations:
            if (method.upper(), template) in public:
                continue
            response = visitor.http.request(method.upper(), path, json={})
            assert response.status_code == 401, f"{method.upper()} {template} is reachable without signing in"
            checked += 1
    assert checked >= 20
    assert all((method.lower() in app.openapi()["paths"][path]) for method, path in public)


# ------------------------------------------------------------------ database


def test_db_no_context_means_no_rows(two_users):
    for table in ("users", *TENANT_TABLES):
        assert run(NO_ACCESS, f"SELECT count(*) FROM {table}")[0][0] == 0, table


def test_db_tenant_context_sees_only_its_own_rows(two_users):
    _, _, alice_id, bob_id = two_users
    ctx = tenant_context(alice_id)
    assert run(ctx, "SELECT id FROM users") == [(alice_id,)]
    for table in TENANT_TABLES:
        owners = {row[0] for row in run(ctx, f"SELECT DISTINCT user_id FROM {table}")}
        assert owners <= {alice_id}, table
        assert (
            run(SYSTEM, f"SELECT count(*) FROM {table} WHERE user_id = :b", b=bob_id)[0][0] > 0
            or table == "email_tokens"
        )
    assert run(ctx, "SELECT count(*) FROM sessions")[0][0] == 1


def test_db_tenant_context_cannot_change_another_users_rows(two_users):
    _, bob, alice_id, bob_id = two_users
    ctx = tenant_context(alice_id)
    assert run(ctx, "UPDATE sessions SET revoked_at = now() WHERE user_id = :b", b=bob_id) == 0
    assert run(ctx, "UPDATE user_credentials SET password_hash = 'x' WHERE user_id = :b", b=bob_id) == 0
    assert run(ctx, "UPDATE users SET display_name = 'pwned' WHERE id = :b", b=bob_id) == 0
    assert run(ctx, "DELETE FROM recovery_codes WHERE user_id = :b", b=bob_id) == 0
    assert bob.get("/auth/me").status_code == 200
    assert bob.get("/auth/mfa").json()["recovery_codes_remaining"] == 10


def test_db_tenant_context_cannot_insert_rows_for_another_user(two_users):
    _, _, alice_id, bob_id = two_users
    ctx = tenant_context(alice_id)
    for sql in (
        "INSERT INTO recovery_codes (user_id, code_hash) VALUES (:b, 'abc')",
        "INSERT INTO audit_logs (user_id, actor_user_id, action) VALUES (:b, :a, 'forged')",
        # Own account, but claiming someone else did it:
        "INSERT INTO audit_logs (user_id, actor_user_id, action) VALUES (:a, :b, 'forged')",
        # Sessions and accounts are created only by trusted sign-in code:
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:a, 'h', now() + interval '1 day')",
        "INSERT INTO users (email) VALUES ('intruder@example.com')",
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            run(ctx, sql, a=alice_id, b=bob_id)


def test_db_a_user_cannot_promote_or_unsuspend_themselves(two_users):
    _, _, alice_id, _ = two_users
    ctx = tenant_context(alice_id)
    for sql in (
        "UPDATE users SET role = 'super_admin' WHERE id = :a",
        "UPDATE users SET status = 'suspended' WHERE id = :a",
        "UPDATE users SET email_verified_at = NULL WHERE id = :a",
    ):
        with pytest.raises(DBAPIError, match="can only be changed by staff|sign-up code only"):
            run(ctx, sql, a=alice_id)
    with pytest.raises(DBAPIError, match="permission denied"):
        run(ctx, "UPDATE users SET email = 'new@example.com' WHERE id = :a", a=alice_id)
    assert system_scalar("SELECT role FROM users WHERE id = :a", a=alice_id) == "user"


def test_db_staff_context_never_sees_credentials(two_users):
    _, _, alice_id, _ = two_users
    support_id = user_id_of_new_staff("support@example.com", "support_admin")
    ctx = staff_context(support_id)
    assert run(ctx, "SELECT count(*) FROM users")[0][0] == 3
    assert run(ctx, "SELECT count(*) FROM sessions")[0][0] >= 2
    for table in ("user_credentials", "email_tokens", "recovery_codes"):
        assert run(ctx, f"SELECT count(*) FROM {table}")[0][0] == 0, table
    assert run(ctx, "UPDATE user_credentials SET password_hash = 'x' WHERE user_id = :a", a=alice_id) == 0


def test_db_staff_rules_are_enforced_by_the_database(two_users):
    alice, _, alice_id, bob_id = two_users
    support_id = user_id_of_new_staff("support@example.com", "support_admin")
    root_id = user_id_of_new_staff("root@example.com", "super_admin")
    support, root = staff_context(support_id), staff_context(root_id)

    with pytest.raises(DBAPIError, match="only a super admin can change roles"):
        run(support, "UPDATE users SET role = 'support_admin' WHERE id = :a", a=alice_id)
    with pytest.raises(DBAPIError, match="support admins can only manage regular users"):
        run(support, "UPDATE users SET status = 'suspended' WHERE id = :r", r=root_id)
    with pytest.raises(DBAPIError, match="cannot change their own role"):
        run(root, "UPDATE users SET role = 'user' WHERE id = :r", r=root_id)
    # A staff context whose user is not actually staff gets nowhere.
    with pytest.raises(DBAPIError, match="only staff accounts"):
        run(staff_context(bob_id), "UPDATE users SET role = 'super_admin' WHERE id = :a", a=alice_id)

    # Staff may revoke a session but not rewrite it, and a revoked session stays revoked.
    with pytest.raises(DBAPIError, match="staff can only revoke sessions"):
        run(support, "UPDATE sessions SET token_hash = 'attacker-known' WHERE user_id = :a", a=alice_id)
    with pytest.raises(DBAPIError, match="row-level security"):
        run(
            support,
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:a, 'h', now() + interval '1 day')",
            a=alice_id,
        )
    assert (
        run(support, "UPDATE sessions SET revoked_at = now(), revoked_reason = 'x' WHERE user_id = :a", a=alice_id) == 1
    )
    assert alice.get("/auth/me").status_code == 401
    with pytest.raises(DBAPIError, match="revoked session cannot be changed"):
        run(SYSTEM, "UPDATE sessions SET revoked_at = NULL WHERE user_id = :a", a=alice_id)

    assert run(root, "UPDATE users SET role = 'support_admin' WHERE id = :a", a=alice_id) == 1


def user_id_of_new_staff(email: str, role: str) -> uuid.UUID:
    staff_member(email, role)
    return user_id(email)


def test_db_audit_trail_cannot_be_changed_by_anyone(two_users):
    _, _, alice_id, _ = two_users
    for ctx in (tenant_context(alice_id), SYSTEM):
        with pytest.raises(DBAPIError, match="permission denied"):
            run(ctx, "UPDATE audit_logs SET action = 'tampered'")
        with pytest.raises(DBAPIError, match="permission denied"):
            run(ctx, "DELETE FROM audit_logs")
    # The schema owner is subject to the same policies: its changes match no rows.
    before = system_scalar("SELECT count(*) FROM audit_logs")
    owner_execute("UPDATE audit_logs SET action = 'tampered'")
    owner_execute("DELETE FROM audit_logs")
    assert system_scalar("SELECT count(*) FROM audit_logs") == before
    assert system_scalar("SELECT count(*) FROM audit_logs WHERE action = 'tampered'") == 0
    # And if row-level security were ever switched off, the trigger still refuses.
    owner_execute("ALTER TABLE audit_logs NO FORCE ROW LEVEL SECURITY")
    try:
        for sql in ("UPDATE audit_logs SET action = 'tampered'", "DELETE FROM audit_logs"):
            with pytest.raises(DBAPIError, match="append-only"):
                owner_execute(sql)
    finally:
        owner_execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    owner_execute("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_no_truncate")
    try:
        with pytest.raises(DBAPIError, match="append-only"):
            owner_execute("TRUNCATE audit_logs")
    finally:
        owner_execute("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_truncate")


def test_db_context_does_not_leak_between_requests_and_survives_commits(two_users):
    _, _, alice_id, _ = two_users
    db = new_session()
    try:
        set_access_context(db, tenant_context(alice_id))
        assert db.execute(text("SELECT count(*) FROM sessions")).scalar() == 1
        db.commit()  # ends the transaction that carried the settings
        assert db.execute(text("SELECT count(*) FROM sessions")).scalar() == 1
        db.commit()
    finally:
        db.close()
    # The pooled connection is reused, but the next session starts with no access.
    for _ in range(5):
        assert run(NO_ACCESS, "SELECT count(*) FROM sessions")[0][0] == 0


# ------------------------------------------------------------- structural guards


def test_every_table_has_forced_row_level_security_and_a_policy():
    tables = run(
        SYSTEM,
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
        """,
    )
    assert len(tables) >= 6
    # Tables holding shared reference data must be listed here deliberately.
    shared_tables: set[str] = set()
    for name, enabled, forced, policies in tables:
        if name in shared_tables:
            continue
        assert enabled and forced, f"{name}: row-level security is not enabled and forced"
        assert policies > 0, f"{name}: no policy"


def test_every_table_with_an_owner_column_filters_on_it():
    owned = [
        r[0]
        for r in run(
            SYSTEM,
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'user_id'",
        )
    ]
    assert set(TENANT_TABLES) <= set(owned)
    for table in owned:
        quals = run(
            SYSTEM,
            "SELECT coalesce(qual, '') || ' ' || coalesce(with_check, '') FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = :t",
            t=table,
        )
        assert any("user_id = app_user_id()" in q[0] for q in quals), f"{table}: no policy compares user_id"


def test_application_role_is_unprivileged():
    row = run(
        SYSTEM,
        "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user",
    )[0]
    assert row == (False, False, False, False)
    assert (
        run(SYSTEM, "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user")[0][0]
        == 0
    )
    denied = {
        ("audit_logs", "UPDATE"),
        ("audit_logs", "DELETE"),
        ("audit_logs", "TRUNCATE"),
        ("users", "DELETE"),
        ("users", "TRUNCATE"),
        ("sessions", "DELETE"),
        ("user_credentials", "DELETE"),
    }
    for table, privilege in denied:
        assert not run(SYSTEM, "SELECT has_table_privilege(current_user, :t, :p)", t=table, p=privilege)[0][0], (
            table,
            privilege,
        )
    immutable = {
        ("users", "id"),
        ("users", "email"),
        ("sessions", "user_id"),
        ("sessions", "expires_at"),
        ("user_credentials", "user_id"),
        ("email_tokens", "token_hash"),
        ("email_tokens", "user_id"),
    }
    for table, column in immutable:
        assert not run(SYSTEM, "SELECT has_column_privilege(current_user, :t, :c, 'UPDATE')", t=table, c=column)[0][
            0
        ], (table, column)


def test_the_system_context_is_used_only_where_it_is_allowed():
    """Request-path code may enter the all-seeing context in exactly these files."""
    allowed = {
        "app/core/db.py",
        "app/modules/auth/service.py",
        "app/cli.py",
        # Machine endpoints authenticate with their own credential, then act for its owner.
        "app/modules/hooks/router.py",
        "app/modules/bridge/router.py",
        # The execution engine (workers only).
        "app/trading/engine.py",
    }
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = [
        str(path.relative_to(root))
        for path in (root / "app").rglob("*.py")
        if re.search(r"\bSYSTEM\b", path.read_text()) and str(path.relative_to(root)) not in allowed
    ]
    assert offenders == []


def test_models_and_migrations_describe_the_same_schema(owner_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.core.db import Base

    with owner_engine.connect() as conn:
        differences = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert differences == []
