from tests.helpers import PASSWORD, Browser, enable_mfa, outbox, signed_in, system_scalar, totp_code

EMAIL = "trader@example.com"


def test_setup_needs_the_password_and_a_working_code_before_it_takes_effect():
    browser = signed_in(EMAIL)
    refused = browser.post("/auth/mfa/setup", json={"password": "not-my-password-1"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "wrong_password"

    setup = browser.post("/auth/mfa/setup", json={"password": PASSWORD}).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/TradeBridge:trader%40example.com?")
    # Scanning the QR code is not enough: sign-in is unchanged until a code is confirmed.
    assert browser.post("/auth/mfa/enable", json={"code": "000000"}).status_code == 400
    assert Browser().login(EMAIL).json()["mfa_required"] is False

    enabled = browser.post("/auth/mfa/enable", json={"code": totp_code(setup["secret"])})
    assert enabled.status_code == 200 and len(set(enabled.json()["recovery_codes"])) == 10
    assert browser.get("/auth/mfa").json() == {"enabled": True, "recovery_codes_remaining": 10}
    assert browser.get("/auth/me").json()["second_factor_used"] is True
    assert outbox()[-1].subject.startswith("Security notice")


def test_the_secret_is_encrypted_at_rest_and_codes_are_stored_hashed():
    browser = signed_in(EMAIL)
    secret, codes = enable_mfa(browser)
    stored = system_scalar("SELECT totp_secret_enc FROM user_credentials")
    assert stored.startswith("enc:k1:") and secret not in stored
    hashes = system_scalar("SELECT string_agg(code_hash, ',') FROM recovery_codes")
    assert all(code not in hashes for code in codes)


def test_enabling_two_factor_signs_out_other_browsers():
    laptop = signed_in(EMAIL)
    phone = Browser()
    assert phone.login(EMAIL).status_code == 200
    enable_mfa(laptop)
    assert phone.get("/auth/me").status_code == 401
    assert laptop.get("/auth/me").status_code == 200


def test_sign_in_needs_the_second_factor_and_a_pending_session_can_do_nothing_else():
    secret, _ = enable_mfa(signed_in(EMAIL))
    browser = Browser()
    first_step = browser.login(EMAIL)
    assert first_step.status_code == 200
    assert first_step.json()["mfa_required"] is True and first_step.json()["user"] is None
    pending_cookie = browser.session_cookie

    for path in ("/auth/me", "/auth/sessions", "/users/me", "/users/me/activity", "/auth/mfa"):
        blocked = browser.get(path)
        assert blocked.status_code == 401 and blocked.json()["error"]["code"] == "mfa_required", path
    assert (
        browser.post("/auth/password/change", json={"current_password": PASSWORD, "new_password": "x" * 12}).status_code
        == 401
    )

    done = browser.second_factor(code=totp_code(secret))
    assert done.status_code == 200 and done.json()["user"]["email"] == EMAIL
    assert browser.get("/auth/me").json()["second_factor_used"] is True
    # The token is rotated when the session gains full access.
    assert browser.session_cookie != pending_cookie
    stale = Browser()
    stale.http.cookies.set("tp_session", pending_cookie)
    assert stale.get("/auth/me").status_code == 401


def test_a_code_cannot_be_used_twice():
    secret, _ = enable_mfa(signed_in(EMAIL))  # consumed the previous step's code
    first = Browser()
    first.login(EMAIL)
    assert first.second_factor(code=totp_code(secret)).status_code == 200
    replay = Browser()
    replay.login(EMAIL)
    assert replay.second_factor(code=totp_code(secret)).status_code == 401  # same code, observed in transit
    assert replay.second_factor(code=totp_code(secret, -1)).status_code == 401  # an older code
    assert replay.second_factor(code=totp_code(secret, +1)).status_code == 200  # the next one is fine


def test_wrong_codes_end_the_pending_session_after_five_tries():
    enable_mfa(signed_in(EMAIL))
    browser = Browser()
    browser.login(EMAIL)
    for _ in range(4):
        wrong = browser.second_factor(code="000000")
        assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "invalid_mfa_code"
    final = browser.second_factor(code="000000")
    assert final.json()["error"]["code"] == "mfa_attempts_exceeded"
    assert browser.second_factor(code="000000").json()["error"]["code"] == "not_authenticated"


def test_recovery_codes_work_once_and_the_owner_is_told():
    secret, codes = enable_mfa(signed_in(EMAIL))
    browser = Browser()
    browser.login(EMAIL)
    outbox().clear()
    # Typed in upper case and without dashes still works.
    assert browser.second_factor(recovery_code=codes[0].upper().replace("-", " ")).status_code == 200
    assert "recovery code was used" in outbox()[-1].body
    assert browser.get("/auth/mfa").json()["recovery_codes_remaining"] == 9

    again = Browser()
    again.login(EMAIL)
    assert again.second_factor(recovery_code=codes[0]).status_code == 401
    assert again.second_factor(recovery_code=codes[1]).status_code == 200


def test_the_request_must_carry_exactly_one_factor():
    enable_mfa(signed_in(EMAIL))
    browser = Browser()
    browser.login(EMAIL)
    assert browser.second_factor().status_code == 422
    assert browser.second_factor(code="123456", recovery_code="abcd-efgh-jkmn").status_code == 422


def test_turning_two_factor_off_needs_the_password_and_a_valid_code():
    browser = signed_in(EMAIL)
    secret, codes = enable_mfa(browser)
    assert (
        browser.post("/auth/mfa/disable", json={"password": "wrong-password-1", "code": totp_code(secret)}).status_code
        == 403
    )
    assert browser.post("/auth/mfa/disable", json={"password": PASSWORD, "code": "000000"}).status_code == 403
    assert browser.get("/auth/mfa").json()["enabled"] is True

    assert browser.post("/auth/mfa/disable", json={"password": PASSWORD, "code": totp_code(secret)}).status_code == 204
    assert browser.get("/auth/mfa").json() == {"enabled": False, "recovery_codes_remaining": 0}
    assert system_scalar("SELECT totp_secret_enc FROM user_credentials") is None
    assert Browser().login(EMAIL).json()["mfa_required"] is False
    assert "turned off" in outbox()[-1].body


def test_recovery_codes_can_be_regenerated_which_cancels_the_old_ones():
    browser = signed_in(EMAIL)
    secret, old_codes = enable_mfa(browser)
    refused = browser.post("/auth/mfa/recovery-codes", json={"password": PASSWORD, "code": "000000"})
    assert refused.status_code == 403
    fresh = browser.post("/auth/mfa/recovery-codes", json={"password": PASSWORD, "code": totp_code(secret)})
    assert fresh.status_code == 200
    new_codes = fresh.json()["recovery_codes"]
    assert len(new_codes) == 10 and not set(new_codes) & set(old_codes)

    visitor = Browser()
    visitor.login(EMAIL)
    assert visitor.second_factor(recovery_code=old_codes[0]).status_code == 401
    assert visitor.second_factor(recovery_code=new_codes[0]).status_code == 200


def test_setup_cannot_be_restarted_while_two_factor_is_on():
    browser = signed_in(EMAIL)
    enable_mfa(browser)
    again = browser.post("/auth/mfa/setup", json={"password": PASSWORD})
    assert again.status_code == 409 and again.json()["error"]["code"] == "mfa_already_enabled"
