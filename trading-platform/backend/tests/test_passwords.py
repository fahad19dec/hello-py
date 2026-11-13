from tests.helpers import (
    OTHER_PASSWORD,
    PASSWORD,
    Browser,
    create_user,
    enable_mfa,
    link_token,
    outbox,
    owner_execute,
    register,
    signed_in,
    totp_code,
)

EMAIL = "trader@example.com"


def change(browser, current, new):
    return browser.post("/auth/password/change", json={"current_password": current, "new_password": new})


def test_changing_the_password_signs_out_other_browsers_and_notifies_the_owner():
    laptop = signed_in(EMAIL)
    phone = Browser()
    phone.login(EMAIL)
    outbox().clear()

    assert change(laptop, PASSWORD, OTHER_PASSWORD).json() == {"count": 1}
    assert laptop.get("/auth/me").status_code == 200
    assert phone.get("/auth/me").status_code == 401
    assert Browser().login(EMAIL, PASSWORD).status_code == 401
    assert Browser().login(EMAIL, OTHER_PASSWORD).status_code == 200
    assert "password for your account was changed" in outbox()[-1].body


def test_change_needs_the_current_password_and_a_different_acceptable_new_one():
    browser = signed_in(EMAIL)
    assert change(browser, "wrong-password-1", OTHER_PASSWORD).json()["error"]["code"] == "wrong_password"
    assert change(browser, PASSWORD, PASSWORD).json()["error"]["code"] == "weak_password"
    assert change(browser, PASSWORD, "short").json()["error"]["code"] == "weak_password"
    assert Browser().login(EMAIL, PASSWORD).status_code == 200


def test_guessing_the_current_password_inside_a_session_is_throttled():
    browser = signed_in(EMAIL)
    for _ in range(5):
        assert change(browser, "wrong-password-1", OTHER_PASSWORD).status_code == 403
    assert change(browser, "wrong-password-1", OTHER_PASSWORD).status_code == 429
    assert change(browser, PASSWORD, OTHER_PASSWORD).status_code == 429


def test_forgot_password_answers_the_same_for_known_and_unknown_addresses(browser):
    create_user(EMAIL)
    outbox().clear()
    known = browser.post("/auth/password/forgot", json={"email": EMAIL})
    unknown = browser.post("/auth/password/forgot", json={"email": "nobody@example.com"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert known.status_code == 202
    assert [m.to for m in outbox()] == [EMAIL]


def test_reset_sets_the_password_once_and_signs_out_every_browser(browser):
    laptop = signed_in(EMAIL)
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    token = link_token(EMAIL, "reset-password")

    weak = browser.post("/auth/password/reset", json={"token": token, "new_password": "short"})
    assert weak.status_code == 422  # a refused password does not burn the link
    done = browser.post("/auth/password/reset", json={"token": token, "new_password": OTHER_PASSWORD})
    assert done.status_code == 200
    assert browser.session_cookie is None  # resetting does not sign anyone in
    assert laptop.get("/auth/me").status_code == 401
    assert Browser().login(EMAIL, PASSWORD).status_code == 401
    assert Browser().login(EMAIL, OTHER_PASSWORD).status_code == 200

    reuse = browser.post("/auth/password/reset", json={"token": token, "new_password": "yet-another-pass-9"})
    assert reuse.status_code == 400 and reuse.json()["error"]["code"] == "invalid_token"


def test_a_newer_reset_link_cancels_the_older_one_and_links_expire(browser):
    create_user(EMAIL)
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    first = link_token(EMAIL, "reset-password")
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    second = link_token(EMAIL, "reset-password")
    assert (
        browser.post("/auth/password/reset", json={"token": first, "new_password": OTHER_PASSWORD}).status_code == 400
    )

    owner_execute("UPDATE email_tokens SET expires_at = now() - interval '1 minute' WHERE used_at IS NULL")
    assert (
        browser.post("/auth/password/reset", json={"token": second, "new_password": OTHER_PASSWORD}).status_code == 400
    )
    assert Browser().login(EMAIL, PASSWORD).status_code == 200


def test_changing_the_password_cancels_outstanding_reset_links(browser):
    laptop = signed_in(EMAIL)
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    token = link_token(EMAIL, "reset-password")
    assert change(laptop, PASSWORD, OTHER_PASSWORD).status_code == 200
    assert (
        browser.post("/auth/password/reset", json={"token": token, "new_password": "third-password-77"}).status_code
        == 400
    )


def test_reset_does_not_remove_two_factor(browser):
    secret, _ = enable_mfa(signed_in(EMAIL))
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    browser.post(
        "/auth/password/reset", json={"token": link_token(EMAIL, "reset-password"), "new_password": OTHER_PASSWORD}
    )
    visitor = Browser()
    assert visitor.login(EMAIL, OTHER_PASSWORD).json()["mfa_required"] is True
    assert visitor.get("/auth/me").status_code == 401
    assert visitor.second_factor(code=totp_code(secret)).status_code == 200


def test_reset_confirms_an_unconfirmed_address_because_the_link_proves_it(browser):
    register(browser, EMAIL)
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    browser.post(
        "/auth/password/reset", json={"token": link_token(EMAIL, "reset-password"), "new_password": OTHER_PASSWORD}
    )
    assert Browser().login(EMAIL, OTHER_PASSWORD).status_code == 200


def test_reset_clears_a_sign_in_lockout(browser):
    create_user(EMAIL)
    for _ in range(5):
        Browser().login(EMAIL, "wrong-password-000")
    assert Browser().login(EMAIL, PASSWORD).status_code == 429
    browser.post("/auth/password/forgot", json={"email": EMAIL})
    browser.post(
        "/auth/password/reset", json={"token": link_token(EMAIL, "reset-password"), "new_password": OTHER_PASSWORD}
    )
    assert Browser().login(EMAIL, OTHER_PASSWORD).status_code == 200
