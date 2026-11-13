"""Admin API: user management and the audit trail. Staff only, second factor required."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.email import get_email_sender
from app.core.http import request_meta
from app.models.identity import User, UserRole
from app.modules.auth.deps import AuthContext, current_staff, current_super_admin

from . import service

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    status: str
    suspended_reason: str | None
    mfa_enabled: bool
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def of(cls, user: User) -> "AdminUserOut":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            status=user.status,
            suspended_reason=user.suspended_reason,
            mfa_enabled=user.mfa_enabled,
            email_verified=user.email_verified_at is not None,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class AdminSessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None


class AdminUserDetailOut(BaseModel):
    user: AdminUserOut
    sessions: list[AdminSessionOut]


class UserPageOut(BaseModel):
    items: list[AdminUserOut]
    total: int


class SuspendIn(BaseModel):
    reason: Annotated[str, Field(min_length=3, max_length=500)]


class RoleIn(BaseModel):
    role: UserRole


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    user_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_role: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    detail: dict


class CountOut(BaseModel):
    count: int


def _sender(background: BackgroundTasks) -> service.SendEmail:
    sender = get_email_sender()
    return lambda email: background.add_task(sender.send, email)


@router.get("/users", response_model=UserPageOut)
def list_users(
    search: str | None = Query(None, max_length=200),
    role: UserRole | None = None,
    status: Literal["active", "suspended"] | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(current_staff),
    db: Session = Depends(get_db),
):
    rows, total = service.list_users(
        db, search=search, role=role.value if role else None, status=status, limit=limit, offset=offset
    )
    return {"items": [AdminUserOut.of(u) for u in rows], "total": total}


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
def view_user(
    user_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_staff), db: Session = Depends(get_db)
):
    user, sessions = service.view_user(db, actor=ctx.user, user_id=user_id, meta=request_meta(request))
    return {
        "user": AdminUserOut.of(user),
        "sessions": [
            AdminSessionOut(
                id=s.id, created_at=s.created_at, last_seen_at=s.last_seen_at, ip=s.ip, user_agent=s.user_agent
            )
            for s in sessions
        ],
    }


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
def suspend_user(
    user_id: uuid.UUID,
    body: SuspendIn,
    request: Request,
    background: BackgroundTasks,
    ctx: AuthContext = Depends(current_staff),
    db: Session = Depends(get_db),
):
    user = service.suspend_user(
        db, actor=ctx.user, user_id=user_id, reason=body.reason, meta=request_meta(request), send=_sender(background)
    )
    return AdminUserOut.of(user)


@router.post("/users/{user_id}/unsuspend", response_model=AdminUserOut)
def unsuspend_user(
    user_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    ctx: AuthContext = Depends(current_staff),
    db: Session = Depends(get_db),
):
    user = service.unsuspend_user(
        db, actor=ctx.user, user_id=user_id, meta=request_meta(request), send=_sender(background)
    )
    return AdminUserOut.of(user)


@router.post("/users/{user_id}/revoke-sessions", response_model=CountOut)
def revoke_sessions(
    user_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_staff), db: Session = Depends(get_db)
):
    return {"count": service.revoke_user_sessions(db, actor=ctx.user, user_id=user_id, meta=request_meta(request))}


@router.put("/users/{user_id}/role", response_model=AdminUserOut)
def set_role(
    user_id: uuid.UUID,
    body: RoleIn,
    request: Request,
    ctx: AuthContext = Depends(current_super_admin),
    db: Session = Depends(get_db),
):
    user = service.set_role(db, actor=ctx.user, user_id=user_id, role=body.role, meta=request_meta(request))
    return AdminUserOut.of(user)


@router.get("/audit", response_model=list[AuditOut])
def audit_log(
    user_id: uuid.UUID | None = None,
    action: str | None = Query(None, max_length=80, description="Action name or prefix, e.g. 'admin.'"),
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1),
    ctx: AuthContext = Depends(current_staff),
    db: Session = Depends(get_db),
):
    rows = service.list_audit(db, user_id=user_id, action_prefix=action, limit=limit, before_id=before_id)
    return [
        AuditOut(
            id=r.id,
            created_at=r.created_at,
            user_id=r.user_id,
            actor_user_id=r.actor_user_id,
            actor_role=r.actor_role,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            ip=r.ip,
            detail=r.detail,
        )
        for r in rows
    ]
