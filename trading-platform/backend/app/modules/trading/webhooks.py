"""Webhook connections: create, rotate, revoke, test. Credentials are shown once and stored hashed."""

import json
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import tokens
from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.db import get_db
from app.core.errors import AppError
from app.core.http import request_meta
from app.models.identity import User
from app.models.trading import BrokerAccount, Strategy, StrategyAccount, Webhook
from app.modules.auth.deps import AuthContext, current_user
from app.trading import risk
from app.trading.engine import ACTION_INTENTS, _capital, resolve_instrument
from app.trading.ledger import utcnow
from app.trading.signal_format import SignalError, check_action_fields, parse, resolve_action
from app.trading.sizing import SizingError, compute_quantity, validate_prices

from .common import owned, record

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookIn(BaseModel):
    strategy_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=100)]
    passphrase: Annotated[str, Field(min_length=8, max_length=200)] | None = None
    require_signature: bool = False


class WebhookPatch(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    enabled: bool | None = None
    passphrase: Annotated[str, Field(min_length=8, max_length=200)] | None = None
    clear_passphrase: bool = False


class TestIn(BaseModel):
    message: Annotated[str, Field(min_length=2, max_length=8192)]


def url_for(token: str) -> str:
    return f"{get_settings().frontend_origin.rstrip('/')}/api/v1/hooks/{token}"


def webhook_out(db: Session, webhook: Webhook) -> dict:
    strategy = db.get(Strategy, webhook.strategy_id)
    return {
        "id": webhook.id,
        "name": webhook.name,
        "strategy_id": webhook.strategy_id,
        "strategy_code": strategy.code if strategy else None,
        "strategy_name": strategy.name if strategy else None,
        "url_hint": f".../api/v1/hooks/...{webhook.token_hint}",
        "has_passphrase": webhook.passphrase_hash is not None,
        "requires_signature": webhook.signing_secret_enc is not None,
        "enabled": webhook.enabled,
        "revoked": webhook.revoked_at is not None,
        "revoked_at": webhook.revoked_at,
        "last_received_at": webhook.last_received_at,
        "created_at": webhook.created_at,
    }


def _issue(webhook: Webhook) -> str:
    token = tokens.new_token()
    webhook.token_hash = tokens.hash_token(token)
    webhook.token_hint = token[-4:]
    return token


@router.get("")
def list_webhooks(ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Webhook).where(Webhook.user_id == ctx.user.id).order_by(Webhook.created_at)).all()
    return [webhook_out(db, w) for w in rows]


@router.post("", status_code=201)
def create_webhook(
    body: WebhookIn, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    strategy = owned(db, Strategy, body.strategy_id, ctx.user, "Strategy")
    if strategy.status == "archived":
        raise AppError(409, "archived", "This strategy is archived.")
    webhook = Webhook(
        user_id=ctx.user.id, strategy_id=strategy.id, name=body.name.strip(), token_hash="", token_hint=""
    )
    token = _issue(webhook)
    if body.passphrase:
        webhook.passphrase_hash = tokens.hash_token(body.passphrase)
    db.add(webhook)
    db.flush()
    signing_secret = None
    if body.require_signature:
        signing_secret = tokens.new_token()
        webhook.signing_secret_enc = get_cipher().encrypt(signing_secret, f"webhook:{webhook.id}:signing")
    record(db, ctx.user, request_meta(request), "webhook.created", "webhook", webhook.id, strategy=strategy.code)
    db.commit()
    return {"webhook": webhook_out(db, webhook), "url": url_for(token), "signing_secret": signing_secret}


@router.patch("/{webhook_id}")
def update_webhook(
    webhook_id: uuid.UUID,
    body: WebhookPatch,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    webhook = owned(db, Webhook, webhook_id, ctx.user, "Webhook")
    if webhook.revoked_at is not None:
        raise AppError(409, "revoked", "This webhook is revoked. Create a new one.")
    changed = []
    if body.name is not None:
        webhook.name = body.name.strip()
        changed.append("name")
    if body.enabled is not None and body.enabled != webhook.enabled:
        webhook.enabled = body.enabled
        changed.append("enabled")
    if body.passphrase:
        webhook.passphrase_hash = tokens.hash_token(body.passphrase)
        changed.append("passphrase")
    elif body.clear_passphrase and webhook.passphrase_hash:
        webhook.passphrase_hash = None
        changed.append("passphrase_removed")
    if changed:
        record(db, ctx.user, request_meta(request), "webhook.updated", "webhook", webhook.id, fields=changed)
    db.commit()
    return webhook_out(db, webhook)


@router.post("/{webhook_id}/rotate")
def rotate_webhook(
    webhook_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """A new URL. The old one stops working immediately."""
    webhook = owned(db, Webhook, webhook_id, ctx.user, "Webhook")
    if webhook.revoked_at is not None:
        raise AppError(409, "revoked", "This webhook is revoked. Create a new one.")
    token = _issue(webhook)
    record(db, ctx.user, request_meta(request), "webhook.rotated", "webhook", webhook.id)
    db.commit()
    return {"webhook": webhook_out(db, webhook), "url": url_for(token)}


@router.post("/{webhook_id}/revoke")
def revoke_webhook(
    webhook_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Permanently disable this webhook (for example if its URL leaked)."""
    webhook = owned(db, Webhook, webhook_id, ctx.user, "Webhook")
    if webhook.revoked_at is None:
        webhook.revoked_at = utcnow()
        webhook.enabled = False
        record(db, ctx.user, request_meta(request), "webhook.revoked", "webhook", webhook.id)
    db.commit()
    return webhook_out(db, webhook)


@router.post("/{webhook_id}/test")
def test_webhook(
    webhook_id: uuid.UUID, body: TestIn, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Check an alert message the way a real one is checked, without storing or executing anything."""
    webhook = owned(db, Webhook, webhook_id, ctx.user, "Webhook")
    strategy = db.get(Strategy, webhook.strategy_id)
    report: dict = {"valid": False, "problems": [], "accounts": []}
    raw = body.message.encode()
    try:
        data, payload = parse(raw)
        action = resolve_action(payload.action, strategy.action_aliases or {})
        check_action_fields(action, payload)
    except SignalError as exc:
        report["problems"].append(str(exc))
        return report
    if webhook.passphrase_hash and tokens.hash_token(str(data.get("passphrase", ""))) != webhook.passphrase_hash:
        report["problems"].append("wrong or missing passphrase")
    if payload.strategy_id and payload.strategy_id != strategy.code:
        report["problems"].append(f"strategy_id '{payload.strategy_id}' does not match '{strategy.code}'")
    now = utcnow()
    limits = risk.load_limits(db, ctx.user.id, None, strategy.id)
    max_age = limits.get("max_signal_age_seconds") or get_settings().default_max_signal_age_seconds
    age = (now - payload.timestamp).total_seconds()
    report["signal_age_seconds"] = round(age, 1)
    if age > max_age:
        report["problems"].append(
            f"signal is {age:.0f}s old; the limit is {max_age}s (live alerts use {{{{timenow}}}})"
        )
    if payload.timestamp > now + timedelta(seconds=get_settings().signal_clock_skew_seconds):
        report["problems"].append("timestamp is in the future")
    if strategy.status != "active":
        report["problems"].append(f"strategy is {strategy.status}")
    report["action"] = action
    intent = ACTION_INTENTS[action][0]
    user = db.get(User, ctx.user.id)
    links = db.execute(
        select(StrategyAccount, BrokerAccount)
        .join(BrokerAccount, BrokerAccount.id == StrategyAccount.broker_account_id)
        .where(
            StrategyAccount.strategy_id == strategy.id,
            StrategyAccount.enabled.is_(True),
            BrokerAccount.status != "archived",
        )
    ).all()
    if not links:
        report["problems"].append("no enabled accounts are linked to this strategy")
    for link, account in links:
        outcome = {"account": account.label, "broker": account.broker, "result": "would be queued"}
        instrument = resolve_instrument(db, account, payload.symbol) if payload.symbol else None
        if payload.symbol and instrument is None:
            outcome["result"] = f"rejected: {payload.symbol} has no instrument mapping"
            report["accounts"].append(outcome)
            continue
        quantity = None
        if intent == "open":
            try:
                validate_prices(
                    instrument,
                    price=payload.price if payload.order_type != "MARKET" else None,
                    trigger_price=payload.trigger_price,
                    stop_loss=payload.stop_loss,
                    take_profit=payload.take_profit,
                )
                quantity = compute_quantity(
                    link.sizing_mode,
                    link.sizing_value,
                    signal_quantity=payload.quantity,
                    instrument=instrument,
                    capital=_capital(db, account),
                    price=payload.price,
                    stop_loss=payload.stop_loss,
                )
                outcome["quantity"] = str(quantity)
            except SizingError as exc:
                outcome["result"] = f"rejected: {exc}"
                report["accounts"].append(outcome)
                continue
        draft = risk.OrderDraft(
            intent=intent,
            symbol=instrument.broker_symbol if instrument else "*",
            exchange=instrument.exchange if instrument else None,
            quantity=quantity,
            price=payload.price,
            signal_time=None,
            is_entry=intent == "open",
        )
        rejection = risk.check(
            db, account=account, strategy=strategy, draft=draft, user_timezone=user.timezone, now=now
        )
        if rejection:
            outcome["result"] = f"rejected ({rejection.rule}): {rejection.message}"
        elif link.approval_required:
            outcome["result"] = "would wait for your approval"
        report["accounts"].append(outcome)
    report["valid"] = not report["problems"]
    report["normalised"] = json.loads(payload.model_dump_json(exclude_none=True))
    return report
