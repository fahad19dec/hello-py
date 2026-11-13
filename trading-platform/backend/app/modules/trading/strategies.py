"""Strategies and the accounts each one trades. Every change creates a new version."""

import re
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.http import request_meta
from app.models.trading import BrokerAccount, Strategy, StrategyAccount, StrategyVersion, Webhook
from app.modules.auth.deps import AuthContext, current_user
from app.trading.signal_format import DEFAULT_ALIASES, STANDARD_ACTIONS

from .common import owned, plain, record

router = APIRouter(prefix="/strategies", tags=["strategies"])

Code = Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_.\-]+$")]
Percent = Annotated[Decimal, Field(gt=0, lt=100)]


def _check_aliases(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    clean = {}
    for alias, target in value.items():
        alias, target = alias.strip().upper(), target.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{1,30}", alias):
            raise ValueError(f"alias '{alias}' may only contain letters, digits and _")
        if alias in STANDARD_ACTIONS:
            raise ValueError(f"'{alias}' is already a standard action")
        if target not in STANDARD_ACTIONS:
            raise ValueError(f"'{target}' is not a standard action")
        clean[alias] = target
    return clean


class StrategyIn(BaseModel):
    code: Code
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=2000)] = ""
    default_order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    default_stop_loss_pct: Percent | None = None
    default_take_profit_pct: Percent | None = None
    action_aliases: dict[str, str] | None = None

    _aliases = field_validator("action_aliases")(_check_aliases)


class StrategyPatch(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    status: Literal["active", "paused", "archived"] | None = None
    default_order_type: Literal["MARKET", "LIMIT"] | None = None
    default_stop_loss_pct: Percent | None = None
    default_take_profit_pct: Percent | None = None
    clear_default_stop_loss: bool = False
    clear_default_take_profit: bool = False
    action_aliases: dict[str, str] | None = None

    _aliases = field_validator("action_aliases")(_check_aliases)


class KillSwitchIn(BaseModel):
    enabled: bool


class LinkIn(BaseModel):
    enabled: bool = True
    approval_required: bool = False
    sizing_mode: Literal["signal", "fixed", "percent_capital", "risk_per_trade"] = "signal"
    sizing_value: Decimal | None = None

    @field_validator("sizing_value")
    @classmethod
    def _positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("must be greater than zero")
        return value


SNAPSHOT_FIELDS = (
    "code",
    "name",
    "description",
    "status",
    "kill_switch",
    "default_order_type",
    "default_stop_loss_pct",
    "default_take_profit_pct",
    "action_aliases",
)


def _links(db: Session, strategy: Strategy) -> list[dict]:
    rows = db.execute(
        select(StrategyAccount, BrokerAccount)
        .join(BrokerAccount, BrokerAccount.id == StrategyAccount.broker_account_id)
        .where(StrategyAccount.strategy_id == strategy.id)
        .order_by(BrokerAccount.created_at)
    ).all()
    return [
        {
            "broker_account_id": account.id,
            "label": account.label,
            "broker": account.broker,
            "account_status": account.status,
            "enabled": link.enabled,
            "approval_required": link.approval_required,
            "sizing_mode": link.sizing_mode,
            "sizing_value": link.sizing_value,
            "execution_mode": "paper"
            if account.broker == "paper"
            else ("semi_automatic" if link.approval_required else "automatic"),
        }
        for link, account in rows
    ]


def _snapshot(db: Session, strategy: Strategy) -> dict:
    data = {name: getattr(strategy, name) for name in SNAPSHOT_FIELDS}
    data["accounts"] = [
        {
            k: v
            for k, v in link.items()
            if k in ("broker_account_id", "enabled", "approval_required", "sizing_mode", "sizing_value")
        }
        for link in _links(db, strategy)
    ]
    return plain(data)


def _new_version(db: Session, strategy: Strategy, user_id) -> None:
    strategy.version += 1
    db.flush()
    db.add(
        StrategyVersion(
            user_id=strategy.user_id,
            strategy_id=strategy.id,
            version=strategy.version,
            snapshot=_snapshot(db, strategy),
            changed_by=user_id,
        )
    )


def strategy_out(db: Session, strategy: Strategy) -> dict:
    webhooks = db.scalar(
        select(func.count(Webhook.id)).where(Webhook.strategy_id == strategy.id, Webhook.revoked_at.is_(None))
    )
    return {
        "id": strategy.id,
        **{name: getattr(strategy, name) for name in SNAPSHOT_FIELDS},
        "version": strategy.version,
        "accounts": _links(db, strategy),
        "webhook_count": webhooks,
        "created_at": strategy.created_at,
        "updated_at": strategy.updated_at,
    }


@router.get("")
def list_strategies(
    include_archived: bool = False, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    query = select(Strategy).where(Strategy.user_id == ctx.user.id).order_by(Strategy.created_at)
    if not include_archived:
        query = query.where(Strategy.status != "archived")
    return [strategy_out(db, s) for s in db.scalars(query).all()]


@router.post("", status_code=201)
def create_strategy(
    body: StrategyIn, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    if db.scalar(select(Strategy.id).where(Strategy.user_id == ctx.user.id, Strategy.code == body.code)):
        raise AppError(409, "duplicate_code", f"You already have a strategy with the id '{body.code}'.")
    strategy = Strategy(
        user_id=ctx.user.id,
        code=body.code,
        name=body.name.strip(),
        description=body.description,
        default_order_type=body.default_order_type,
        default_stop_loss_pct=body.default_stop_loss_pct,
        default_take_profit_pct=body.default_take_profit_pct,
        action_aliases=body.action_aliases if body.action_aliases is not None else dict(DEFAULT_ALIASES),
        version=1,
    )
    db.add(strategy)
    db.flush()
    db.add(
        StrategyVersion(
            user_id=ctx.user.id,
            strategy_id=strategy.id,
            version=1,
            snapshot=_snapshot(db, strategy),
            changed_by=ctx.user.id,
        )
    )
    record(db, ctx.user, request_meta(request), "strategy.created", "strategy", strategy.id, strategy=strategy.code)
    db.commit()
    return strategy_out(db, strategy)


@router.get("/{strategy_id}")
def get_strategy(strategy_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    return strategy_out(db, owned(db, Strategy, strategy_id, ctx.user, "Strategy"))


@router.patch("/{strategy_id}")
def update_strategy(
    strategy_id: uuid.UUID,
    body: StrategyPatch,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    strategy = owned(db, Strategy, strategy_id, ctx.user, "Strategy")
    changed = []
    for name in (
        "name",
        "description",
        "status",
        "default_order_type",
        "default_stop_loss_pct",
        "default_take_profit_pct",
        "action_aliases",
    ):
        value = getattr(body, name)
        if value is not None and value != getattr(strategy, name):
            setattr(strategy, name, value.strip() if isinstance(value, str) and name == "name" else value)
            changed.append(name)
    if body.clear_default_stop_loss and strategy.default_stop_loss_pct is not None:
        strategy.default_stop_loss_pct = None
        changed.append("default_stop_loss_pct")
    if body.clear_default_take_profit and strategy.default_take_profit_pct is not None:
        strategy.default_take_profit_pct = None
        changed.append("default_take_profit_pct")
    if changed:
        _new_version(db, strategy, ctx.user.id)
        record(
            db,
            ctx.user,
            request_meta(request),
            "strategy.updated",
            "strategy",
            strategy.id,
            fields=changed,
            version=strategy.version,
        )
    db.commit()
    return strategy_out(db, strategy)


@router.post("/{strategy_id}/kill-switch")
def strategy_kill_switch(
    strategy_id: uuid.UUID,
    body: KillSwitchIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    strategy = owned(db, Strategy, strategy_id, ctx.user, "Strategy")
    if strategy.kill_switch != body.enabled:
        strategy.kill_switch = body.enabled
        _new_version(db, strategy, ctx.user.id)
        record(
            db,
            ctx.user,
            request_meta(request),
            "risk.kill_switch.strategy",
            "strategy",
            strategy.id,
            enabled=body.enabled,
        )
    db.commit()
    return strategy_out(db, strategy)


@router.put("/{strategy_id}/accounts/{account_id}")
def link_account(
    strategy_id: uuid.UUID,
    account_id: uuid.UUID,
    body: LinkIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    strategy = owned(db, Strategy, strategy_id, ctx.user, "Strategy")
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    if account.status == "archived":
        raise AppError(409, "archived", "This account is archived.")
    if body.sizing_mode != "signal" and body.sizing_value is None:
        raise AppError(422, "invalid_sizing", "This sizing mode needs a value.")
    if body.sizing_mode == "percent_capital" and body.sizing_value is not None and body.sizing_value > 100:
        raise AppError(422, "invalid_sizing", "Percent of capital must be at most 100.")
    link = db.scalar(
        select(StrategyAccount).where(
            StrategyAccount.strategy_id == strategy.id, StrategyAccount.broker_account_id == account.id
        )
    )
    if link is None:
        link = StrategyAccount(user_id=ctx.user.id, strategy_id=strategy.id, broker_account_id=account.id)
        db.add(link)
    link.enabled = body.enabled
    link.approval_required = body.approval_required
    link.sizing_mode = body.sizing_mode
    link.sizing_value = body.sizing_value if body.sizing_mode != "signal" else None
    db.flush()
    _new_version(db, strategy, ctx.user.id)
    record(
        db,
        ctx.user,
        request_meta(request),
        "strategy.account_linked",
        "strategy",
        strategy.id,
        broker_account_id=str(account.id),
        sizing_mode=body.sizing_mode,
        approval_required=body.approval_required,
    )
    db.commit()
    return strategy_out(db, strategy)


@router.delete("/{strategy_id}/accounts/{account_id}")
def unlink_account(
    strategy_id: uuid.UUID,
    account_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    strategy = owned(db, Strategy, strategy_id, ctx.user, "Strategy")
    link = db.scalar(
        select(StrategyAccount).where(
            StrategyAccount.strategy_id == strategy.id,
            StrategyAccount.broker_account_id == account_id,
            StrategyAccount.user_id == ctx.user.id,
        )
    )
    if link is None:
        raise AppError(404, "not_found", "That account is not linked to this strategy.")
    db.delete(link)
    db.flush()
    _new_version(db, strategy, ctx.user.id)
    record(
        db,
        ctx.user,
        request_meta(request),
        "strategy.account_unlinked",
        "strategy",
        strategy.id,
        broker_account_id=str(account_id),
    )
    db.commit()
    return strategy_out(db, strategy)


@router.get("/{strategy_id}/versions")
def versions(strategy_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    strategy = owned(db, Strategy, strategy_id, ctx.user, "Strategy")
    rows = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.version.desc())
    ).all()
    return [
        {
            "version": v.version,
            "snapshot": v.snapshot,
            "created_at": v.created_at,
            "changed_by_you": v.changed_by == ctx.user.id,
        }
        for v in rows
    ]
