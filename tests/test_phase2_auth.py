import pytest

from mizan.auth import AuthService, PermissionDenied, PermissionService


@pytest.fixture()
def auth(db):
    service = AuthService(db)
    service.seed_defaults()
    return service


def test_AUTH_login_and_wrong_password(auth):
    owner = auth.create_user("owner", "Owner", "correct horse", ["OWNER"])
    assert auth.login("owner", "correct horse")["id"] == owner
    with pytest.raises(ValueError):
        auth.login("owner", "wrong")


def test_password_is_hashed_not_plain(auth):
    auth.create_user("owner", "Owner", "secret-pass", ["OWNER"])
    with auth.db.connect() as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE username='owner'").fetchone()[0]
    assert "secret-pass" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_T_SEC_001_cashier_cannot_view_cost(auth):
    cashier = auth.create_user("cashier", "Cashier", "cashier-pass", ["CASHIER"])
    permissions = PermissionService(auth.db)
    assert not permissions.has(cashier, "view_purchase_cost")
    assert not permissions.has(cashier, "view_profit_reports")
    with pytest.raises(PermissionDenied):
        permissions.require(cashier, "view_purchase_cost")


def test_T_SEC_003_cashier_can_post_sale(auth):
    cashier = auth.create_user("cashier", "Cashier", "cashier-pass", ["CASHIER"])
    PermissionService(auth.db).require(cashier, "post_sales")


def test_T_SEC_005_disabled_user_cannot_login(auth):
    user = auth.create_user("cashier", "Cashier", "cashier-pass", ["CASHIER"])
    auth.disable_user(user, actor_id=user)
    with pytest.raises(ValueError):
        auth.login("cashier", "cashier-pass")


def test_default_deny_for_unknown_permission(auth):
    owner = auth.create_user("owner", "Owner", "pass", ["OWNER"])
    assert PermissionService(auth.db).has(owner, "not_a_real_permission") is False


def test_T_SEC_006_role_change_is_audited(auth):
    owner = auth.create_user("owner", "Owner", "pass", ["OWNER"])
    user = auth.create_user("manager", "Manager", "pass", ["CASHIER"])
    auth.assign_role(user, "MANAGER", actor_id=owner)
    with auth.db.connect() as conn:
        rows = conn.execute(
            "SELECT event_type FROM audit_events WHERE entity_type='user' AND entity_id=?",
            (user,),
        ).fetchall()
    assert any(r["event_type"] == "USER_ROLE_CHANGED" for r in rows)
