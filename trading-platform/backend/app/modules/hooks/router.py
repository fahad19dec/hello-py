"""Incoming TradingView alerts and Zerodha postbacks.

The alert endpoint does the minimum and answers quickly: authenticate, check
size and format, check the timestamp, store the signal once, answer. Workers
turn stored signals into orders. Nothing here contacts a broker.

TradingView cannot sign requests or add headers, so a webhook is authenticated
by the long random token in its URL, plus an optional passphrase in the
message. Senders that can sign may also be required to send an HMAC header.
"""

import hashlib
import hmac
import json
import logging
from datetime import timedelta

import redis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.brokers import kite as kite_api
from app.core import ratelimit, tokens
from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.db import SYSTEM, access, get_db
from app.core.http import client_ip
from app.models.trading import BrokerAccount, BrokerCredential, Order, Signal, Strategy, Webhook
from app.trading import risk
from app.trading.engine import _apply_status, credential_aad
from app.trading.ledger import utcnow
from app.trading.signal_format import (
    MAX_BODY_BYTES,
    SignalError,
    check_action_fields,
    derived_signal_id,
    parse,
    resolve_action,
    sanitise_payload,
)

router = APIRouter(prefix="/hooks", tags=["webhooks"])
log = logging.getLogger("app.hooks")
WAKE_QUEUE = "tp:wake"


def _answer(status_code: int, status: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"status": status, "message": message, **extra}, status_code=status_code)


def wake_workers() -> None:
    """Best effort: a worker also polls, so a Redis outage only delays processing."""
    try:
        client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
        client.lpush(WAKE_QUEUE, "1")
        client.ltrim(WAKE_QUEUE, 0, 100)
    except redis.RedisError:
        pass


def _store(db: Session, webhook: Webhook, client_signal_id: str, values: dict) -> tuple[bool, Signal | None]:
    """Insert the signal once. Returns (stored_now, row). A repeat only counts as a duplicate."""
    statement = (
        insert(Signal)
        .values(user_id=webhook.user_id, webhook_id=webhook.id, client_signal_id=client_signal_id, **values)
        .on_conflict_do_nothing(constraint="uq_signals_webhook_id_client_signal_id")
        .returning(Signal.id)
    )
    signal_id = db.execute(statement).scalar()
    if signal_id is not None:
        return True, db.get(Signal, signal_id)
    db.execute(
        update(Signal)
        .where(Signal.webhook_id == webhook.id, Signal.client_signal_id == client_signal_id)
        .values(duplicate_count=Signal.duplicate_count + 1, last_duplicate_at=utcnow())
    )
    return False, db.scalar(
        select(Signal).where(Signal.webhook_id == webhook.id, Signal.client_signal_id == client_signal_id)
    )


@router.post("/{token}")
async def receive_alert(token: str, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    now = utcnow()
    ip = client_ip(request)
    if len(token) > 100:
        return _answer(404, "not_found", "unknown webhook")
    ratelimit.enforce(f"hook-ip:{ip}", limit=600, window_seconds=60)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _answer(413, "rejected", f"alert message is larger than {MAX_BODY_BYTES} bytes")

    with access(db, SYSTEM):
        webhook = db.scalar(select(Webhook).where(Webhook.token_hash == tokens.hash_token(token)))
        if webhook is None or webhook.revoked_at is not None:
            return _answer(404, "not_found", "unknown webhook")
        ratelimit.enforce(f"hook:{webhook.id}", limit=120, window_seconds=60)
        webhook.last_received_at = now
        strategy = db.get(Strategy, webhook.strategy_id)

        def reject(
            status_code: int,
            reason: str,
            *,
            client_signal_id: str | None = None,
            data: dict | None = None,
            values: dict | None = None,
        ) -> JSONResponse:
            stored, signal = _store(
                db,
                webhook,
                client_signal_id or derived_signal_id(body),
                {
                    "strategy_id": webhook.strategy_id,
                    "status": "rejected",
                    "reason": reason[:500],
                    "payload": sanitise_payload(data) if data else {"raw": body.decode("utf-8", "replace")[:2000]},
                    "source_ip": ip,
                    **(values or {}),
                },
            )
            db.commit()
            if not stored:
                return _answer(200, "duplicate", "this signal was already received", signal_id=str(signal.id))
            return _answer(status_code, "rejected", reason, signal_id=str(signal.id))

        # Optional HMAC signature, for senders that can sign (TradingView cannot).
        if webhook.signing_secret_enc:
            secret = get_cipher().decrypt(webhook.signing_secret_enc, f"webhook:{webhook.id}:signing")
            expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(request.headers.get("x-signature", ""), expected):
                return reject(401, "missing or invalid X-Signature")
        if not webhook.enabled:
            return reject(403, "this webhook is disabled")

        try:
            data, payload = parse(body)
        except SignalError as exc:
            data = None
            try:
                candidate = json.loads(body)
                data = candidate if isinstance(candidate, dict) else None
            except (ValueError, UnicodeDecodeError):
                pass
            client_signal_id = str(data.get("signal_id"))[:100] if data and data.get("signal_id") else None
            return reject(400, str(exc), client_signal_id=client_signal_id, data=data)

        client_signal_id = payload.signal_id or derived_signal_id(body)
        base = {"signal_time": payload.timestamp, "symbol": payload.symbol}

        if webhook.passphrase_hash:
            supplied = str(data.get("passphrase", ""))
            if not hmac.compare_digest(tokens.hash_token(supplied), webhook.passphrase_hash):
                return reject(
                    401, "wrong or missing passphrase", client_signal_id=client_signal_id, data=data, values=base
                )

        if payload.strategy_id and strategy and payload.strategy_id != strategy.code:
            return reject(
                400,
                f"strategy_id '{payload.strategy_id}' does not match this webhook's strategy '{strategy.code}'",
                client_signal_id=client_signal_id,
                data=data,
                values=base,
            )
        try:
            action = resolve_action(payload.action, (strategy.action_aliases if strategy else {}) or {})
            check_action_fields(action, payload)
        except SignalError as exc:
            return reject(400, str(exc), client_signal_id=client_signal_id, data=data, values=base)
        base["action"] = action

        # Freshness and replay protection: a signal must be recent, and is accepted once.
        limits = risk.load_limits(db, webhook.user_id, None, webhook.strategy_id)
        max_age = limits.get("max_signal_age_seconds") or settings.default_max_signal_age_seconds
        age = (now - payload.timestamp).total_seconds()
        if age > max_age:
            return reject(
                422,
                f"signal is {age:.0f}s old; the limit is {max_age}s",
                client_signal_id=client_signal_id,
                data=data,
                values=base,
            )
        if payload.timestamp > now + timedelta(seconds=settings.signal_clock_skew_seconds):
            return reject(
                422,
                "signal timestamp is in the future; check the sender's clock",
                client_signal_id=client_signal_id,
                data=data,
                values=base,
            )

        stored, signal = _store(
            db,
            webhook,
            client_signal_id,
            {
                **base,
                "strategy_id": webhook.strategy_id,
                "status": "received",
                "payload": sanitise_payload(data),
                "source_ip": ip,
            },
        )
        db.commit()
    if not stored:
        return _answer(
            200,
            "duplicate",
            "this signal was already received; it will not be executed again",
            signal_id=str(signal.id),
        )
    wake_workers()
    return _answer(202, "accepted", "signal stored; orders are being prepared", signal_id=str(signal.id))


@router.post("/kite/postback")
async def kite_postback(request: Request, db: Session = Depends(get_db)):
    """Zerodha order updates. Verified with the account's API secret, then applied like a status poll."""
    try:
        data = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return _answer(400, "rejected", "not JSON")
    if not isinstance(data, dict):
        return _answer(400, "rejected", "not an object")
    order_id = str(data.get("order_id") or "")
    with access(db, SYSTEM):
        order = db.scalar(select(Order).where(Order.broker_order_id == order_id)) if order_id else None
        if order is None:
            return _answer(200, "ignored", "not an order placed by this platform")
        account = db.get(BrokerAccount, order.broker_account_id)
        secret_row = db.scalar(
            select(BrokerCredential).where(
                BrokerCredential.broker_account_id == account.id, BrokerCredential.kind == "kite_api_secret"
            )
        )
        if secret_row is None:
            return _answer(401, "rejected", "account has no API secret")
        secret = get_cipher().decrypt(secret_row.value_enc, credential_aad(account.id, secret_row.kind))
        expected = kite_api.postback_checksum(order_id, str(data.get("order_timestamp", "")), secret)
        if not hmac.compare_digest(str(data.get("checksum", "")), expected):
            return _answer(401, "rejected", "checksum does not match")
        status = kite_api.KiteAdapter._status_of(data)
        _apply_status(db, order, account, status, utcnow())
        db.commit()
    return _answer(200, "ok", "applied")
