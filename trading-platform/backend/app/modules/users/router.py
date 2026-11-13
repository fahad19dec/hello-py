"""The signed-in user's own profile and security activity."""
from datetime import datetime
from typing import Annotated
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.http import request_meta
from app.models.audit import AuditLog
from app.modules.auth.deps import AuthContext, current_user
from app.modules.auth.schemas import UserOut

router = APIRouter(prefix="/users", tags=["users"])


class ProfileIn(BaseModel):
    display_name: Annotated[str, Field(max_length=100)] | None = None
    timezone: Annotated[str, Field(max_length=64)] | None = None

    @field_validator("display_name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is not None and value not in available_timezones():
            raise ValueError("unknown time zone")
        return value


class ActivityOut(BaseModel):
    id: int
    created_at: datetime
    action: str
    actor: str  # "you", "staff" or "system"; staff identities are not shown to users
    ip: str | None
    user_agent: str | None
    detail: dict


@router.get("/me", response_model=UserOut)
def get_profile(ctx: AuthContext = Depends(current_user)):
    return UserOut.model_validate(ctx.user)


@router.patch("/me", response_model=UserOut)
def update_profile(
    body: ProfileIn, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    changed = []
    for field in ("display_name", "timezone"):
        value = getattr(body, field)
        if value is not None and value != getattr(ctx.user, field):
            setattr(ctx.user, field, value)
            changed.append(field)
    if changed:
        audit.record(
            db, "user.profile.updated", user_id=ctx.user.id, actor_user_id=ctx.user.id, actor_role=ctx.user.role,
            meta=request_meta(request), detail={"fields": changed},
        )
        db.commit()
    return UserOut.model_validate(ctx.user)


@router.get("/me/activity", response_model=list[ActivityOut])
def my_activity(
    limit: int = Query(25, ge=1, le=100),
    before_id: int | None = Query(None, ge=1),
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Security events on the caller's own account, newest first."""
    query = select(AuditLog).where(AuditLog.user_id == ctx.user.id).order_by(AuditLog.id.desc()).limit(limit)
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    rows = db.scalars(query).all()
    return [
        ActivityOut(
            id=row.id,
            created_at=row.created_at,
            action=row.action,
            actor="you" if row.actor_user_id == ctx.user.id else ("staff" if row.actor_user_id else "system"),
            ip=row.ip if row.actor_user_id in (None, ctx.user.id) else None,
            user_agent=row.user_agent if row.actor_user_id in (None, ctx.user.id) else None,
            detail=row.detail,
        )
        for row in rows
    ]
