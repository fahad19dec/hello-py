"""Risk limits at each scope, and the user's own kill switch."""

import re
import uuid
from datetime import time
from decimal import Decimal
from typing import Annotated
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.http import request_meta
from app.models.trading import BrokerAccount, PlatformControl, RiskLimits, Strategy, TradingControl
from app.modules.auth.deps import AuthContext, current_user
from app.trading.ledger import utcnow
from app.trading.signal_format import normalise_symbol

from .common import plain, record

router = APIRouter(prefix="/risk", tags=["risk"])

Positive = Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=8)]
LIMIT_FIELDS = (
    "max_order_quantity",
    "max_order_lots",
    "max_open_positions",
    "max_daily_loss",
    "max_daily_trades",
    "max_exposure",
    "max_slippage_pct",
    "max_signal_age_seconds",
    "max_consecutive_failures",
    "min_available_margin",
    "allowed_symbols",
    "trading_sessions",
)


class Session_(BaseModel):
    days: list[int] = Field(min_length=1, max_length=7)
    start: str
    end: str
    timezone: str = "Asia/Kolkata"

    @field_validator("days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in value):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(value))

    @field_validator("start", "end")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("use HH:MM")
        time.fromisoformat(value)
        return value

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        if value not in available_timezones():
            raise ValueError("unknown time zone")
        return value


class LimitsIn(BaseModel):
    max_order_quantity: Positive | None = None
    max_order_lots: Positive | None = None
    max_open_positions: Annotated[int, Field(ge=0, le=10000)] | None = None
    max_daily_loss: Positive | None = None
    max_daily_trades: Annotated[int, Field(ge=0, le=100000)] | None = None
    max_exposure: Positive | None = None
    max_slippage_pct: Annotated[Decimal, Field(gt=0, le=100)] | None = None
    max_signal_age_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None
    max_consecutive_failures: Annotated[int, Field(ge=1, le=100)] | None = None
    min_available_margin: Positive | None = None
    allowed_symbols: list[Annotated[str, Field(min_length=1, max_length=60)]] | None = Field(None, max_length=500)
    trading_sessions: list[Session_] | None = Field(None, max_length=20)

    @field_validator("allowed_symbols")
    @classmethod
    def _symbols(cls, value: list[str] | None) -> list[str] | None:
        return sorted({normalise_symbol(s) for s in value}) if value is not None else None


class KillSwitchIn(BaseModel):
    enabled: bool
    reason: Annotated[str, Field(max_length=300)] = ""


def _scope(db: Session, user_id, scope_key: str) -> dict:
    if scope_key == "global":
        return {"scope_key": scope_key, "label": "All accounts"}
    kind, _, raw_id = scope_key.partition(":")
    try:
        object_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise AppError(404, "not_found", "Unknown scope.") from exc
    model = {"account": BrokerAccount, "strategy": Strategy}.get(kind)
    row = db.get(model, object_id) if model else None
    if row is None or row.user_id != user_id:
        raise AppError(404, "not_found", "Unknown scope.")
    return {"scope_key": f"{kind}:{object_id}", "label": getattr(row, "label", None) or row.name}


def limits_out(row: RiskLimits | None) -> dict:
    return {name: (getattr(row, name) if row else None) for name in LIMIT_FIELDS}


@router.get("")
def overview(ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    rows = {r.scope_key: r for r in db.scalars(select(RiskLimits).where(RiskLimits.user_id == ctx.user.id)).all()}
    control = db.get(TradingControl, ctx.user.id)
    halt = db.get(PlatformControl, 1)
    accounts = db.scalars(
        select(BrokerAccount)
        .where(BrokerAccount.user_id == ctx.user.id, BrokerAccount.status != "archived")
        .order_by(BrokerAccount.created_at)
    ).all()
    strategies = db.scalars(
        select(Strategy)
        .where(Strategy.user_id == ctx.user.id, Strategy.status != "archived")
        .order_by(Strategy.created_at)
    ).all()
    return {
        "kill_switch": {
            "enabled": bool(control and control.kill_switch),
            "reason": control.kill_switch_reason if control else None,
            "changed_at": control.kill_switch_at if control else None,
        },
        "platform_halted": bool(halt and halt.trading_halted),
        "platform_halt_reason": halt.reason if halt and halt.trading_halted else None,
        "defaults": {
            "max_signal_age_seconds": get_settings().default_max_signal_age_seconds,
            "max_consecutive_failures": get_settings().default_max_consecutive_failures,
        },
        "scopes": [
            {"scope_key": "global", "label": "All accounts", "limits": limits_out(rows.get("global"))},
            *[
                {
                    "scope_key": f"account:{a.id}",
                    "label": a.label,
                    "broker": a.broker,
                    "kill_switch": a.kill_switch,
                    "circuit_open": a.circuit_open,
                    "consecutive_failures": a.consecutive_failures,
                    "limits": limits_out(rows.get(f"account:{a.id}")),
                }
                for a in accounts
            ],
            *[
                {
                    "scope_key": f"strategy:{s.id}",
                    "label": s.name,
                    "kill_switch": s.kill_switch,
                    "limits": limits_out(rows.get(f"strategy:{s.id}")),
                }
                for s in strategies
            ],
        ],
    }


@router.put("/limits/{scope_key}")
def save_limits(
    scope_key: str,
    body: LimitsIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    scope = _scope(db, ctx.user.id, scope_key)
    row = db.scalar(
        select(RiskLimits).where(RiskLimits.user_id == ctx.user.id, RiskLimits.scope_key == scope["scope_key"])
    )
    if row is None:
        row = RiskLimits(user_id=ctx.user.id, scope_key=scope["scope_key"])
        db.add(row)
    values = body.model_dump()
    values["trading_sessions"] = [s for s in values["trading_sessions"]] if body.trading_sessions else None
    for name in LIMIT_FIELDS:
        setattr(row, name, values[name])
    record(
        db,
        ctx.user,
        request_meta(request),
        "risk.limits.updated",
        "risk_limits",
        scope["scope_key"],
        limits=plain({k: v for k, v in values.items() if v is not None}),
    )
    db.commit()
    return {"scope_key": scope["scope_key"], "label": scope["label"], "limits": limits_out(row)}


@router.post("/kill-switch")
def kill_switch(
    body: KillSwitchIn, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Stop all automated trading on every one of your accounts. Manual closes stay possible."""
    control = db.get(TradingControl, ctx.user.id)
    if control is None:
        control = TradingControl(user_id=ctx.user.id)
        db.add(control)
    control.kill_switch = body.enabled
    control.kill_switch_reason = (body.reason.strip() or None) if body.enabled else None
    control.kill_switch_at = utcnow()
    record(
        db,
        ctx.user,
        request_meta(request),
        "risk.kill_switch.global",
        "user",
        ctx.user.id,
        enabled=body.enabled,
        reason=body.reason,
    )
    db.commit()
    return {"enabled": control.kill_switch, "reason": control.kill_switch_reason, "changed_at": control.kill_switch_at}
