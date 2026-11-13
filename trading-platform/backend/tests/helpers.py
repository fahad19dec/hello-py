"""Shared test helpers: a browser-like client and shortcuts for common flows."""

import re
import uuid

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import SYSTEM, access, new_session
from app.core.email import get_email_sender
from app.main import app
from app.modules.auth import totp

PASSWORD = "correct horse battery staple"
OTHER_PASSWORD = "another-long-passphrase-42"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
OWNER_ENGINE = None  # set by conftest

# A fixed instant for authenticator codes, so tests never straddle a 30-second step.
TOTP_NOW = 1_800_000_015.0


def pin_totp_clock(monkeypatch, now: float = TOTP_NOW) -> None:
    monkeypatch.setattr(totp, "_clock", lambda: now)


def totp_code(secret: str, step_offset: int = 0, now: float = TOTP_NOW) -> str:
    """The authenticator code for the pinned time, or a neighbouring 30-second step."""
    return pyotp.TOTP(secret).at(int(now) + step_offset * totp.STEP_SECONDS)


class Browser:
    """Behaves like one browser: keeps cookies and sends the CSRF token on writes."""

    def __init__(self, application=app):
        self.http = TestClient(application)
        self.csrf: str | None = None

    def request(self, method: str, path: str, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        if self.csrf and method in UNSAFE:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return self.http.request(method, "/api/v1" + path, headers=headers, **kwargs)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    @property
    def session_cookie(self) -> str | None:
        return self.http.cookies.get("tp_session")

    def login(self, email: str, password: str = PASSWORD):
        response = self.post("/auth/login", json={"email": email, "password": password})
        if response.status_code == 200:
            self.csrf = response.json()["csrf_token"]
        return response

    def second_factor(self, **body):
        response = self.post("/auth/mfa/verify", json=body)
        if response.status_code == 200:
            self.csrf = response.json()["csrf_token"]
        return response


def outbox():
    return get_email_sender().outbox


def link_token(email: str, page: str) -> str:
    """The token from the most recent emailed link to `page` ("verify-email" or "reset-password")."""
    for message in reversed(outbox()):
        if message.to == email:
            match = re.search(rf"/{page}\?token=([\w-]+)", message.body)
            if match:
                return match.group(1)
    raise AssertionError(f"no {page} email was sent to {email}")


def register(browser: Browser, email: str, password: str = PASSWORD):
    return browser.post("/auth/register", json={"email": email, "password": password})


def create_user(email: str, password: str = PASSWORD) -> None:
    """Register and confirm an account (not signed in)."""
    visitor = Browser()
    assert register(visitor, email, password).status_code == 202
    response = visitor.post("/auth/verify-email", json={"token": link_token(email, "verify-email")})
    assert response.status_code == 200, response.text


def signed_in(email: str, password: str = PASSWORD) -> Browser:
    """A browser signed in as a new, confirmed user."""
    create_user(email, password)
    browser = Browser()
    response = browser.login(email, password)
    assert response.status_code == 200, response.text
    return browser


def enable_mfa(browser: Browser, password: str = PASSWORD) -> tuple[str, list[str]]:
    """Turn on two-factor. Uses the previous time step, leaving the current and next codes unused."""
    setup = browser.post("/auth/mfa/setup", json={"password": password})
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    enabled = browser.post("/auth/mfa/enable", json={"code": totp_code(secret, -1)})
    assert enabled.status_code == 200, enabled.text
    return secret, enabled.json()["recovery_codes"]


def user_id(email: str) -> uuid.UUID:
    return system_scalar("SELECT id FROM users WHERE email = :email", email=email)


def system_scalar(sql: str, **params):
    """Run a query as the application role in the trusted system context."""
    db = new_session()
    try:
        with access(db, SYSTEM):
            value = db.execute(text(sql), params).scalar()
            db.commit()
            return value
    finally:
        db.close()


def system_rows(sql: str, **params) -> list:
    db = new_session()
    try:
        with access(db, SYSTEM):
            rows = db.execute(text(sql), params).all()
            db.commit()
            return rows
    finally:
        db.close()


def owner_execute(sql: str, **params) -> None:
    """Run a statement as the schema owner (used to simulate the passage of time)."""
    with OWNER_ENGINE.begin() as conn:
        conn.execute(text("SELECT set_config('app.access_mode', 'system', true)"))
        conn.execute(text(sql), params)


def staff_member(email: str, role: str) -> Browser:
    """A staff account signed in with two-factor, as the admin area requires."""
    create_user(email)
    system_scalar("UPDATE users SET role = :role WHERE email = :email RETURNING id", role=role, email=email)
    browser = Browser()
    assert browser.login(email).status_code == 200
    enable_mfa(browser)
    return browser
