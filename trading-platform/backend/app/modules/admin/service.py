"""Staff actions on user accounts. Every change is audited with the acting staff member.

These functions run in the staff access context, which can read profiles,
session metadata and audit logs but never credential tables. The permission
rules here are enforced a second time by database triggers.
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import audit
from app.core import email as mail
from app.core.errors import AppError
from app.core.http import RequestMeta
from app.models.audit import AuditLog
from app.models.identity import AuthSession, User, UserRole, UserStatus
from app.modules.auth.service import SendEmail, utcnow

NOT_FOUND = AppError(404, "not_found", "User not found.")


def _target(db: Session, actor: User, user_id: uuid.UUID, *, changing: bool) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise NOT_FOUND
    if changing:
        if target.id == actor.id:
            raise AppError(403, "cannot_modify_self", "You cannot change your own role or status.")
        if actor.role == UserRole.SUPPORT_ADMIN.value and target.role != UserRole.USER.value:
            raise AppError(403, "insufficient_role", "Support admins can only manage regular users.")
    return target


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_users(
    db: Session, *, search: str | None, role: str | None, status: str | None, limit: int, offset: int
) -> tuple[list[User], int]:
    query = select(User)
    if search:
        query = query.where(User.email.ilike(f"%{_escape_like(search.strip())}%", escape="\\"))
    if role:
        query = query.where(User.role == role)
    if status:
        query = query.where(User.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(User.created_at.desc()).limit(limit).offset(offset)).all()
    return list(rows), int(total or 0)


def view_user(db: Session, *, actor: User, user_id: uuid.UUID, meta: RequestMeta) -> tuple[User, list[AuthSession]]:
    target = _target(db, actor, user_id, changing=False)
    sessions = db.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == target.id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())
        .order_by(AuthSession.last_seen_at.desc())
    ).all()
    # Looking at a customer's account is itself recorded.
    audit.record(
        db,
        "admin.user.viewed",
        user_id=target.id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        meta=meta,
        target_type="user",
        target_id=str(target.id),
    )
    db.commit()
    return target, list(sessions)


def _end_sessions(db: Session, user_id: uuid.UUID, reason: str) -> int:
    return db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    ).rowcount


def suspend_user(
    db: Session, *, actor: User, user_id: uuid.UUID, reason: str, meta: RequestMeta, send: SendEmail
) -> User:
    target = _target(db, actor, user_id, changing=True)
    if target.status == UserStatus.SUSPENDED.value:
        raise AppError(409, "already_suspended", "This account is already suspended.")
    target.status = UserStatus.SUSPENDED.value
    target.suspended_reason = reason
    ended = _end_sessions(db, target.id, "suspended")
    audit.record(
        db,
        "admin.user.suspended",
        user_id=target.id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        meta=meta,
        target_type="user",
        target_id=str(target.id),
        detail={"reason": reason, "sessions_ended": ended},
    )
    db.commit()
    send(mail.security_notice_email(target.email, "Your account has been suspended. Contact support for details."))
    return target


def unsuspend_user(db: Session, *, actor: User, user_id: uuid.UUID, meta: RequestMeta, send: SendEmail) -> User:
    target = _target(db, actor, user_id, changing=True)
    if target.status != UserStatus.SUSPENDED.value:
        raise AppError(409, "not_suspended", "This account is not suspended.")
    target.status = UserStatus.ACTIVE.value
    target.suspended_reason = None
    audit.record(
        db,
        "admin.user.unsuspended",
        user_id=target.id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        meta=meta,
        target_type="user",
        target_id=str(target.id),
    )
    db.commit()
    send(mail.security_notice_email(target.email, "Your account has been reinstated. You can sign in again."))
    return target


def set_role(db: Session, *, actor: User, user_id: uuid.UUID, role: UserRole, meta: RequestMeta) -> User:
    """Super admins only (enforced by the route and by a database trigger)."""
    target = _target(db, actor, user_id, changing=True)
    if target.role == role.value:
        return target
    previous = target.role
    target.role = role.value
    # A change of privilege ends existing sessions so the new role starts from a fresh sign-in.
    ended = _end_sessions(db, target.id, "role_changed")
    audit.record(
        db,
        "admin.user.role_changed",
        user_id=target.id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        meta=meta,
        target_type="user",
        target_id=str(target.id),
        detail={"from": previous, "to": role.value, "sessions_ended": ended},
    )
    db.commit()
    return target


def revoke_user_sessions(db: Session, *, actor: User, user_id: uuid.UUID, meta: RequestMeta) -> int:
    target = _target(db, actor, user_id, changing=True)
    ended = _end_sessions(db, target.id, "revoked_by_staff")
    audit.record(
        db,
        "admin.user.sessions_revoked",
        user_id=target.id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        meta=meta,
        target_type="user",
        target_id=str(target.id),
        detail={"sessions_ended": ended},
    )
    db.commit()
    return ended


def list_audit(
    db: Session, *, user_id: uuid.UUID | None, action_prefix: str | None, limit: int, before_id: int | None
) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)
    if action_prefix:
        query = query.where(AuditLog.action.like(f"{_escape_like(action_prefix)}%", escape="\\"))
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    return list(db.scalars(query).all())
