"""Helpers shared by the trading API modules."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import AppError
from app.core.http import RequestMeta
from app.models.identity import User


def owned(db: Session, model, object_id: uuid.UUID, user: User, what: str):
    """The row, if it exists and belongs to the user; otherwise 404 (never 403, which would reveal it exists)."""
    row = db.get(model, object_id)
    if row is None or row.user_id != user.id:
        raise AppError(404, "not_found", f"{what} not found.")
    return row


def record(db: Session, user: User, meta: RequestMeta, action: str, target_type: str, target_id, **detail) -> None:
    audit.record(
        db,
        action,
        user_id=user.id,
        actor_user_id=user.id,
        actor_role=user.role,
        meta=meta,
        target_type=target_type,
        target_id=str(target_id),
        detail=detail,
    )


def plain(value: Any) -> Any:
    """JSON-safe copy for snapshots and audit detail."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, uuid.UUID)):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value
