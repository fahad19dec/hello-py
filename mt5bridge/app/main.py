import hmac
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import kite
from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .models import Alert, Account, Order, User, utcnow
from .security import decrypt, encrypt, hash_password, new_token, verify_password
from .signals import parse_signal

MAX_ALERT_BYTES = 4096
# A claimed order with no result after this long is marked failed, never re-run.
CLAIM_TIMEOUT = timedelta(minutes=5)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Base.metadata.create_all(engine)

app = FastAPI(title="Trade Bridge", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.https_only,
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_recent_alerts: dict[int, deque] = defaultdict(deque)


def _rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    window = _recent_alerts[user_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= settings.alerts_per_minute:
        return True
    window.append(now)
    return False


# ---------------------------------------------------------------- web pages


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    return db.get(User, user_id) if user_id else None


def _flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _sweep_stale_orders(db: Session, user_id: int) -> None:
    """Stop orders nobody picked up from showing as queued forever.

    MT5 orders wait for a Windows worker; Zerodha orders are sent right after
    the webhook replies. Neither is ever executed late.
    """
    now = utcnow()
    kite_account_ids = select(Account.id).where(Account.user_id == user_id, Account.broker == "kite")
    db.execute(
        update(Order)
        .where(
            Order.user_id == user_id,
            Order.status == "queued",
            Order.account_id.not_in(kite_account_ids),
            Order.created_at < now - timedelta(seconds=settings.max_order_age_seconds),
        )
        .values(status="expired", message="not executed in time; signal was dropped", done_at=now)
    )
    db.execute(
        update(Order)
        .where(
            Order.user_id == user_id,
            Order.status == "queued",
            Order.account_id.in_(kite_account_ids),
            Order.created_at < now - CLAIM_TIMEOUT,
        )
        .values(
            status="failed",
            message="result unknown (the server restarted); check your Zerodha order book",
            done_at=now,
        )
    )
    db.commit()


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return _redirect("/login")
    _sweep_stale_orders(db, user.id)
    accounts = db.scalars(
        select(Account).where(Account.user_id == user.id).order_by(Account.id)
    ).all()
    orders = db.scalars(
        select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(50)
    ).all()
    rejected = db.scalars(
        select(Alert)
        .where(Alert.user_id == user.id, Alert.status == "rejected")
        .order_by(Alert.id.desc())
        .limit(10)
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "accounts": accounts,
            "orders": orders,
            "rejected": rejected,
            "webhook_url": f"{settings.public_url}/hook/{user.webhook_token}",
            "kite_redirect_url": f"{settings.public_url}/kite/callback",
            "kite_static_ip": settings.kite_static_ip,
            "kite_ready": {a.id: kite_session_valid(a) for a in accounts if a.broker == "kite"},
            "flash": request.session.pop("flash", None),
        },
    )


@app.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse(
        request, "auth.html", {"mode": "signup", "flash": request.session.pop("flash", None)}
    )


@app.post("/signup")
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 255:
        _flash(request, "Enter a valid email address.")
        return _redirect("/signup")
    if len(password) < 8:
        _flash(request, "Password must be at least 8 characters.")
        return _redirect("/signup")
    if db.scalar(select(User).where(User.email == email)):
        _flash(request, "That email is already registered.")
        return _redirect("/signup")
    user = User(email=email, password_hash=hash_password(password), webhook_token=new_token())
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    return _redirect("/")


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request, "auth.html", {"mode": "login", "flash": request.session.pop("flash", None)}
    )


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not verify_password(password, user.password_hash):
        _flash(request, "Wrong email or password.")
        return _redirect("/login")
    request.session["user_id"] = user.id
    return _redirect("/")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _redirect("/login")


@app.post("/webhook/rotate")
def rotate_webhook(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return _redirect("/login")
    user.webhook_token = new_token()
    db.commit()
    _flash(request, "Webhook URL replaced. Update it in your TradingView alerts.")
    return _redirect("/")


@app.post("/accounts")
def add_account(
    request: Request,
    label: str = Form(...),
    login: str = Form(...),
    server: str = Form(...),
    password: str = Form(...),
    max_lots: float = Form(1.0),
    symbol_suffix: str = Form(""),
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if not user:
        return _redirect("/login")
    label, login, server = label.strip(), login.strip(), server.strip()
    symbol_suffix = symbol_suffix.strip()
    error = None
    if not label or len(label) > 50:
        error = "Give the account a name of up to 50 characters."
    elif not login.isdigit() or len(login) > 20:
        error = "MT5 login must be the account number."
    elif not server or len(server) > 100:
        error = "Enter the broker server name shown in MT5."
    elif not password:
        error = "Enter the MT5 trading password."
    elif not (0 < max_lots <= 1000):
        error = "Max lots must be greater than zero."
    elif len(symbol_suffix) > 16:
        error = "Symbol suffix is too long."
    elif db.scalar(
        select(Account).where(Account.user_id == user.id, Account.label == label)
    ):
        error = "You already have an account with that name."
    if error:
        _flash(request, error)
        return _redirect("/")
    db.add(
        Account(
            user_id=user.id,
            label=label,
            login=login,
            server=server,
            password_enc=encrypt(password),
            max_lots=max_lots,
            symbol_suffix=symbol_suffix,
        )
    )
    db.commit()
    _flash(request, "Account added. It connects within about a minute.")
    return _redirect("/")


def _own_account(request: Request, db: Session, account_id: int) -> Account:
    user = current_user(request, db)
    account = db.get(Account, account_id)
    if not user or not account or account.user_id != user.id:
        raise HTTPException(status_code=404)
    return account


@app.post("/accounts/{account_id}/toggle")
def toggle_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = _own_account(request, db, account_id)
    account.enabled = not account.enabled
    if not account.enabled and account.broker == "mt5":
        account.status, account.status_message = "pending", ""
    db.commit()
    return _redirect("/")


@app.post("/accounts/{account_id}/delete")
def delete_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = _own_account(request, db, account_id)
    db.execute(delete(Order).where(Order.account_id == account.id))
    db.delete(account)
    db.commit()
    _flash(request, "Account and its order history removed.")
    return _redirect("/")


# ------------------------------------------------------------------ zerodha

IST_OFFSET = timedelta(hours=5, minutes=30)


def kite_session_valid(account: Account, now: datetime | None = None) -> bool:
    """Zerodha access tokens die at about 6:00 IST the morning after login."""
    if not account.access_token_enc or not account.token_at:
        return False
    now_ist = (now or utcnow()) + IST_OFFSET
    cutoff_ist = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_ist < cutoff_ist:
        cutoff_ist -= timedelta(days=1)
    return account.token_at + IST_OFFSET >= cutoff_ist


@app.post("/accounts/kite")
def add_kite_account(
    request: Request,
    label: str = Form(...),
    api_key: str = Form(...),
    api_secret: str = Form(...),
    max_quantity: int = Form(1),
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if not user:
        return _redirect("/login")
    label, api_key, api_secret = label.strip(), api_key.strip(), api_secret.strip()
    error = None
    if not label or len(label) > 50:
        error = "Give the account a name of up to 50 characters."
    elif not api_key.isalnum() or len(api_key) > 64:
        error = "Enter the API key from your Kite Connect app."
    elif not api_secret or len(api_secret) > 128:
        error = "Enter the API secret from your Kite Connect app."
    elif not (0 < max_quantity <= 1_000_000):
        error = "Max quantity must be greater than zero."
    elif db.scalar(select(Account).where(Account.user_id == user.id, Account.label == label)):
        error = "You already have an account with that name."
    if error:
        _flash(request, error)
        return _redirect("/")
    db.add(
        Account(
            user_id=user.id,
            label=label,
            broker="kite",
            api_key=api_key,
            api_secret_enc=encrypt(api_secret),
            max_quantity=max_quantity,
            status="error",
            status_message="Zerodha login needed",
        )
    )
    db.commit()
    _flash(request, "Zerodha account added. Press 'Log in to Zerodha' to connect it for today.")
    return _redirect("/")


@app.get("/kite/{account_id}/login")
def kite_login(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = _own_account(request, db, account_id)
    if account.broker != "kite":
        raise HTTPException(status_code=404)
    # Zerodha redirects back without saying which account, so remember it here.
    request.session["kite_account_id"] = account.id
    return _redirect(kite.login_url(account.api_key))


@app.get("/kite/callback")
def kite_callback(
    request: Request,
    request_token: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if not user:
        return _redirect("/login")
    account = db.get(Account, request.session.pop("kite_account_id", 0))
    if not account or account.user_id != user.id or account.broker != "kite":
        _flash(request, "Start the Zerodha login from the dashboard.")
        return _redirect("/")
    # Zerodha documents only request_token; a status other than success means a cancel.
    if not request_token or (status and status != "success"):
        _flash(request, "Zerodha login was cancelled.")
        return _redirect("/")
    try:
        with kite.new_client() as client:
            token = kite.exchange_token(
                client, account.api_key, decrypt(account.api_secret_enc), request_token
            )
    except (kite.KiteError, httpx.HTTPError) as exc:
        account.status, account.status_message = "error", f"Zerodha login failed: {exc}"[:255]
        db.commit()
        _flash(request, f"Zerodha login failed: {exc}")
        return _redirect("/")
    account.access_token_enc = encrypt(token)
    account.token_at = utcnow()
    account.status, account.status_message = "connected", ""
    account.last_seen = utcnow()
    db.commit()
    _flash(request, f"{account.label} is connected to Zerodha until tomorrow morning.")
    return _redirect("/")


def run_kite_order(order_id: int) -> None:
    """Send one queued order to Zerodha. Runs right after the webhook replies."""
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if not order or order.status != "queued":
            return
        account = order.account
        try:
            with kite.new_client() as client:
                result = kite.execute(
                    client,
                    account.api_key,
                    decrypt(account.access_token_enc),
                    {
                        "action": order.action,
                        "symbol": order.symbol,
                        "exchange": order.exchange,
                        "quantity": order.quantity,
                        "product": order.product,
                        "order_type": order.order_type,
                        "limit_price": order.limit_price,
                        "reverse": order.reverse,
                        "comment": order.comment,
                    },
                )
        except Exception as exc:  # never leave the order looking queued
            result = {
                "ok": False,
                "message": f"unexpected error ({type(exc).__name__}); check your Zerodha order book",
            }
        order.status = "placed" if result["ok"] else "failed"
        order.message = result["message"][:500]
        order.ticket = result.get("ticket")
        order.done_at = utcnow()
        if result.get("token_expired"):
            account.access_token_enc = ""
            account.status, account.status_message = "error", "Zerodha login needed"
        db.commit()


def _prepare_order(order: Order, account: Account, signal) -> None:
    """Fill in the broker-specific fields, or reject the order with a reason."""

    def reject(message: str) -> None:
        order.status, order.message, order.done_at = "rejected", message, utcnow()

    opening = signal.action in ("buy", "sell")
    if account.broker == "mt5":
        order.symbol = signal.symbol + account.symbol_suffix
        order.lots, order.sl, order.tp = signal.lots, signal.sl, signal.tp
        if opening and signal.lots > account.max_lots:
            reject(f"{signal.lots} lots is above this account's limit of {account.max_lots}")
        return

    order.symbol = signal.symbol
    order.quantity = signal.quantity
    order.exchange = signal.exchange or "NSE"
    order.product, order.order_type, order.limit_price = signal.product, signal.order_type, signal.price
    if not kite_session_valid(account):
        reject("Zerodha login needed for today; log in from the dashboard")
    elif opening and signal.quantity > account.max_quantity:
        reject(f"quantity {signal.quantity} is above this account's limit of {account.max_quantity}")
    elif signal.sl is not None or signal.tp is not None:
        reject("sl and tp are not supported on Zerodha accounts; remove them from the alert")


# ------------------------------------------------------------------ webhook


@app.post("/hook/{token}")
async def webhook(
    token: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)
):
    user = db.scalar(select(User).where(User.webhook_token == token))
    if not user:
        raise HTTPException(status_code=404)
    body = await request.body()
    if len(body) > MAX_ALERT_BYTES:
        raise HTTPException(status_code=413, detail="alert message too large")
    if _rate_limited(user.id):
        raise HTTPException(status_code=429, detail="too many alerts")

    raw = body.decode("utf-8", errors="replace")

    def reject(message: str) -> JSONResponse:
        db.add(Alert(user_id=user.id, raw=raw, status="rejected", error=message[:500]))
        db.commit()
        return JSONResponse({"error": message}, status_code=400)

    try:
        signal = parse_signal(body)
    except ValueError as exc:
        return reject(str(exc))

    accounts = db.scalars(
        select(Account).where(Account.user_id == user.id, Account.enabled.is_(True))
    ).all()
    if signal.account:
        wanted = signal.account.strip().lower()
        accounts = [a for a in accounts if a.label.lower() == wanted]
    if signal.broker:
        accounts = [a for a in accounts if a.broker == signal.broker]
    if signal.action in ("buy", "sell"):
        # `lots` sizes MT5 orders and `quantity` sizes Zerodha orders, so an
        # alert only goes to the kind of account it carries a size for.
        accounts = [
            a for a in accounts
            if (signal.lots if a.broker == "mt5" else signal.quantity) is not None
        ]
    if not accounts:
        return reject("no enabled account matches this alert")

    alert = Alert(user_id=user.id, raw=raw, status="accepted")
    db.add(alert)
    db.flush()
    orders = []
    for account in accounts:
        order = Order(
            alert_id=alert.id,
            user_id=user.id,
            account_id=account.id,
            action=signal.action,
            symbol=signal.symbol,
            reverse=signal.reverse,
            comment=signal.comment,
        )
        _prepare_order(order, account, signal)
        db.add(order)
        orders.append(order)
    db.commit()
    for order in orders:
        if order.account.broker == "kite" and order.status == "queued":
            background.add_task(run_kite_order, order.id)
    return {
        "alert_id": alert.id,
        "orders": [
            {"id": o.id, "account": o.account.label, "status": o.status, "message": o.message}
            for o in orders
        ],
    }


# --------------------------------------------------------------- worker API


def require_worker(x_worker_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_worker_key.encode(), settings.worker_key.encode()):
        raise HTTPException(status_code=401)


def _worker_account(db: Session, account_id: int, must_be_enabled: bool = True) -> Account:
    """Workers only ever see MT5 accounts."""
    account = db.get(Account, account_id)
    if not account or account.broker != "mt5" or (must_be_enabled and not account.enabled):
        raise HTTPException(status_code=404)
    return account


def _account_payload(account: Account) -> dict:
    return {
        "id": account.id,
        "login": account.login,
        "server": account.server,
        "password": decrypt(account.password_enc),
    }


@app.get("/worker/accounts", dependencies=[Depends(require_worker)])
def worker_accounts(db: Session = Depends(get_db)):
    """Ids of the accounts that should have a running terminal."""
    ids = db.scalars(
        select(Account.id).where(Account.enabled.is_(True), Account.broker == "mt5")
    ).all()
    return {"account_ids": list(ids)}


@app.get("/worker/accounts/{account_id}", dependencies=[Depends(require_worker)])
def worker_account(account_id: int, db: Session = Depends(get_db)):
    return _account_payload(_worker_account(db, account_id))


class StatusReport(BaseModel):
    status: str  # connected / error
    message: str = ""
    balance: float | None = None
    equity: float | None = None


@app.post("/worker/accounts/{account_id}/status", dependencies=[Depends(require_worker)])
def worker_status(account_id: int, report: StatusReport, db: Session = Depends(get_db)):
    account = _worker_account(db, account_id, must_be_enabled=False)
    if report.status not in ("connected", "error"):
        raise HTTPException(status_code=422, detail="bad status")
    account.status = report.status
    account.status_message = report.message[:255]
    account.balance = report.balance
    account.equity = report.equity
    account.last_seen = utcnow()
    db.commit()
    return {"ok": True}


@app.post("/worker/accounts/{account_id}/claim", dependencies=[Depends(require_worker)])
def worker_claim(account_id: int, db: Session = Depends(get_db)):
    """Hand queued orders to the worker. Each order is handed out at most once."""
    _worker_account(db, account_id)
    now = utcnow()
    # Orders that waited too long are stale signals: never trade them late.
    db.execute(
        update(Order)
        .where(
            Order.account_id == account_id,
            Order.status == "queued",
            Order.created_at < now - timedelta(seconds=settings.max_order_age_seconds),
        )
        .values(status="expired", message="not executed in time; signal was dropped", done_at=now)
    )
    db.execute(
        update(Order)
        .where(
            Order.account_id == account_id,
            Order.status == "claimed",
            Order.claimed_at < now - CLAIM_TIMEOUT,
        )
        .values(
            status="failed",
            message="no result reported by the terminal; check the account for an open trade",
            done_at=now,
        )
    )
    claimed = []
    queued = db.scalars(
        select(Order)
        .where(Order.account_id == account_id, Order.status == "queued")
        .order_by(Order.id)
        .limit(20)
    ).all()
    for order in queued:
        won = db.execute(
            update(Order)
            .where(Order.id == order.id, Order.status == "queued")
            .values(status="claimed", claimed_at=now)
        )
        if won.rowcount == 1:
            claimed.append(
                {
                    "id": order.id,
                    "action": order.action,
                    "symbol": order.symbol,
                    "lots": order.lots,
                    "sl": order.sl,
                    "tp": order.tp,
                    "reverse": order.reverse,
                    "comment": order.comment,
                }
            )
    db.commit()
    return {"orders": claimed}


class OrderResult(BaseModel):
    ok: bool
    message: str = ""
    ticket: str | None = None
    price: float | None = None


@app.post("/worker/orders/{order_id}/result", dependencies=[Depends(require_worker)])
def worker_result(order_id: int, result: OrderResult, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404)
    if order.status != "claimed":
        raise HTTPException(status_code=409, detail=f"order is {order.status}")
    order.status = "filled" if result.ok else "failed"
    order.message = result.message[:500]
    order.ticket = result.ticket
    order.price = result.price
    order.done_at = utcnow()
    db.commit()
    return {"ok": True}
