"""Account and sign-in logic.

Sign-in and link-token flows run before we know who the caller is, so they use
the SYSTEM access context. This module and `deps.py` are the only request-path
code allowed to do that; a test enforces the allowlist.
"""

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit, passwords, tokens
from app.core import email as mail
from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.db import SYSTEM, access
from app.core.errors import AppError
from app.core.http import RequestMeta
from app.core.ratelimit import get_limiter, too_many
from app.models.identity import (
    AuthSession,
    EmailToken,
    EmailTokenPurpose,
    RecoveryCode,
    User,
    UserCredential,
    UserStatus,
)

from . import totp

SendEmail = Callable[[mail.Email], None]

VERIFY_TOKEN_HOURS = 24
RESET_TOKEN_HOURS = 1
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
MFA_MAX_FAILED_ATTEMPTS = 5
RECOVERY_CODE_COUNT = 10
LAST_SEEN_RESOLUTION = timedelta(seconds=60)

INVALID_CREDENTIALS = AppError(401, "invalid_credentials", "Email or password is incorrect.")
INVALID_LINK = AppError(400, "invalid_token", "This link is invalid or has expired.")
INVALID_MFA_CODE = AppError(401, "invalid_mfa_code", "That code is not valid.")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _login_failure_key(email: str) -> str:
    return "login-fail:" + hashlib.sha256(email.encode()).hexdigest()[:32]


def _totp_aad(user_id: uuid.UUID) -> str:
    return f"user:{user_id}:totp"


# --------------------------------------------------------------------------- tokens


def _issue_email_token(db: Session, user_id: uuid.UUID, purpose: EmailTokenPurpose, *, hours: int) -> str:
    """Create a single-use link token, cancelling earlier ones of the same kind."""
    now = utcnow()
    db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user_id, EmailToken.purpose == purpose.value, EmailToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = tokens.new_token()
    db.add(
        EmailToken(
            user_id=user_id,
            purpose=purpose.value,
            token_hash=tokens.hash_token(token),
            expires_at=now + timedelta(hours=hours),
        )
    )
    return token


def _take_email_token(db: Session, token: str, purpose: EmailTokenPurpose) -> EmailToken:
    """Lock and return a valid token row, or raise. The caller marks it used."""
    row = db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == tokens.hash_token(token), EmailToken.purpose == purpose.value)
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise INVALID_LINK
    return row


# --------------------------------------------------------------------------- sessions


def _revoke_sessions(
    db: Session, user_id: uuid.UUID, reason: str, *, except_session_id: uuid.UUID | None = None
) -> int:
    query = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    if except_session_id is not None:
        query = query.where(AuthSession.id != except_session_id)
    return db.execute(query).rowcount


def resolve_session(db: Session, raw_token: str) -> tuple[AuthSession, User] | None:
    """Find the live session for a cookie value, enforcing both expiry rules."""
    settings = get_settings()
    with access(db, SYSTEM):
        row = db.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == tokens.hash_token(raw_token), AuthSession.revoked_at.is_(None))
        ).first()
        if row is None:
            return None
        session, user = row
        now = utcnow()
        idle_deadline = session.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
        if session.expires_at <= now or idle_deadline <= now:
            session.revoked_at, session.revoked_reason = now, "expired"
            db.commit()
            return None
        if user.status != UserStatus.ACTIVE.value:
            return None
        if now - session.last_seen_at > LAST_SEEN_RESOLUTION:
            session.last_seen_at = now
            db.commit()
        return session, user


def revoke_session_token(db: Session, raw_token: str, reason: str) -> None:
    with access(db, SYSTEM):
        db.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == tokens.hash_token(raw_token), AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow(), revoked_reason=reason)
        )
        db.commit()


# --------------------------------------------------------------------------- registration


def register(db: Session, *, email: str, password: str, meta: RequestMeta, send: SendEmail) -> None:
    """Create an unverified account and email a confirmation link.

    The caller gets the same answer whether or not the address is already
    registered, so this endpoint cannot be used to discover who has an account.
    """
    passwords.validate_new_password(password, email=email)
    # Hash before looking the address up, so every path costs the same time.
    password_hash = passwords.hash_password(password)
    with access(db, SYSTEM):
        user = db.scalar(select(User).where(User.email == email))
        if user is not None and user.email_verified_at is not None:
            send(mail.already_registered_email(user.email))
            return
        if user is not None:
            # Unverified account: the latest registration replaces the pending
            # password and cancels earlier links.
            credential = db.get(UserCredential, user.id)
            credential.password_hash = password_hash
            credential.password_changed_at = utcnow()
        else:
            user = User(email=email)
            db.add(user)
            db.flush()
            db.add(UserCredential(user_id=user.id, password_hash=password_hash))
            audit.record(db, "auth.registered", user_id=user.id, actor_user_id=user.id, actor_role=user.role, meta=meta)
        token = _issue_email_token(db, user.id, EmailTokenPurpose.VERIFY_EMAIL, hours=VERIFY_TOKEN_HOURS)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # the same address was registered a moment ago by a parallel request
            return
        send(mail.verification_email(user.email, token))


def verify_email(db: Session, *, token: str, meta: RequestMeta) -> None:
    with access(db, SYSTEM):
        row = _take_email_token(db, token, EmailTokenPurpose.VERIFY_EMAIL)
        user = db.get(User, row.user_id)
        now = utcnow()
        row.used_at = now
        if user.email_verified_at is None:
            user.email_verified_at = now
            audit.record(
                db, "auth.email_verified", user_id=user.id, actor_user_id=user.id, actor_role=user.role, meta=meta
            )
        db.commit()


def resend_verification(db: Session, *, email: str, send: SendEmail) -> None:
    with access(db, SYSTEM):
        user = db.scalar(select(User).where(User.email == email))
        if user is None or user.email_verified_at is not None:
            return
        token = _issue_email_token(db, user.id, EmailTokenPurpose.VERIFY_EMAIL, hours=VERIFY_TOKEN_HOURS)
        db.commit()
        send(mail.verification_email(user.email, token))


# --------------------------------------------------------------------------- sign-in


@dataclass(frozen=True)
class LoginOutcome:
    token: str
    user: User
    session: AuthSession
    mfa_required: bool


def login(db: Session, *, email: str, password: str, meta: RequestMeta) -> LoginOutcome:
    limiter = get_limiter()
    failure_key = _login_failure_key(email)
    failures, retry_after = limiter.peek(failure_key)
    if failures >= LOGIN_FAILURE_LIMIT:
        # Applies to unknown addresses too, so it reveals nothing about who has an account.
        raise too_many(retry_after)

    settings = get_settings()
    with access(db, SYSTEM):
        user = db.scalar(select(User).where(User.email == email))
        credential = db.get(UserCredential, user.id) if user else None
        if user is None or credential is None:
            passwords.burn_verification_time(password)
            limiter.hit(failure_key, LOGIN_FAILURE_WINDOW_SECONDS)
            raise INVALID_CREDENTIALS
        if not passwords.verify_password(password, credential.password_hash):
            limiter.hit(failure_key, LOGIN_FAILURE_WINDOW_SECONDS)
            audit.record(db, "auth.login.failed", user_id=user.id, meta=meta, detail={"reason": "wrong_password"})
            db.commit()
            raise INVALID_CREDENTIALS

        # The password was right, so it is safe to say why sign-in is refused.
        if user.status != UserStatus.ACTIVE.value:
            audit.record(db, "auth.login.failed", user_id=user.id, meta=meta, detail={"reason": "suspended"})
            db.commit()
            raise AppError(403, "account_suspended", "This account is suspended. Contact support.")
        if user.email_verified_at is None:
            raise AppError(403, "email_not_verified", "Confirm your email address before signing in.")

        if passwords.needs_rehash(credential.password_hash):
            credential.password_hash = passwords.hash_password(password)

        now = utcnow()
        mfa_required = credential.totp_enabled_at is not None
        token = tokens.new_token()
        session = AuthSession(
            user_id=user.id,
            token_hash=tokens.hash_token(token),
            expires_at=now + timedelta(days=settings.session_absolute_days),
            last_seen_at=now,
            pending_mfa=mfa_required,
            ip=meta.ip,
            user_agent=meta.user_agent,
        )
        db.add(session)
        if not mfa_required:
            user.last_login_at = now
            audit.record(
                db, "auth.login.succeeded", user_id=user.id, actor_user_id=user.id, actor_role=user.role, meta=meta
            )
        db.commit()

    limiter.reset(failure_key)
    return LoginOutcome(token=token, user=user, session=session, mfa_required=mfa_required)


def _check_second_factor(
    db: Session, user: User, credential: UserCredential, *, code: str | None, recovery_code: str | None
) -> str | None:
    """Return "totp" or "recovery_code" when the factor is valid, else None.

    A valid factor is consumed: the TOTP step is remembered and a recovery code
    is marked used, so neither can be presented twice.
    """
    if code and credential.totp_secret_enc:
        secret = get_cipher().decrypt(credential.totp_secret_enc, _totp_aad(user.id))
        step = totp.match_step(secret, code)
        if step is not None and (credential.totp_last_step is None or step > credential.totp_last_step):
            credential.totp_last_step = step
            return "totp"
        return None
    if recovery_code:
        row = db.scalar(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_hash == tokens.hash_recovery_code(recovery_code),
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        if row is not None:
            row.used_at = utcnow()
            return "recovery_code"
    return None


def complete_mfa(
    db: Session,
    *,
    user: User,
    session: AuthSession,
    code: str | None,
    recovery_code: str | None,
    meta: RequestMeta,
    send: SendEmail,
) -> str:
    """Finish a sign-in that is waiting for the second factor. Returns the new session token."""
    if not session.pending_mfa:
        raise AppError(409, "mfa_not_pending", "This session is already fully signed in.")
    credential = db.get(UserCredential, user.id)
    method = _check_second_factor(db, user, credential, code=code, recovery_code=recovery_code)
    now = utcnow()
    if method is None:
        session.mfa_failed_attempts += 1
        exhausted = session.mfa_failed_attempts >= MFA_MAX_FAILED_ATTEMPTS
        if exhausted:
            session.revoked_at, session.revoked_reason = now, "mfa_attempts_exceeded"
        audit.record(
            db,
            "auth.mfa.challenge_failed",
            user_id=user.id,
            actor_user_id=user.id,
            actor_role=user.role,
            meta=meta,
            detail={"session_ended": exhausted},
        )
        db.commit()
        if exhausted:
            raise AppError(401, "mfa_attempts_exceeded", "Too many wrong codes. Sign in again.")
        raise INVALID_MFA_CODE

    # Rotate the token now that the session gains full access, so a token seen
    # before the second factor is useless afterwards.
    token = tokens.new_token()
    session.token_hash = tokens.hash_token(token)
    session.pending_mfa = False
    session.mfa_at = now
    user.last_login_at = now
    audit.record(
        db,
        "auth.login.succeeded",
        user_id=user.id,
        actor_user_id=user.id,
        actor_role=user.role,
        meta=meta,
        detail={"second_factor": method},
    )
    db.commit()
    if method == "recovery_code":
        send(mail.security_notice_email(user.email, "A recovery code was used to sign in to your account."))
    return token


def logout(db: Session, *, user: User, session: AuthSession, meta: RequestMeta) -> None:
    session.revoked_at, session.revoked_reason = utcnow(), "logout"
    audit.record(db, "auth.logout", user_id=user.id, actor_user_id=user.id, actor_role=user.role, meta=meta)
    db.commit()


# --------------------------------------------------------------------------- re-authentication


def _confirm_password(db: Session, user: User, password: str) -> UserCredential:
    """Sensitive changes ask for the password again, even inside a session."""
    limiter = get_limiter()
    key = f"reauth-fail:{user.id}"
    failures, retry_after = limiter.peek(key)
    if failures >= LOGIN_FAILURE_LIMIT:
        raise too_many(retry_after)
    credential = db.get(UserCredential, user.id)
    if not passwords.verify_password(password, credential.password_hash):
        limiter.hit(key, LOGIN_FAILURE_WINDOW_SECONDS)
        raise AppError(403, "wrong_password", "Current password is incorrect.")
    return credential


# --------------------------------------------------------------------------- two-factor management


def begin_mfa_setup(db: Session, *, user: User, password: str) -> tuple[str, str]:
    """Generate a pending authenticator secret. Returns (secret, otpauth URI)."""
    credential = _confirm_password(db, user, password)
    if credential.totp_enabled_at is not None:
        raise AppError(409, "mfa_already_enabled", "Two-factor authentication is already on.")
    secret = totp.new_secret()
    credential.totp_secret_enc = get_cipher().encrypt(secret, _totp_aad(user.id))
    credential.totp_last_step = None
    db.commit()
    return secret, totp.provisioning_uri(secret, user.email)


def _replace_recovery_codes(db: Session, user_id: uuid.UUID) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    codes = [tokens.new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    db.add_all(RecoveryCode(user_id=user_id, code_hash=tokens.hash_recovery_code(code)) for code in codes)
    return codes


def enable_mfa(
    db: Session, *, user: User, session: AuthSession, code: str, meta: RequestMeta, send: SendEmail
) -> list[str]:
    """Confirm the authenticator works and turn two-factor on. Returns recovery codes (shown once)."""
    credential = db.get(UserCredential, user.id)
    if credential.totp_enabled_at is not None:
        raise AppError(409, "mfa_already_enabled", "Two-factor authentication is already on.")
    if not credential.totp_secret_enc:
        raise AppError(409, "mfa_setup_not_started", "Start the setup again to get a new QR code.")
    secret = get_cipher().decrypt(credential.totp_secret_enc, _totp_aad(user.id))
    step = totp.match_step(secret, code)
    if step is None:
        raise AppError(400, "invalid_mfa_code", "That code is not valid. Check the time on your phone and try again.")
    now = utcnow()
    credential.totp_enabled_at = now
    credential.totp_last_step = step
    user.mfa_enabled = True
    session.mfa_at = now
    codes = _replace_recovery_codes(db, user.id)
    # Other browsers signed in with the password alone; make them sign in again.
    _revoke_sessions(db, user.id, "mfa_enabled", except_session_id=session.id)
    audit.record(db, "auth.mfa.enabled", user_id=user.id, actor_user_id=user.id, actor_role=user.role, meta=meta)
    db.commit()
    send(mail.security_notice_email(user.email, "Two-factor authentication was turned on for your account."))
    return codes


def disable_mfa(
    db: Session,
    *,
    user: User,
    session: AuthSession,
    password: str,
    code: str | None,
    recovery_code: str | None,
    meta: RequestMeta,
    send: SendEmail,
) -> None:
    credential = _confirm_password(db, user, password)
    if credential.totp_enabled_at is None:
        raise AppError(409, "mfa_not_enabled", "Two-factor authentication is not on.")
    if _check_second_factor(db, user, credential, code=code, recovery_code=recovery_code) is None:
        db.rollback()
        raise AppError(403, "invalid_mfa_code", "That code is not valid.")
    credential.totp_secret_enc = None
    credential.totp_enabled_at = None
    credential.totp_last_step = None
    user.mfa_enabled = False
    session.mfa_at = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    audit.record(db, "auth.mfa.disabled", user_id=user.id, actor_user_id=user.id, actor_role=user.role, meta=meta)
    db.commit()
    send(mail.security_notice_email(user.email, "Two-factor authentication was turned off for your account."))


def regenerate_recovery_codes(db: Session, *, user: User, password: str, code: str, meta: RequestMeta) -> list[str]:
    credential = _confirm_password(db, user, password)
    if credential.totp_enabled_at is None:
        raise AppError(409, "mfa_not_enabled", "Two-factor authentication is not on.")
    if _check_second_factor(db, user, credential, code=code, recovery_code=None) is None:
        db.rollback()
        raise AppError(403, "invalid_mfa_code", "That code is not valid.")
    codes = _replace_recovery_codes(db, user.id)
    audit.record(
        db, "auth.mfa.recovery_regenerated", user_id=user.id, actor_user_id=user.id, actor_role=user.role, meta=meta
    )
    db.commit()
    return codes


def recovery_codes_remaining(db: Session, user_id: uuid.UUID) -> int:
    return len(
        db.scalars(select(RecoveryCode.id).where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))).all()
    )


# --------------------------------------------------------------------------- passwords


def change_password(
    db: Session,
    *,
    user: User,
    session: AuthSession,
    current_password: str,
    new_password: str,
    meta: RequestMeta,
    send: SendEmail,
) -> int:
    """Change the password and sign out every other browser. Returns how many were signed out."""
    credential = _confirm_password(db, user, current_password)
    if passwords.verify_password(new_password, credential.password_hash):
        raise AppError(422, "weak_password", "The new password must be different from the current one.")
    passwords.validate_new_password(new_password, email=user.email)
    now = utcnow()
    credential.password_hash = passwords.hash_password(new_password)
    credential.password_changed_at = now
    revoked = _revoke_sessions(db, user.id, "password_changed", except_session_id=session.id)
    db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == EmailTokenPurpose.RESET_PASSWORD.value,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    audit.record(
        db,
        "auth.password.changed",
        user_id=user.id,
        actor_user_id=user.id,
        actor_role=user.role,
        meta=meta,
        detail={"other_sessions_ended": revoked},
    )
    db.commit()
    send(mail.security_notice_email(user.email, "The password for your account was changed."))
    return revoked


def request_password_reset(db: Session, *, email: str, meta: RequestMeta, send: SendEmail) -> None:
    with access(db, SYSTEM):
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            return
        token = _issue_email_token(db, user.id, EmailTokenPurpose.RESET_PASSWORD, hours=RESET_TOKEN_HOURS)
        audit.record(db, "auth.password.reset_requested", user_id=user.id, meta=meta)
        db.commit()
        send(mail.password_reset_email(user.email, token))


def reset_password(db: Session, *, token: str, new_password: str, meta: RequestMeta, send: SendEmail) -> None:
    """Set a new password from an emailed link and sign out every browser.

    Two-factor authentication is left untouched: the next sign-in still asks for it.
    """
    with access(db, SYSTEM):
        row = _take_email_token(db, token, EmailTokenPurpose.RESET_PASSWORD)
        user = db.get(User, row.user_id)
        credential = db.get(UserCredential, user.id)
        passwords.validate_new_password(new_password, email=user.email)
        now = utcnow()
        credential.password_hash = passwords.hash_password(new_password)
        credential.password_changed_at = now
        row.used_at = now
        if user.email_verified_at is None:
            user.email_verified_at = now  # receiving the link proves the address
        revoked = _revoke_sessions(db, user.id, "password_reset")
        audit.record(
            db,
            "auth.password.reset_completed",
            user_id=user.id,
            actor_user_id=user.id,
            actor_role=user.role,
            meta=meta,
            detail={"sessions_ended": revoked},
        )
        db.commit()
        get_limiter().reset(_login_failure_key(user.email))
        send(mail.security_notice_email(user.email, "The password for your account was reset using an emailed link."))


# --------------------------------------------------------------------------- session management


def list_sessions(db: Session, user_id: uuid.UUID) -> list[AuthSession]:
    now = utcnow()
    idle_cutoff = now - timedelta(minutes=get_settings().session_idle_minutes)
    return list(
        db.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.pending_mfa.is_(False),
                AuthSession.expires_at > now,
                AuthSession.last_seen_at > idle_cutoff,
            )
            .order_by(AuthSession.last_seen_at.desc())
        ).all()
    )


def revoke_session(db: Session, *, user: User, session_id: uuid.UUID, meta: RequestMeta) -> None:
    """End one of the caller's own sessions. Another user's session id is simply not found."""
    target = db.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id, AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
        )
    )
    if target is None:
        raise AppError(404, "not_found", "Session not found.")
    target.revoked_at, target.revoked_reason = utcnow(), "revoked_by_user"
    audit.record(
        db,
        "auth.session.revoked",
        user_id=user.id,
        actor_user_id=user.id,
        actor_role=user.role,
        meta=meta,
        target_type="session",
        target_id=str(session_id),
    )
    db.commit()


def revoke_other_sessions(db: Session, *, user: User, session: AuthSession, meta: RequestMeta) -> int:
    revoked = _revoke_sessions(db, user.id, "revoked_by_user", except_session_id=session.id)
    audit.record(
        db,
        "auth.sessions.revoked_others",
        user_id=user.id,
        actor_user_id=user.id,
        actor_role=user.role,
        meta=meta,
        detail={"sessions_ended": revoked},
    )
    db.commit()
    return revoked
