"""Writing audit events.

Events are added to the caller's transaction, so an action and its audit row
commit together or not at all. Detail values must never contain secrets:
record what happened and to what, not the credential involved.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog

from .http import RequestMeta

_FORBIDDEN_DETAIL_KEYS = ("password", "token", "secret", "code", "otp", "hash", "cookie")


def record(
    db: Session,
    action: str,
    *,
    user_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    meta: RequestMeta | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    detail = detail or {}
    for key in detail:
        if any(word in key.lower() for word in _FORBIDDEN_DETAIL_KEYS):
            raise ValueError(f"audit detail must not carry secrets (key: {key})")
    entry = AuditLog(
        action=action,
        user_id=user_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        target_type=target_type,
        target_id=target_id,
        ip=meta.ip if meta else None,
        user_agent=meta.user_agent if meta else None,
        request_id=meta.request_id if meta else None,
        detail=detail,
    )
    db.add(entry)
    return entry
