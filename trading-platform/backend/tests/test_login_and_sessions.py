from fastapi import Response

from app.core.config import Settings, get_settings
from app.modules.auth.router import set_session_cookie
from tests.helpers import (
    PASSWORD,
    Browser,
    create_user,
    owner_execute,
    signed_in,
    system_scalar,
    user_id,
)
from tests.test_config_and_crypto import BASE

EMAIL = "trader@example.com"


def test_wrong_password_and_unknown_address_give_the_same_answer():
    create_user(EMAIL)
    wrong = Browser().login(EMAIL, "definitely-not-the-password")
    unknown = Browser().login("nobody@example.com", "definitely-not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    assert "set-cookie" not in wrong.headers


def test_sign_in_is_blocked_after_repeated_failures_for_known_and_unknown_addresses_alike():
    create_user(EMAIL)
    for email in (EMAIL, "nobody@example.com"):
        for _ in range(5):
            assert Browser().login(email, "wrong-password-000").status_code == 401
        blocked = Browser().login(email, "wrong-password-000")
        assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "too_many_requests"
    # Even the right password is refused until the window passes.
    assert Browser().login(EMAIL, PASSWORD).status_code == 429


def test_a_successful_sign_in_clears_the_failure_count():
    create_user(EMAIL)
    for _ in range(4):
        Browser().login(EMAIL, "wrong-password-000")
    assert Browser().login(EMAIL).status_code == 200
    for _ in range(4):
        assert Browser().login(EMAIL, "wrong-password-000").status_code == 401
    assert Browser().login(EMAIL).status_code == 200


def test_session_cookie_is_httponly_and_only_a_hash_is_stored():
    create_user(EMAIL)
    browser = Browser()
    response = browser.login(EMAIL)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    stored = system_scalar("SELECT token_hash FROM sessions")
    assert browser.session_cookie and browser.session_cookie != stored and len(stored) == 64
    assert browser.session_cookie not in response.text  # the token travels only in the cookie


def test_production_cookie_is_secure_and_host_only():
    settings = Settings(
        _env_file=None,
        **{
            **BASE,
            "environment": "production",
            "cookie_secure": True,
            "email_backend": "smtp",
            "rate_limit_backend": "redis",
            "frontend_origin": "https://app.example.com",
        },
    )
    response = Response()
    set_session_cookie(response, "token-value", settings)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-tp_session=token-value")
    assert "Secure" in cookie and "HttpOnly" in cookie and "Domain" not in cookie


def test_protected_endpoints_need_a_session(browser):
    assert browser.get("/auth/me").status_code == 401
    assert browser.get("/auth/sessions").status_code == 401
    forged = Browser()
    forged.http.cookies.set("tp_session", "A" * 43)
    assert forged.get("/auth/me").status_code == 401


def test_logout_ends_the_session_on_the_server():
    browser = signed_in(EMAIL)
    token = browser.session_cookie
    assert browser.post("/auth/logout").status_code == 204
    assert browser.session_cookie is None
    # Replaying the old cookie does not work: the session is gone, not just the cookie.
    replay = Browser()
    replay.http.cookies.set("tp_session", token)
    assert replay.get("/auth/me").status_code == 401


def test_writes_require_the_session_csrf_token():
    browser = signed_in(EMAIL)
    csrf, browser.csrf = browser.csrf, None
    for headers in ({}, {"X-CSRF-Token": "0" * 64}):
        refused = browser.patch("/users/me", json={"display_name": "Mallory"}, headers=headers)
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "csrf_failed"
    # Another session's token does not work either.
    other = signed_in("other@example.com")
    assert (
        browser.patch("/users/me", json={"display_name": "M"}, headers={"X-CSRF-Token": other.csrf}).status_code == 403
    )
    assert browser.get("/users/me").json()["display_name"] == ""
    browser.csrf = csrf
    assert browser.patch("/users/me", json={"display_name": "Asha"}).status_code == 200
    assert browser.get("/auth/me").json()["csrf_token"] == csrf


def test_cross_site_writes_are_refused_by_origin():
    create_user(EMAIL)
    credentials = {"email": EMAIL, "password": PASSWORD}
    evil = Browser().post("/auth/login", json=credentials, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "origin_not_allowed"
    via_referer = Browser().post("/auth/login", json=credentials, headers={"Referer": "https://evil.example/page"})
    assert via_referer.status_code == 403
    assert (
        Browser().post("/auth/login", json=credentials, headers={"Origin": "http://localhost:3000"}).status_code == 200
    )
    # Reads are not affected.
    assert Browser().get("/system/status", headers={"Origin": "https://evil.example"}).status_code == 200


def test_sessions_can_be_listed_and_ended_individually_or_all_at_once():
    laptop = signed_in(EMAIL)
    phone, tablet = Browser(), Browser()
    assert phone.login(EMAIL).status_code == 200 and tablet.login(EMAIL).status_code == 200

    sessions = laptop.get("/auth/sessions").json()
    assert len(sessions) == 3 and [s["current"] for s in sessions].count(True) == 1
    phone_id = phone.get("/auth/me").json()["session_id"]

    assert laptop.delete(f"/auth/sessions/{phone_id}").status_code == 204
    assert phone.get("/auth/me").status_code == 401
    assert tablet.get("/auth/me").status_code == 200

    assert laptop.post("/auth/sessions/revoke-others").json() == {"count": 1}
    assert tablet.get("/auth/me").status_code == 401
    assert laptop.get("/auth/me").status_code == 200
    assert len(laptop.get("/auth/sessions").json()) == 1


def test_sessions_expire_when_idle_and_at_the_absolute_limit():
    idle = signed_in(EMAIL)
    owner_execute(
        "UPDATE sessions SET last_seen_at = now() - make_interval(mins => :m)",
        m=get_settings().session_idle_minutes + 1,
    )
    assert idle.get("/auth/me").status_code == 401
    assert system_scalar("SELECT revoked_reason FROM sessions") == "expired"

    fresh = Browser()
    assert fresh.login(EMAIL).status_code == 200
    owner_execute("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE revoked_at IS NULL")
    assert fresh.get("/auth/me").status_code == 401


def test_signing_in_again_replaces_the_previous_session_in_that_browser():
    browser = signed_in(EMAIL)
    first = browser.session_cookie
    assert browser.login(EMAIL).status_code == 200
    assert browser.session_cookie != first
    replay = Browser()
    replay.http.cookies.set("tp_session", first)
    assert replay.get("/auth/me").status_code == 401


def test_profile_update_validates_and_is_recorded():
    browser = signed_in(EMAIL)
    assert browser.patch("/users/me", json={"timezone": "Mars/Olympus"}).status_code == 422
    updated = browser.patch("/users/me", json={"display_name": "  Asha   Rao ", "timezone": "Asia/Kolkata"})
    assert updated.status_code == 200
    assert (updated.json()["display_name"], updated.json()["timezone"]) == ("Asha Rao", "Asia/Kolkata")
    # Role and status are not part of the profile and cannot be set through it.
    browser.patch("/users/me", json={"role": "super_admin", "status": "suspended", "display_name": "A"})
    assert system_scalar("SELECT role FROM users WHERE id = :id", id=user_id(EMAIL)) == "user"
    actions = [a["action"] for a in browser.get("/users/me/activity").json()]
    assert actions.count("user.profile.updated") == 2 and "auth.login.succeeded" in actions


def test_failed_sign_ins_show_up_in_the_account_activity():
    browser = signed_in(EMAIL)
    Browser().login(EMAIL, "wrong-password-000")
    newest = browser.get("/users/me/activity").json()[0]
    assert newest["action"] == "auth.login.failed" and newest["actor"] == "system"
    assert newest["detail"] == {"reason": "wrong_password"}
