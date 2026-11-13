from tests.helpers import OTHER_PASSWORD, PASSWORD, Browser, link_token, outbox, owner_execute, register, system_scalar

EMAIL = "trader@example.com"


def test_register_confirm_and_sign_in(browser):
    response = register(browser, EMAIL)
    assert response.status_code == 202
    assert browser.session_cookie is None  # registering does not sign anyone in

    # The password is right but the address is unconfirmed.
    blocked = browser.login(EMAIL)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "email_not_verified"

    assert browser.post("/auth/verify-email", json={"token": link_token(EMAIL, "verify-email")}).status_code == 200
    signed_in = browser.login(EMAIL)
    assert signed_in.status_code == 200
    body = signed_in.json()
    assert body["mfa_required"] is False and body["user"]["email"] == EMAIL and body["user"]["role"] == "user"

    me = browser.get("/auth/me").json()
    assert me["user"]["email_verified"] is True and me["second_factor_used"] is False


def test_email_is_case_insensitive_and_stored_once(browser):
    register(browser, "Trader@Example.com")
    browser.post("/auth/verify-email", json={"token": link_token("trader@example.com", "verify-email")})
    assert browser.login("TRADER@example.COM").status_code == 200
    assert system_scalar("SELECT count(*) FROM users") == 1


def test_registering_an_existing_address_looks_identical_and_changes_nothing(browser):
    first = register(browser, EMAIL)
    browser.post("/auth/verify-email", json={"token": link_token(EMAIL, "verify-email")})
    outbox().clear()

    second = register(Browser(), EMAIL, OTHER_PASSWORD)
    assert (second.status_code, second.json()) == (first.status_code, first.json())
    assert system_scalar("SELECT count(*) FROM users") == 1
    # The owner is told; the stranger learns nothing and the password is unchanged.
    assert [m.subject for m in outbox()] == ["You already have a TradeBridge account"]
    assert Browser().login(EMAIL, OTHER_PASSWORD).status_code == 401
    assert Browser().login(EMAIL, PASSWORD).status_code == 200


def test_reregistering_an_unconfirmed_address_replaces_the_pending_password_and_old_link(browser):
    register(browser, EMAIL, PASSWORD)
    first_link = link_token(EMAIL, "verify-email")
    register(browser, EMAIL, OTHER_PASSWORD)
    second_link = link_token(EMAIL, "verify-email")
    assert first_link != second_link

    stale = browser.post("/auth/verify-email", json={"token": first_link})
    assert stale.status_code == 400 and stale.json()["error"]["code"] == "invalid_token"
    assert browser.post("/auth/verify-email", json={"token": second_link}).status_code == 200
    assert Browser().login(EMAIL, PASSWORD).status_code == 401
    assert Browser().login(EMAIL, OTHER_PASSWORD).status_code == 200


def test_confirmation_links_work_once_and_expire(browser):
    register(browser, EMAIL)
    token = link_token(EMAIL, "verify-email")
    assert browser.post("/auth/verify-email", json={"token": token}).status_code == 200
    assert browser.post("/auth/verify-email", json={"token": token}).status_code == 400

    register(browser, "late@example.com")
    late = link_token("late@example.com", "verify-email")
    owner_execute("UPDATE email_tokens SET expires_at = now() - interval '1 minute' WHERE used_at IS NULL")
    assert browser.post("/auth/verify-email", json={"token": late}).status_code == 400
    assert browser.post("/auth/verify-email", json={"token": "x" * 43}).status_code == 400


def test_a_reset_link_cannot_be_used_to_confirm_an_email(browser):
    register(browser, EMAIL)
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    reset = link_token(EMAIL, "reset-password")
    assert browser.post("/auth/verify-email", json={"token": reset}).status_code == 400


def test_resend_confirmation_is_generic_and_only_mails_unconfirmed_accounts(browser):
    register(browser, EMAIL)
    outbox().clear()
    known = browser.post("/auth/resend-verification", json={"email": EMAIL})
    unknown = browser.post("/auth/resend-verification", json={"email": "nobody@example.com"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json()) == (202, known.json())
    assert [m.to for m in outbox()] == [EMAIL]
    browser.post("/auth/verify-email", json={"token": link_token(EMAIL, "verify-email")})
    outbox().clear()
    browser.post("/auth/resend-verification", json={"email": EMAIL})
    assert outbox() == []


def test_weak_passwords_are_refused_with_a_reason(browser):
    cases = {
        "short1": "at least 10 characters",
        "password123": "too common",
        "aaaaaaaaaaaa": "repeated character",
        "trader-is-my-name-1": "must not contain your email",
        "I love TradeBridge 1": "name of this service",
        "x" * 129: "at most 128 characters",
    }
    for password, reason in cases.items():
        response = register(browser, EMAIL, password)
        assert response.status_code == 422, password
        assert response.json()["error"]["code"] == "weak_password"
        assert reason in response.json()["error"]["message"], password
    assert system_scalar("SELECT count(*) FROM users") == 0


def test_invalid_input_is_rejected_without_echoing_what_was_sent(browser):
    response = browser.post("/auth/register", json={"email": "not-an-email", "password": "hunter2-hunter2"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"
    assert "hunter2" not in response.text and "not-an-email" not in response.text
    assert browser.post("/auth/register", json={"email": EMAIL}).status_code == 422


def test_registration_is_rate_limited_per_address(browser):
    # Refused passwords do not count against the allowance.
    for _ in range(8):
        assert register(browser, EMAIL, "short").status_code == 422
    for _ in range(5):
        assert register(browser, EMAIL).status_code == 202
    limited = register(browser, EMAIL)
    assert limited.status_code == 429 and "Retry-After" in limited.headers
