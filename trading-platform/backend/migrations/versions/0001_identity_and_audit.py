"""Identity, sessions, single-use tokens and the audit trail, with tenant isolation.

Revision ID: 0001
Revises:

Besides the tables, this migration installs the database half of tenant
isolation (see docs/security.md):

* helper functions that read the per-transaction access context,
* row-level security policies on every table,
* column-level privileges for the application role (deny by default),
* guard triggers for the most sensitive changes (roles, session revocation,
  the append-only audit log).
"""

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _app_role() -> str:
    """The runtime database role that receives (limited) privileges."""
    role = os.environ.get("DB_APP_ROLE", "tp_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise RuntimeError("DB_APP_ROLE must be a plain lower-case PostgreSQL role name")
    return role


def _uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamp(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default_now else None,
    )


def _user_fk(table: str, column: str = "user_id", *, cascade: bool = True, nullable: bool = False) -> sa.Column:
    return sa.Column(
        column,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(
            "users.id",
            name=op.f(f"fk_{table}_{column}_users"),
            ondelete="CASCADE" if cascade else None,
        ),
        nullable=nullable,
    )


# Conditions shared by the policies.
OWN_ROW = "(app_access_mode() = 'tenant' AND user_id = app_user_id())"
OWN_USER = "(app_access_mode() = 'tenant' AND id = app_user_id())"
SYSTEM = "app_access_mode() = 'system'"
STAFF_OR_SYSTEM = "app_access_mode() IN ('staff', 'system')"


def upgrade() -> None:
    app_role = _app_role()
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ------------------------------------------------------------------ tables
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.String(100), server_default="", nullable=False),
        sa.Column("timezone", sa.String(64), server_default="UTC", nullable=False),
        sa.Column("role", sa.String(20), server_default="user", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("suspended_reason", sa.Text()),
        _timestamp("email_verified_at"),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _timestamp("last_login_at"),
        _timestamp("created_at", nullable=False, default_now=True),
        _timestamp("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.CheckConstraint("role IN ('user', 'support_admin', 'super_admin')", name=op.f("ck_users_role_valid")),
        sa.CheckConstraint("status IN ('active', 'suspended')", name=op.f("ck_users_status_valid")),
    )

    op.create_table(
        "user_credentials",
        _user_fk("user_credentials"),
        sa.Column("password_hash", sa.Text(), nullable=False),
        _timestamp("password_changed_at", nullable=False, default_now=True),
        sa.Column("totp_secret_enc", sa.Text()),
        _timestamp("totp_enabled_at"),
        sa.Column("totp_last_step", sa.BigInteger()),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_credentials")),
    )

    op.create_table(
        "sessions",
        _uuid_pk(),
        _user_fk("sessions"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        _timestamp("created_at", nullable=False, default_now=True),
        _timestamp("last_seen_at", nullable=False, default_now=True),
        _timestamp("expires_at", nullable=False),
        _timestamp("revoked_at"),
        sa.Column("revoked_reason", sa.String(40)),
        sa.Column("pending_mfa", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _timestamp("mfa_at"),
        sa.Column("mfa_failed_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("ip", sa.String(45)),
        sa.Column("user_agent", sa.String(300)),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "email_tokens",
        _uuid_pk(),
        _user_fk("email_tokens"),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        _timestamp("created_at", nullable=False, default_now=True),
        _timestamp("expires_at", nullable=False),
        _timestamp("used_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_email_tokens_token_hash")),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')", name=op.f("ck_email_tokens_purpose_valid")),
    )
    op.create_index("ix_email_tokens_user_id", "email_tokens", ["user_id"])

    op.create_table(
        "recovery_codes",
        _uuid_pk(),
        _user_fk("recovery_codes"),
        sa.Column("code_hash", sa.String(64), nullable=False),
        _timestamp("created_at", nullable=False, default_now=True),
        _timestamp("used_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_codes")),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_recovery_codes_user_id_code_hash"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _timestamp("created_at", nullable=False, default_now=True),
        _user_fk("audit_logs", cascade=False, nullable=True),
        _user_fk("audit_logs", "actor_user_id", cascade=False, nullable=True),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(40)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("ip", sa.String(45)),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("request_id", sa.String(32)),
        sa.Column("detail", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_logs_user_id_created_at", "audit_logs", ["user_id", "created_at"])
    op.create_index("ix_audit_logs_action_created_at", "audit_logs", ["action", "created_at"])

    # ------------------------------------------------------- access context
    # The application sets these two values at the start of every transaction.
    # Unset means "none", which matches no policy: fail closed.
    op.execute(
        """
        CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_access_mode() RETURNS text LANGUAGE sql STABLE AS
        $$ SELECT COALESCE(NULLIF(current_setting('app.access_mode', true), ''), 'none') $$
        """
    )

    # ------------------------------------------------------ row-level security
    for table in ("users", "user_credentials", "sessions", "email_tokens", "recovery_codes", "audit_logs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE applies the policies to the table owner too.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # users: a user sees and edits only their own row; staff and system see all.
    # New accounts are created only by trusted sign-up code.
    op.execute(f"CREATE POLICY users_select ON users FOR SELECT USING ({OWN_USER} OR {STAFF_OR_SYSTEM})")
    op.execute(f"CREATE POLICY users_insert ON users FOR INSERT WITH CHECK ({SYSTEM})")
    op.execute(
        f"CREATE POLICY users_update ON users FOR UPDATE "
        f"USING ({OWN_USER} OR {STAFF_OR_SYSTEM}) WITH CHECK ({OWN_USER} OR {STAFF_OR_SYSTEM})"
    )

    # user_credentials: never visible in the staff context.
    op.execute(f"CREATE POLICY user_credentials_select ON user_credentials FOR SELECT USING ({OWN_ROW} OR {SYSTEM})")
    op.execute(f"CREATE POLICY user_credentials_insert ON user_credentials FOR INSERT WITH CHECK ({SYSTEM})")
    op.execute(
        f"CREATE POLICY user_credentials_update ON user_credentials FOR UPDATE "
        f"USING ({OWN_ROW} OR {SYSTEM}) WITH CHECK ({OWN_ROW} OR {SYSTEM})"
    )

    # sessions: staff may look at and revoke them, but only sign-in code creates them.
    op.execute(f"CREATE POLICY sessions_select ON sessions FOR SELECT USING ({OWN_ROW} OR {STAFF_OR_SYSTEM})")
    op.execute(f"CREATE POLICY sessions_insert ON sessions FOR INSERT WITH CHECK ({SYSTEM})")
    op.execute(
        f"CREATE POLICY sessions_update ON sessions FOR UPDATE "
        f"USING ({OWN_ROW} OR {STAFF_OR_SYSTEM}) WITH CHECK ({OWN_ROW} OR {STAFF_OR_SYSTEM})"
    )

    # email_tokens: link tokens are issued by trusted code; never visible to staff.
    op.execute(f"CREATE POLICY email_tokens_select ON email_tokens FOR SELECT USING ({OWN_ROW} OR {SYSTEM})")
    op.execute(f"CREATE POLICY email_tokens_insert ON email_tokens FOR INSERT WITH CHECK ({SYSTEM})")
    op.execute(
        f"CREATE POLICY email_tokens_update ON email_tokens FOR UPDATE "
        f"USING ({OWN_ROW} OR {SYSTEM}) WITH CHECK ({OWN_ROW} OR {SYSTEM})"
    )

    # recovery_codes: owned and managed by the user; never visible to staff.
    op.execute(
        f"CREATE POLICY recovery_codes_all ON recovery_codes FOR ALL "
        f"USING ({OWN_ROW} OR {SYSTEM}) WITH CHECK ({OWN_ROW} OR {SYSTEM})"
    )

    # audit_logs: readable by the account concerned and by staff. Writers must
    # name themselves as the actor. There is no UPDATE or DELETE policy at all.
    op.execute(f"CREATE POLICY audit_logs_select ON audit_logs FOR SELECT USING ({OWN_ROW} OR {STAFF_OR_SYSTEM})")
    op.execute(
        f"""
        CREATE POLICY audit_logs_insert ON audit_logs FOR INSERT WITH CHECK (
            ({OWN_ROW} AND actor_user_id = app_user_id())
            OR (app_access_mode() = 'staff' AND actor_user_id = app_user_id())
            OR {SYSTEM}
        )
        """
    )

    # ------------------------------------------------------------ guard triggers
    # Roles and account status are the escalation path, so the rules are
    # enforced here as well as in the application.
    op.execute(
        """
        CREATE FUNCTION users_guard_privileged_columns() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            actor_role text;
        BEGIN
            IF app_access_mode() = 'system' THEN
                RETURN NEW;
            END IF;
            IF NEW.role IS DISTINCT FROM OLD.role
               OR NEW.status IS DISTINCT FROM OLD.status
               OR NEW.suspended_reason IS DISTINCT FROM OLD.suspended_reason THEN
                IF app_access_mode() <> 'staff' THEN
                    RAISE EXCEPTION 'role and status can only be changed by staff' USING ERRCODE = '42501';
                END IF;
                IF NEW.id = app_user_id() THEN
                    RAISE EXCEPTION 'staff cannot change their own role or status' USING ERRCODE = '42501';
                END IF;
                SELECT role INTO actor_role FROM users WHERE id = app_user_id();
                IF actor_role IS NULL OR actor_role NOT IN ('support_admin', 'super_admin') THEN
                    RAISE EXCEPTION 'only staff accounts can change role or status' USING ERRCODE = '42501';
                END IF;
                IF NEW.role IS DISTINCT FROM OLD.role AND actor_role <> 'super_admin' THEN
                    RAISE EXCEPTION 'only a super admin can change roles' USING ERRCODE = '42501';
                END IF;
                IF actor_role = 'support_admin' AND OLD.role <> 'user' THEN
                    RAISE EXCEPTION 'support admins can only manage regular users' USING ERRCODE = '42501';
                END IF;
            END IF;
            IF NEW.email_verified_at IS DISTINCT FROM OLD.email_verified_at THEN
                RAISE EXCEPTION 'email verification is recorded by sign-up code only' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER users_guard_privileged_columns BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION users_guard_privileged_columns()"
    )

    # A revoked session stays revoked, and staff can do nothing to a session except revoke it.
    op.execute(
        """
        CREATE FUNCTION sessions_guard_updates() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
                RAISE EXCEPTION 'a revoked session cannot be changed' USING ERRCODE = '42501';
            END IF;
            IF app_access_mode() = 'staff' AND (
                   NEW.token_hash IS DISTINCT FROM OLD.token_hash
                OR NEW.pending_mfa IS DISTINCT FROM OLD.pending_mfa
                OR NEW.mfa_at IS DISTINCT FROM OLD.mfa_at
                OR NEW.mfa_failed_attempts IS DISTINCT FROM OLD.mfa_failed_attempts
                OR NEW.last_seen_at IS DISTINCT FROM OLD.last_seen_at
            ) THEN
                RAISE EXCEPTION 'staff can only revoke sessions' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER sessions_guard_updates BEFORE UPDATE ON sessions "
        "FOR EACH ROW EXECUTE FUNCTION sessions_guard_updates()"
    )

    # The audit trail is append-only for every role, including the table owner.
    op.execute(
        """
        CREATE FUNCTION audit_logs_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only' USING ERRCODE = '42501';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_change BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_append_only()"
    )

    # ------------------------------------------------------------- privileges
    # The application role gets exactly what the code needs and nothing else.
    # Columns that must never change after insert (ids, owners, timestamps of
    # creation, email) are simply not updatable.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {app_role}")
    op.execute(f"GRANT SELECT, INSERT ON users TO {app_role}")
    op.execute(
        "GRANT UPDATE (display_name, timezone, role, status, suspended_reason, email_verified_at, "
        f"mfa_enabled, last_login_at, updated_at) ON users TO {app_role}"
    )
    op.execute(f"GRANT SELECT, INSERT ON user_credentials TO {app_role}")
    op.execute(
        "GRANT UPDATE (password_hash, password_changed_at, totp_secret_enc, totp_enabled_at, totp_last_step) "
        f"ON user_credentials TO {app_role}"
    )
    op.execute(f"GRANT SELECT, INSERT ON sessions TO {app_role}")
    op.execute(
        "GRANT UPDATE (token_hash, last_seen_at, revoked_at, revoked_reason, pending_mfa, mfa_at, "
        f"mfa_failed_attempts) ON sessions TO {app_role}"
    )
    op.execute(f"GRANT SELECT, INSERT ON email_tokens TO {app_role}")
    op.execute(f"GRANT UPDATE (used_at) ON email_tokens TO {app_role}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON recovery_codes TO {app_role}")
    op.execute(f"GRANT UPDATE (used_at) ON recovery_codes TO {app_role}")
    op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {app_role}")


def downgrade() -> None:
    for table in ("audit_logs", "recovery_codes", "email_tokens", "sessions", "user_credentials", "users"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for function in (
        "audit_logs_append_only()",
        "sessions_guard_updates()",
        "users_guard_privileged_columns()",
        "app_access_mode()",
        "app_user_id()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
