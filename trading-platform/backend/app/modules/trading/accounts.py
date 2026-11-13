"""Broker accounts: create, configure, connect (Zerodha login, terminal pairing), kill switch."""

import base64
import hashlib
import hmac
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.brokers import kite as kite_api
from app.brokers.base import BrokerError
from app.brokers.paper import DEFAULTS as PAPER_DEFAULTS
from app.core import ratelimit, tokens
from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.db import get_db
from app.core.errors import AppError
from app.core.http import request_meta
from app.models.trading import (
    BRIDGE_BROKERS,
    BrokerAccount,
    BrokerCredential,
    InstrumentMapping,
    Order,
    OrderState,
    Position,
    StrategyAccount,
)
from app.modules.auth.deps import AuthContext, current_user
from app.trading import engine
from app.trading.ledger import new_client_order_id, transition, utcnow
from app.trading.signal_format import normalise_symbol

from .common import owned, plain, record

router = APIRouter(prefix="/broker-accounts", tags=["broker accounts"])

Label = Annotated[str, Field(min_length=1, max_length=60)]


# ------------------------------------------------------------------ schemas


class AccountIn(BaseModel):
    broker: Literal["paper", "kite", "mt5", "mt4"]
    label: Label
    settings: dict = Field(default_factory=dict)


class AccountPatch(BaseModel):
    label: Label | None = None
    status: Literal["active", "paused"] | None = None
    settings: dict | None = None


class KillSwitchIn(BaseModel):
    enabled: bool
    reason: Annotated[str, Field(max_length=300)] = ""


class KiteCredentialsIn(BaseModel):
    api_key: Annotated[str, Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9]+$")]
    api_secret: Annotated[str, Field(min_length=4, max_length=128)]


class KiteSessionIn(BaseModel):
    state: Annotated[str, Field(max_length=300)]
    request_token: Annotated[str, Field(min_length=4, max_length=128)]


class MappingIn(BaseModel):
    alert_symbol: Annotated[str, Field(min_length=1, max_length=60)]
    broker_symbol: Annotated[str, Field(min_length=1, max_length=60)]
    exchange: Literal["NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "BCD"] | None = None
    product: Literal["MIS", "CNC", "NRML", "MTF"] | None = None
    lot_size: Decimal = Decimal(1)
    quantity_step: Decimal | None = None
    min_quantity: Decimal | None = None
    max_quantity: Decimal | None = None
    tick_size: Decimal | None = None
    contract_size: Decimal = Decimal(1)

    @field_validator("alert_symbol")
    @classmethod
    def _alert(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator("lot_size", "quantity_step", "min_quantity", "max_quantity", "tick_size", "contract_size")
    @classmethod
    def _positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("must be greater than zero")
        return value


def _clean_settings(broker: str, settings: dict) -> dict:
    """Only known, non-secret settings are stored, each checked."""
    allowed: dict[str, type] = {}
    if broker == "paper":
        allowed = {
            "starting_balance": Decimal,
            "fee_per_order": Decimal,
            "fee_pct": Decimal,
            "slippage_pct": Decimal,
            "currency": str,
        }
    elif broker == "kite":
        allowed = {"api_key": str}
    elif broker in BRIDGE_BROKERS:
        allowed = {"symbol_suffix": str, "magic": int}
    clean = {}
    for key, value in settings.items():
        if key not in allowed:
            raise AppError(422, "invalid_setting", f"'{key}' is not a setting for {broker} accounts.")
        kind = allowed[key]
        try:
            if kind is Decimal:
                number = Decimal(str(value))
                if not number.is_finite() or number < 0:
                    raise ValueError
                clean[key] = str(number)
            elif kind is int:
                number = int(value)
                if not 0 < number < 2**31:
                    raise ValueError
                clean[key] = number
            else:
                text = str(value).strip()
                if len(text) > 64:
                    raise ValueError
                clean[key] = text
        except (ValueError, ArithmeticError) as exc:
            raise AppError(422, "invalid_setting", f"'{key}' has an invalid value.") from exc
    return clean


def _credential(db: Session, account: BrokerAccount, kind: str) -> BrokerCredential | None:
    return db.scalar(
        select(BrokerCredential).where(BrokerCredential.broker_account_id == account.id, BrokerCredential.kind == kind)
    )


def _set_credential(
    db: Session,
    account: BrokerAccount,
    kind: str,
    *,
    secret: str | None = None,
    token_hash: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    row = _credential(db, account, kind)
    if row is None:
        row = BrokerCredential(user_id=account.user_id, broker_account_id=account.id, kind=kind)
        db.add(row)
    row.value_enc = get_cipher().encrypt(secret, engine.credential_aad(account.id, kind)) if secret else None
    row.token_hash = token_hash
    row.expires_at = expires_at


def account_out(db: Session, account: BrokerAccount) -> dict:
    now = utcnow()
    kinds = {
        row.kind: row
        for row in db.scalars(select(BrokerCredential).where(BrokerCredential.broker_account_id == account.id)).all()
    }
    token = kinds.get("kite_access_token")
    open_positions = db.scalar(
        select(Position.id).where(Position.broker_account_id == account.id, Position.quantity != 0).limit(1)
    )
    heartbeat_age = (now - account.last_heartbeat_at).total_seconds() if account.last_heartbeat_at else None
    return {
        "id": account.id,
        "broker": account.broker,
        "label": account.label,
        "status": account.status,
        "kill_switch": account.kill_switch,
        "kill_switch_reason": account.kill_switch_reason,
        "settings": account.settings or {},
        "connection_status": account.connection_status,
        "connection_message": account.connection_message,
        "last_heartbeat_at": account.last_heartbeat_at,
        "terminal_online": heartbeat_age is not None and heartbeat_age <= get_settings().bridge_offline_after_seconds,
        "currency": account.currency,
        "balance": account.balance,
        "equity": account.equity,
        "margin_available": account.margin_available,
        "snapshot_at": account.snapshot_at,
        "consecutive_failures": account.consecutive_failures,
        "circuit_open": account.circuit_open,
        "is_paper": account.broker == "paper",
        "has_api_secret": "kite_api_secret" in kinds,
        "kite_session_expires_at": token.expires_at if token else None,
        "bridge_paired": "bridge_device_token" in kinds,
        "has_open_positions": open_positions is not None,
        "created_at": account.created_at,
    }


# ------------------------------------------------------------------ accounts


@router.get("")
def list_accounts(
    include_archived: bool = False, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    query = select(BrokerAccount).where(BrokerAccount.user_id == ctx.user.id).order_by(BrokerAccount.created_at)
    if not include_archived:
        query = query.where(BrokerAccount.status != "archived")
    return [account_out(db, a) for a in db.scalars(query).all()]


@router.post("", status_code=201)
def create_account(
    body: AccountIn, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    settings = _clean_settings(body.broker, body.settings)
    if body.broker == "paper":
        settings = {**{k: v for k, v in PAPER_DEFAULTS.items()}, **settings}
    if db.scalar(
        select(BrokerAccount.id).where(BrokerAccount.user_id == ctx.user.id, BrokerAccount.label == body.label)
    ):
        raise AppError(409, "duplicate_label", "You already have an account with that name.")
    account = BrokerAccount(user_id=ctx.user.id, broker=body.broker, label=body.label.strip(), settings=settings)
    db.add(account)
    db.flush()
    if body.broker == "paper":
        engine._refresh_paper_snapshots(db, [account], utcnow())
    record(
        db,
        ctx.user,
        request_meta(request),
        "broker.account.created",
        "broker_account",
        account.id,
        broker=body.broker,
        label=account.label,
    )
    db.commit()
    return account_out(db, account)


@router.get("/{account_id}")
def get_account(account_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    return account_out(db, owned(db, BrokerAccount, account_id, ctx.user, "Account"))


@router.patch("/{account_id}")
def update_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    if account.status == "archived":
        raise AppError(409, "archived", "This account is archived.")
    changes = {}
    if body.label is not None and body.label.strip() != account.label:
        if db.scalar(
            select(BrokerAccount.id).where(
                BrokerAccount.user_id == ctx.user.id, BrokerAccount.label == body.label.strip()
            )
        ):
            raise AppError(409, "duplicate_label", "You already have an account with that name.")
        account.label = body.label.strip()
        changes["label"] = account.label
    if body.status is not None and body.status != account.status:
        account.status = body.status
        changes["status"] = body.status
    if body.settings is not None:
        account.settings = {**(account.settings or {}), **_clean_settings(account.broker, body.settings)}
        changes["settings"] = plain(account.settings)
    if changes:
        record(db, ctx.user, request_meta(request), "broker.account.updated", "broker_account", account.id, **changes)
    db.commit()
    return account_out(db, account)


@router.post("/{account_id}/archive")
def archive_account(
    account_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    working = db.scalar(
        select(Order.id)
        .where(
            Order.broker_account_id == account.id,
            Order.state.in_(["QUEUED", "EXECUTING", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "AWAITING_APPROVAL"]),
        )
        .limit(1)
    )
    if working is not None:
        raise AppError(
            409, "orders_working", "This account has orders in progress. Wait for them or cancel them first."
        )
    if db.scalar(select(Position.id).where(Position.broker_account_id == account.id, Position.quantity != 0).limit(1)):
        raise AppError(409, "positions_open", "Close this account's open positions before archiving it.")
    account.status = "archived"
    db.execute(delete(BrokerCredential).where(BrokerCredential.broker_account_id == account.id))
    for link in db.scalars(select(StrategyAccount).where(StrategyAccount.broker_account_id == account.id)).all():
        link.enabled = False
    record(db, ctx.user, request_meta(request), "broker.account.archived", "broker_account", account.id)
    db.commit()
    return account_out(db, account)


@router.post("/{account_id}/kill-switch")
def account_kill_switch(
    account_id: uuid.UUID,
    body: KillSwitchIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    account.kill_switch = body.enabled
    account.kill_switch_reason = body.reason.strip() or None if body.enabled else None
    record(
        db,
        ctx.user,
        request_meta(request),
        "risk.kill_switch.account",
        "broker_account",
        account.id,
        enabled=body.enabled,
        reason=body.reason,
    )
    db.commit()
    return account_out(db, account)


@router.post("/{account_id}/reset-circuit")
def reset_circuit(
    account_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    engine.reset_circuit(account)
    record(db, ctx.user, request_meta(request), "risk.circuit_reset", "broker_account", account.id)
    db.commit()
    return account_out(db, account)


@router.post("/{account_id}/refresh")
def refresh_account(account_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    """Read the latest balance from the broker (Zerodha) or recompute it (paper). Never places orders."""
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    ratelimit.enforce(f"refresh:{account.id}", limit=20, window_seconds=60)
    now = utcnow()
    if account.broker == "paper":
        engine._refresh_paper_snapshots(db, [account], now)
    elif account.broker == "kite":
        if account.connection_status != "connected":
            raise AppError(409, "not_connected", "Log in to Zerodha first.")
        try:
            info = engine.make_kite_adapter(db, account).get_account_info()
        except BrokerError as exc:
            if exc.auth_failed:
                account.connection_status = "login_required"
                account.connection_message = "Zerodha no longer accepts the saved login; log in again"
                db.commit()
            raise AppError(502, "broker_error", f"Zerodha: {exc}") from exc
        account.currency, account.balance, account.equity = info.currency, info.balance, info.equity
        account.margin_available, account.snapshot_at = info.margin_available, now
    db.commit()
    return account_out(db, account)


# --------------------------------------------------------------- Zerodha


def _sign_state(account_id: uuid.UUID, user_id: uuid.UUID, expires: int) -> str:
    message = f"{account_id}.{user_id}.{expires}".encode()
    digest = hmac.new(get_settings().secret_key.encode(), b"kite-login:" + message, hashlib.sha256).digest()
    return f"{account_id}.{expires}.{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"


def _verify_state(state: str, user_id: uuid.UUID) -> uuid.UUID:
    try:
        account_part, expires_part, _ = state.split(".", 2)
        account_id, expires = uuid.UUID(account_part), int(expires_part)
    except ValueError as exc:
        raise AppError(400, "invalid_state", "This Zerodha login link is not valid. Start again.") from exc
    if expires < time.time() or not hmac.compare_digest(_sign_state(account_id, user_id, expires), state):
        raise AppError(400, "invalid_state", "This Zerodha login link has expired or is not yours. Start again.")
    return account_id


@router.put("/{account_id}/kite/credentials")
def set_kite_credentials(
    account_id: uuid.UUID,
    body: KiteCredentialsIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    if account.broker != "kite":
        raise AppError(409, "wrong_broker", "This is not a Zerodha account.")
    account.settings = {**(account.settings or {}), "api_key": body.api_key}
    _set_credential(db, account, "kite_api_secret", secret=body.api_secret)
    db.execute(
        delete(BrokerCredential).where(
            BrokerCredential.broker_account_id == account.id, BrokerCredential.kind == "kite_access_token"
        )
    )
    account.connection_status = "login_required"
    account.connection_message = "log in to Zerodha to connect"
    record(db, ctx.user, request_meta(request), "broker.kite.credentials_saved", "broker_account", account.id)
    db.commit()
    return account_out(db, account)


@router.post("/{account_id}/kite/login")
def kite_login_url(account_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    api_key = (account.settings or {}).get("api_key")
    if account.broker != "kite" or not api_key or _credential(db, account, "kite_api_secret") is None:
        raise AppError(409, "credentials_missing", "Save your Kite Connect API key and secret first.")
    state = _sign_state(account.id, ctx.user.id, int(time.time()) + 600)
    return {
        "login_url": kite_api.login_url(api_key, state),
        "redirect_url": f"{get_settings().frontend_origin.rstrip('/')}/broker-accounts/kite/callback",
    }


@router.post("/kite/session")
def kite_session(
    body: KiteSessionIn, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Finish the Zerodha login: swap the one-time request token for today's access token."""
    ratelimit.enforce(f"kite-session:{ctx.user.id}", limit=10, window_seconds=300)
    account = owned(db, BrokerAccount, _verify_state(body.state, ctx.user.id), ctx.user, "Account")
    secret_row = _credential(db, account, "kite_api_secret")
    if secret_row is None:
        raise AppError(409, "credentials_missing", "Save your Kite Connect API key and secret first.")
    secret = get_cipher().decrypt(secret_row.value_enc, engine.credential_aad(account.id, secret_row.kind))
    adapter = kite_api.KiteAdapter(api_key=account.settings["api_key"], access_token=None)
    try:
        token = adapter.exchange_request_token(body.request_token, secret)
        info = adapter.get_account_info()
    except BrokerError as exc:
        account.connection_status = "error"
        account.connection_message = f"Zerodha login failed: {exc}"[:300]
        db.commit()
        raise AppError(502, "broker_error", f"Zerodha login failed: {exc}") from exc
    finally:
        adapter.disconnect()
    now = utcnow()
    _set_credential(db, account, "kite_access_token", secret=token, expires_at=kite_api.token_expiry(now))
    account.connection_status = "connected"
    account.connection_message = "logged in to Zerodha for today"
    account.last_connected_at = now
    account.currency, account.balance, account.equity = info.currency, info.balance, info.equity
    account.margin_available, account.snapshot_at = info.margin_available, now
    record(db, ctx.user, request_meta(request), "broker.kite.connected", "broker_account", account.id)
    db.commit()
    return account_out(db, account)


# ----------------------------------------------------------- terminal bridge


@router.post("/{account_id}/bridge/pair")
def pair_terminal(
    account_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Issue a new device token for the MetaTrader Expert Advisor. Shown once; any previous token stops working."""
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    if account.broker not in BRIDGE_BROKERS:
        raise AppError(409, "wrong_broker", "Only MetaTrader accounts are paired with a terminal.")
    if account.status == "archived":
        raise AppError(409, "archived", "This account is archived.")
    device_token = "tbd_" + tokens.new_token()
    _set_credential(db, account, "bridge_device_token", token_hash=tokens.hash_token(device_token))
    account.connection_status = "not_connected"
    account.connection_message = "waiting for the terminal to connect"
    account.last_heartbeat_at = None
    record(db, ctx.user, request_meta(request), "broker.bridge.paired", "broker_account", account.id)
    db.commit()
    return {
        "device_token": device_token,
        "api_url": f"{get_settings().frontend_origin.rstrip('/')}/api/v1/bridge",
        "account": account_out(db, account),
    }


# ------------------------------------------------------------ instruments


def mapping_out(m: InstrumentMapping) -> dict:
    return {
        k: getattr(m, k)
        for k in (
            "id",
            "alert_symbol",
            "broker_symbol",
            "exchange",
            "product",
            "lot_size",
            "quantity_step",
            "min_quantity",
            "max_quantity",
            "tick_size",
            "contract_size",
            "created_at",
        )
    }


@router.get("/{account_id}/instruments")
def list_mappings(account_id: uuid.UUID, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    rows = db.scalars(
        select(InstrumentMapping)
        .where(InstrumentMapping.broker_account_id == account.id)
        .order_by(InstrumentMapping.alert_symbol)
    ).all()
    return [mapping_out(m) for m in rows]


@router.put("/{account_id}/instruments")
def upsert_mapping(
    account_id: uuid.UUID,
    body: MappingIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    if account.broker == "kite" and (body.exchange is None or body.product is None):
        raise AppError(422, "invalid_mapping", "Zerodha mappings need an exchange and a product (MIS, CNC, NRML).")
    if body.min_quantity and body.max_quantity and body.min_quantity > body.max_quantity:
        raise AppError(422, "invalid_mapping", "The minimum quantity is above the maximum.")
    mapping = db.scalar(
        select(InstrumentMapping).where(
            InstrumentMapping.broker_account_id == account.id, InstrumentMapping.alert_symbol == body.alert_symbol
        )
    )
    if mapping is None:
        mapping = InstrumentMapping(
            user_id=ctx.user.id,
            broker_account_id=account.id,
            alert_symbol=body.alert_symbol,
            broker_symbol=body.broker_symbol,
        )
        db.add(mapping)
    for field, value in body.model_dump().items():
        setattr(mapping, field, value)
    record(
        db,
        ctx.user,
        request_meta(request),
        "broker.instrument.saved",
        "broker_account",
        account.id,
        alert_symbol=body.alert_symbol,
        broker_symbol=body.broker_symbol,
    )
    db.commit()
    return mapping_out(mapping)


@router.delete("/{account_id}/instruments/{mapping_id}", status_code=204)
def delete_mapping(
    account_id: uuid.UUID,
    mapping_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    mapping = owned(db, InstrumentMapping, mapping_id, ctx.user, "Mapping")
    if mapping.broker_account_id != account.id:
        raise AppError(404, "not_found", "Mapping not found.")
    db.delete(mapping)
    record(
        db,
        ctx.user,
        request_meta(request),
        "broker.instrument.deleted",
        "broker_account",
        account.id,
        alert_symbol=mapping.alert_symbol,
    )
    db.commit()


# ------------------------------------------------------------ manual close


@router.post("/{account_id}/close-all")
def close_all(
    account_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    """Queue market orders that close every position the platform holds on this account.

    Allowed even with a kill switch on: reducing risk is always possible.
    """
    account = owned(db, BrokerAccount, account_id, ctx.user, "Account")
    positions = db.scalars(
        select(Position).where(Position.broker_account_id == account.id, Position.quantity != 0)
    ).all()
    now = utcnow()
    created = []
    for position in positions:
        order = Order(
            user_id=ctx.user.id,
            broker_account_id=account.id,
            source="manual",
            leg=0,
            client_order_id=new_client_order_id(),
            is_paper=account.broker == "paper",
            intent="close",
            symbol=position.symbol,
            broker_symbol=position.symbol,
            order_type="MARKET",
            reference_price=position.last_price,
            filled_quantity=Decimal(0),
            attempts=0,
        )
        mapping = db.scalar(
            select(InstrumentMapping).where(
                InstrumentMapping.broker_account_id == account.id, InstrumentMapping.broker_symbol == position.symbol
            )
        )
        if mapping is not None:
            order.exchange, order.product = mapping.exchange, mapping.product
        transition(db, order, OrderState.VALIDATED, "close requested from the dashboard", now=now)
        order.next_attempt_at = now
        transition(db, order, OrderState.QUEUED, now=now)
        created.append(order)
    record(
        db,
        ctx.user,
        request_meta(request),
        "order.close_all_requested",
        "broker_account",
        account.id,
        orders=len(created),
    )
    db.commit()
    return {"orders": [o.client_order_id for o in created]}
