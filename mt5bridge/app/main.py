import hmac
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import Base, engine, get_db
from .models import Alert, MT5Account, Order, User, utcnow
from .security import decrypt, encrypt, hash_password, new_token, verify_password
from .signals import parse_signal

MAX_ALERT_BYTES = 4096
# A claimed order with no result after this long is marked failed, never re-run.
CLAIM_TIMEOUT = timedelta(minutes=5)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Base.metadata.create_all(engine)

app = FastAPI(title="MT5 Bridge", docs_url=None, redoc_url=None)
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


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return _redirect("/login")
    accounts = db.scalars(
        select(MT5Account).where(MT5Account.user_id == user.id).order_by(MT5Account.id)
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
        select(MT5Account).where(MT5Account.user_id == user.id, MT5Account.label == label)
    ):
        error = "You already have an account with that name."
    if error:
        _flash(request, error)
        return _redirect("/")
    db.add(
        MT5Account(
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


def _own_account(request: Request, db: Session, account_id: int) -> MT5Account:
    user = current_user(request, db)
    account = db.get(MT5Account, account_id)
    if not user or not account or account.user_id != user.id:
        raise HTTPException(status_code=404)
    return account


@app.post("/accounts/{account_id}/toggle")
def toggle_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = _own_account(request, db, account_id)
    account.enabled = not account.enabled
    if not account.enabled:
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


# ------------------------------------------------------------------ webhook


@app.post("/hook/{token}")
async def webhook(token: str, request: Request, db: Session = Depends(get_db)):
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
        select(MT5Account).where(MT5Account.user_id == user.id, MT5Account.enabled.is_(True))
    ).all()
    if signal.account:
        wanted = signal.account.strip().lower()
        accounts = [a for a in accounts if a.label.lower() == wanted]
    if not accounts:
        return reject("no enabled MT5 account matches this alert")

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
            symbol=signal.symbol + account.symbol_suffix,
            lots=signal.lots,
            sl=signal.sl,
            tp=signal.tp,
            reverse=signal.reverse,
            comment=signal.comment,
        )
        if signal.lots is not None and signal.lots > account.max_lots:
            order.status = "rejected"
            order.message = f"{signal.lots} lots is above this account's limit of {account.max_lots}"
            order.done_at = utcnow()
        db.add(order)
        orders.append(order)
    db.commit()
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


def _account_payload(account: MT5Account) -> dict:
    return {
        "id": account.id,
        "login": account.login,
        "server": account.server,
        "password": decrypt(account.password_enc),
    }


@app.get("/worker/accounts", dependencies=[Depends(require_worker)])
def worker_accounts(db: Session = Depends(get_db)):
    """Ids of the accounts that should have a running terminal."""
    ids = db.scalars(select(MT5Account.id).where(MT5Account.enabled.is_(True))).all()
    return {"account_ids": list(ids)}


@app.get("/worker/accounts/{account_id}", dependencies=[Depends(require_worker)])
def worker_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(MT5Account, account_id)
    if not account or not account.enabled:
        raise HTTPException(status_code=404)
    return _account_payload(account)


class StatusReport(BaseModel):
    status: str  # connected / error
    message: str = ""
    balance: float | None = None
    equity: float | None = None


@app.post("/worker/accounts/{account_id}/status", dependencies=[Depends(require_worker)])
def worker_status(account_id: int, report: StatusReport, db: Session = Depends(get_db)):
    account = db.get(MT5Account, account_id)
    if not account:
        raise HTTPException(status_code=404)
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
    account = db.get(MT5Account, account_id)
    if not account or not account.enabled:
        raise HTTPException(status_code=404)
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
