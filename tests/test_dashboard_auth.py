"""Authentication and subscription controls fail closed and remain outside trading authority."""

from datetime import UTC, datetime, timedelta

import pytest

from app.dashboard.auth import AuthError, AuthStore


def test_first_user_is_single_master_admin_and_password_is_not_stored(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    assert store.setup_required() is True
    assert store.bootstrap_admin("Boss@Example.Test", "CorrectHorse123") == {
        "status": "MASTER_ADMIN_CREATED",
        "email": "boss@example.test",
    }
    assert store.setup_required() is False
    with pytest.raises(AuthError, match="already complete"):
        store.bootstrap_admin("other@example.test", "CorrectHorse123")
    assert b"CorrectHorse123" not in (tmp_path / "auth.db").read_bytes()


def test_login_is_generic_expiring_and_revocable(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    store.bootstrap_admin("admin@example.test", "CorrectHorse123")
    with pytest.raises(AuthError, match="incorrect"):
        store.create_session("missing@example.test", "CorrectHorse123")
    with pytest.raises(AuthError, match="incorrect"):
        store.create_session("admin@example.test", "WrongPassword123")
    token, user = store.create_session("admin@example.test", "CorrectHorse123")
    assert user["role"] == "MASTER_ADMIN" and user["entitled"] is True
    assert token.encode() not in (tmp_path / "auth.db").read_bytes()
    store.logout(token)
    assert store.authenticate(token) is None


def test_admin_manages_plans_users_subscriptions_and_bounded_promo(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    store.bootstrap_admin("admin@example.test", "CorrectHorse123")
    store.create_user("user@example.test", "AnotherPassword123")
    store.save_plan(
        {
            "code": "pro",
            "name": "Professional",
            "price_cents": 4900,
            "billing_period": "MONTH",
            "active": True,
        }
    )
    snapshot = store.admin_snapshot()
    user = next(item for item in snapshot["users"] if item["email"] == "user@example.test")
    ends = (datetime.now(UTC) + timedelta(days=30)).isoformat().replace("+00:00", "Z")
    store.assign_subscription(
        {"user_id": user["id"], "plan_code": "PRO", "status": "ACTIVE", "ends_at": ends}
    )
    store.save_promo(
        {
            "code": "welcome30",
            "percent_off": 30,
            "bonus_days": 7,
            "max_redemptions": 1,
            "expires_at": (datetime.now(UTC) + timedelta(days=1))
            .isoformat()
            .replace("+00:00", "Z"),
            "active": True,
        }
    )
    _, signed_in = store.create_session("user@example.test", "AnotherPassword123")
    result = store.redeem(signed_in["id"], "WELCOME30")
    assert result["percent_off"] == 30 and result["bonus_days"] == 7
    with pytest.raises(AuthError, match="already used|limit"):
        store.redeem(signed_in["id"], "WELCOME30")
    updated = next(
        item for item in store.admin_snapshot()["users"] if item["email"] == "user@example.test"
    )
    assert updated["plan_code"] == "PRO" and updated["status"] == "ACTIVE"


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("bad", "CorrectHorse123"),
        ("user@example.test", "short"),
        ("user@example.test", "onlyletterslong"),
        ("user@example.test", "1234567890123"),
    ],
)
def test_account_input_validation(email, password, tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    with pytest.raises(AuthError):
        store.bootstrap_admin(email, password)
