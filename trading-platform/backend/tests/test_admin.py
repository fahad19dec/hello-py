import uuid

from tests.helpers import (
    Browser,
    create_user,
    enable_mfa,
    outbox,
    signed_in,
    staff_member,
    system_rows,
    system_scalar,
    user_id,
)

USER = "trader@example.com"
SUPPORT = "support@example.com"
ROOT = "root@example.com"


def test_regular_users_cannot_tell_the_admin_api_exists():
    user = signed_in(USER)
    for method, path in (
        ("GET", "/admin/users"),
        ("GET", "/admin/audit"),
        ("POST", f"/admin/users/{uuid.uuid4()}/suspend"),
    ):
        response = user.request(method, path, json={"reason": "testing"})
        assert response.status_code == 404, path
    assert Browser().get("/admin/users").status_code == 401


def test_staff_must_sign_in_with_two_factor_to_use_the_admin_api():
    create_user(SUPPORT)
    system_scalar("UPDATE users SET role = 'support_admin' WHERE email = :e RETURNING id", e=SUPPORT)
    staff = Browser()
    staff.login(SUPPORT)
    refused = staff.get("/admin/users")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "staff_mfa_required"
    enable_mfa(staff)
    assert staff.get("/admin/users").status_code == 200


def test_listing_and_searching_users_never_exposes_credentials():
    signed_in(USER)
    create_user("another@example.com")
    staff = staff_member(SUPPORT, "support_admin")
    page = staff.get("/admin/users").json()
    assert page["total"] == 3 and {u["email"] for u in page["items"]} == {USER, "another@example.com", SUPPORT}
    assert set(page["items"][0]) == {
        "id",
        "email",
        "display_name",
        "role",
        "status",
        "suspended_reason",
        "mfa_enabled",
        "email_verified",
        "created_at",
        "last_login_at",
    }
    assert staff.get("/admin/users", params={"search": "anoth"}).json()["total"] == 1
    assert staff.get("/admin/users", params={"search": "%"}).json()["total"] == 0  # wildcards are literal
    assert staff.get("/admin/users", params={"role": "support_admin"}).json()["total"] == 1


def test_viewing_a_customer_is_itself_audited():
    signed_in(USER)
    staff = staff_member(SUPPORT, "support_admin")
    target = user_id(USER)
    detail = staff.get(f"/admin/users/{target}").json()
    assert detail["user"]["email"] == USER and len(detail["sessions"]) == 1
    assert "token_hash" not in str(detail)
    row = system_rows("SELECT actor_user_id, user_id FROM audit_logs WHERE action = 'admin.user.viewed'")[0]
    assert row == (user_id(SUPPORT), target)


def test_suspending_ends_sessions_blocks_sign_in_and_can_be_undone():
    customer = signed_in(USER)
    staff = staff_member(SUPPORT, "support_admin")
    target = user_id(USER)
    outbox().clear()

    assert staff.post(f"/admin/users/{target}/suspend", json={"reason": ""}).status_code == 422
    suspended = staff.post(f"/admin/users/{target}/suspend", json={"reason": "Chargeback under review"})
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    assert customer.get("/auth/me").status_code == 401
    blocked = Browser().login(USER)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "account_suspended"
    assert "suspended" in outbox()[-1].body
    assert staff.post(f"/admin/users/{target}/suspend", json={"reason": "again"}).status_code == 409

    assert staff.post(f"/admin/users/{target}/unsuspend").json()["status"] == "active"
    assert Browser().login(USER).status_code == 200

    audit = staff.get("/admin/audit", params={"user_id": str(target), "action": "admin.user."}).json()
    assert [a["action"] for a in audit] == ["admin.user.unsuspended", "admin.user.suspended"]
    assert audit[1]["actor_user_id"] == str(user_id(SUPPORT)) and audit[1]["actor_role"] == "support_admin"
    assert audit[1]["detail"] == {"reason": "Chargeback under review", "sessions_ended": 1}


def test_a_suspended_user_with_the_wrong_password_learns_nothing():
    create_user(USER)
    staff = staff_member(SUPPORT, "support_admin")
    staff.post(f"/admin/users/{user_id(USER)}/suspend", json={"reason": "review"})
    assert Browser().login(USER, "wrong-password-000").json()["error"]["code"] == "invalid_credentials"


def test_support_admins_cannot_change_roles_or_touch_other_staff():
    create_user(USER)
    support = staff_member(SUPPORT, "support_admin")
    staff_member(ROOT, "super_admin")
    # Role changes are a super-admin route; to support it looks absent.
    assert support.put(f"/admin/users/{user_id(USER)}/role", json={"role": "support_admin"}).status_code == 404
    for target in (ROOT, "second-support@example.com"):
        if target != ROOT:
            staff_member(target, "support_admin")
        refused = support.post(f"/admin/users/{user_id(target)}/suspend", json={"reason": "nope"})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "insufficient_role"
        assert support.post(f"/admin/users/{user_id(target)}/revoke-sessions").status_code == 403
    assert system_scalar("SELECT count(*) FROM users WHERE status = 'suspended'") == 0


def test_super_admin_can_change_roles_which_forces_a_fresh_sign_in():
    customer = signed_in(USER)
    root = staff_member(ROOT, "super_admin")
    target = user_id(USER)
    promoted = root.put(f"/admin/users/{target}/role", json={"role": "support_admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "support_admin"
    assert customer.get("/auth/me").status_code == 401  # old session carried the old role
    assert root.put(f"/admin/users/{target}/role", json={"role": "emperor"}).status_code == 422
    entry = root.get("/admin/audit", params={"action": "admin.user.role_changed"}).json()[0]
    assert entry["detail"] == {"from": "user", "to": "support_admin", "sessions_ended": 1}


def test_staff_cannot_change_their_own_role_or_status():
    root = staff_member(ROOT, "super_admin")
    me = user_id(ROOT)
    for response in (
        root.put(f"/admin/users/{me}/role", json={"role": "user"}),
        root.post(f"/admin/users/{me}/suspend", json={"reason": "oops"}),
        root.post(f"/admin/users/{me}/revoke-sessions"),
    ):
        assert response.status_code == 403 and response.json()["error"]["code"] == "cannot_modify_self"
    assert root.get("/admin/users").status_code == 200


def test_staff_can_sign_a_user_out_everywhere():
    customer = signed_in(USER)
    second = Browser()
    second.login(USER)
    staff = staff_member(SUPPORT, "support_admin")
    assert staff.post(f"/admin/users/{user_id(USER)}/revoke-sessions").json() == {"count": 2}
    assert customer.get("/auth/me").status_code == 401 and second.get("/auth/me").status_code == 401


def test_users_see_staff_actions_on_their_account_without_staff_identities():
    customer = signed_in(USER)
    staff = staff_member(SUPPORT, "support_admin")
    staff.get(f"/admin/users/{user_id(USER)}")
    entry = customer.get("/users/me/activity").json()[0]
    assert entry["action"] == "admin.user.viewed" and entry["actor"] == "staff"
    assert entry["ip"] is None and entry["user_agent"] is None
    assert str(user_id(SUPPORT)) not in str(entry)


def test_unknown_user_ids_are_not_found():
    staff = staff_member(SUPPORT, "support_admin")
    assert staff.get(f"/admin/users/{uuid.uuid4()}").status_code == 404
    assert staff.post(f"/admin/users/{uuid.uuid4()}/suspend", json={"reason": "ghost"}).status_code == 404
    assert staff.get("/admin/users/not-a-uuid").status_code == 422
