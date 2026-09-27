"""Local authentication, entitlements, and master-admin storage for RevMind."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class AuthError(ValueError):
    """A safe authentication or account-management error."""


_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,31}$")
SESSION_HOURS = 12


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _email(value: object) -> str:
    if not isinstance(value, str):
        raise AuthError("Enter a valid email address.")
    result = value.strip().lower()
    if len(result) > 254 or not _EMAIL.fullmatch(result):
        raise AuthError("Enter a valid email address.")
    return result


def _password(value: object) -> str:
    if not isinstance(value, str) or not 12 <= len(value) <= 512:
        raise AuthError("Password must contain 12 to 512 characters.")
    if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise AuthError("Password must include at least one letter and one number.")
    return value


def _hash_password(password: str, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=actual_salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt$16384$8$1${actual_salt.hex()}${derived.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
    except (ValueError, TypeError):
        return False


class AuthStore:
    """SQLite-backed local identity and commercial entitlement boundary."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS users(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('USER','MASTER_ADMIN')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS plans(
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
                    billing_period TEXT NOT NULL CHECK(billing_period IN ('MONTH','YEAR','NONE')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscriptions(
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    plan_code TEXT NOT NULL REFERENCES plans(code),
                    status TEXT NOT NULL CHECK(status IN ('TRIAL','ACTIVE','PAST_DUE','CANCELED')),
                    starts_at TEXT NOT NULL,
                    ends_at TEXT,
                    promo_code TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS promo_codes(
                    code TEXT PRIMARY KEY,
                    percent_off INTEGER NOT NULL CHECK(percent_off BETWEEN 0 AND 100),
                    bonus_days INTEGER NOT NULL CHECK(bonus_days BETWEEN 0 AND 3650),
                    max_redemptions INTEGER CHECK(max_redemptions IS NULL OR max_redemptions > 0),
                    redemption_count INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS promo_redemptions(
                    code TEXT NOT NULL REFERENCES promo_codes(code),
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    redeemed_at TEXT NOT NULL,
                    PRIMARY KEY(code,user_id)
                );
                """
            )
            stamp = _iso(_now())
            db.execute(
                "INSERT OR IGNORE INTO plans"
                "(code,name,price_cents,billing_period,created_at,updated_at) "
                "VALUES('FREE','Free',0,'NONE',?,?)",
                (stamp, stamp),
            )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def setup_required(self) -> bool:
        with closing(self._db()) as db:
            return db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None

    def bootstrap_admin(self, email: object, password: object) -> dict[str, Any]:
        normalized, checked = _email(email), _password(password)
        stamp = _iso(_now())
        try:
            with self._db() as db:
                if db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                    raise AuthError("Master-admin setup is already complete.")
                cursor = db.execute(
                    "INSERT INTO users(email,password_hash,role,created_at,updated_at) "
                    "VALUES(?,?,'MASTER_ADMIN',?,?)",
                    (normalized, _hash_password(checked), stamp, stamp),
                )
                db.execute(
                    "INSERT INTO subscriptions(user_id,plan_code,status,starts_at,updated_at) "
                    "VALUES(?,'FREE','ACTIVE',?,?)",
                    (cursor.lastrowid, stamp, stamp),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthError("That account cannot be created.") from exc
        return {"status": "MASTER_ADMIN_CREATED", "email": normalized}

    def create_session(self, email: object, password: object) -> tuple[str, dict[str, Any]]:
        normalized = _email(email)
        if not isinstance(password, str):
            password = ""
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (normalized,)).fetchone()
            valid = (
                row is not None
                and bool(row["active"])
                and _verify_password(password, row["password_hash"])
            )
            if not valid:
                raise AuthError("Email or password is incorrect.")
            token = secrets.token_urlsafe(32)
            created = _now()
            db.execute(
                "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                (
                    self._token_hash(token),
                    row["id"],
                    _iso(created),
                    _iso(created + timedelta(hours=SESSION_HOURS)),
                ),
            )
        return token, self.authenticate(token) or {}

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        now = _now()
        with self._db() as db:
            row = db.execute(
                "SELECT u.id,u.email,u.role,u.active,s.expires_at,"
                "sub.plan_code,sub.status,sub.ends_at "
                "FROM sessions s JOIN users u ON u.id=s.user_id "
                "LEFT JOIN subscriptions sub ON sub.user_id=u.id "
                "WHERE s.token_hash=? AND s.revoked_at IS NULL",
                (self._token_hash(token),),
            ).fetchone()
            if row is None or not row["active"] or _parse(row["expires_at"]) <= now:
                return None
            entitled = row["status"] in {"TRIAL", "ACTIVE"} and (
                row["ends_at"] is None or _parse(row["ends_at"]) > now
            )
            return {
                "id": row["id"],
                "email": row["email"],
                "role": row["role"],
                "plan": row["plan_code"] or "NONE",
                "subscription_status": row["status"] or "NONE",
                "subscription_ends_at": row["ends_at"],
                "entitled": entitled,
            }

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._db() as db:
            db.execute(
                "UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (_iso(_now()), self._token_hash(token)),
            )

    def admin_snapshot(self) -> dict[str, Any]:
        with closing(self._db()) as db:
            users = [
                dict(row)
                for row in db.execute(
                    "SELECT u.id,u.email,u.role,u.active,u.created_at,"
                    "sub.plan_code,sub.status,sub.ends_at "
                    "FROM users u LEFT JOIN subscriptions sub ON sub.user_id=u.id ORDER BY u.id"
                )
            ]
            plans = [
                dict(row) for row in db.execute("SELECT * FROM plans ORDER BY price_cents,code")
            ]
            promos = [
                dict(row)
                for row in db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
            ]
        return {"users": users, "plans": plans, "promo_codes": promos}

    def create_user(self, email: object, password: object) -> dict[str, Any]:
        normalized, checked = _email(email), _password(password)
        stamp = _iso(_now())
        try:
            with self._db() as db:
                cursor = db.execute(
                    "INSERT INTO users(email,password_hash,role,created_at,updated_at) "
                    "VALUES(?,?,'USER',?,?)",
                    (normalized, _hash_password(checked), stamp, stamp),
                )
                db.execute(
                    "INSERT INTO subscriptions(user_id,plan_code,status,starts_at,updated_at) "
                    "VALUES(?,'FREE','ACTIVE',?,?)",
                    (cursor.lastrowid, stamp, stamp),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthError("An account with that email already exists.") from exc
        return {"status": "USER_CREATED", "email": normalized}

    def save_plan(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {
            "code",
            "name",
            "price_cents",
            "billing_period",
            "active",
        }:
            raise AuthError("Invalid plan.")
        code = str(payload["code"]).strip().upper()
        name = str(payload["name"]).strip()
        if not _CODE.fullmatch(code) or not name or len(name) > 80:
            raise AuthError("Invalid plan code or name.")
        if type(payload["price_cents"]) is not int or payload["price_cents"] < 0:
            raise AuthError("Invalid plan price.")
        if (
            payload["billing_period"] not in {"MONTH", "YEAR", "NONE"}
            or type(payload["active"]) is not bool
        ):
            raise AuthError("Invalid plan settings.")
        stamp = _iso(_now())
        with self._db() as db:
            db.execute(
                "INSERT INTO plans"
                "(code,name,price_cents,billing_period,active,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,"
                "price_cents=excluded.price_cents,billing_period=excluded.billing_period,"
                "active=excluded.active,updated_at=excluded.updated_at",
                (
                    code,
                    name,
                    payload["price_cents"],
                    payload["billing_period"],
                    int(payload["active"]),
                    stamp,
                    stamp,
                ),
            )
        return {"status": "PLAN_SAVED", "code": code}

    def save_promo(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {
            "code",
            "percent_off",
            "bonus_days",
            "max_redemptions",
            "expires_at",
            "active",
        }:
            raise AuthError("Invalid promo code.")
        code = str(payload["code"]).strip().upper()
        percent, days, maximum = (
            payload["percent_off"],
            payload["bonus_days"],
            payload["max_redemptions"],
        )
        if not _CODE.fullmatch(code) or type(percent) is not int or not 0 <= percent <= 100:
            raise AuthError("Invalid promo code or discount.")
        if (
            type(days) is not int
            or not 0 <= days <= 3650
            or (maximum is not None and (type(maximum) is not int or maximum <= 0))
        ):
            raise AuthError("Invalid promo limits.")
        expires = payload["expires_at"]
        if expires is not None:
            try:
                _parse(str(expires))
            except ValueError as exc:
                raise AuthError("Invalid promo expiry.") from exc
        if type(payload["active"]) is not bool:
            raise AuthError("Invalid promo status.")
        with self._db() as db:
            db.execute(
                "INSERT INTO promo_codes"
                "(code,percent_off,bonus_days,max_redemptions,expires_at,active,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                "percent_off=excluded.percent_off,"
                "bonus_days=excluded.bonus_days,max_redemptions=excluded.max_redemptions,"
                "expires_at=excluded.expires_at,active=excluded.active",
                (code, percent, days, maximum, expires, int(payload["active"]), _iso(_now())),
            )
        return {"status": "PROMO_SAVED", "code": code}

    def assign_subscription(self, payload: object) -> dict[str, Any]:
        required = {"user_id", "plan_code", "status", "ends_at"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise AuthError("Invalid subscription.")
        if type(payload["user_id"]) is not int or payload["status"] not in {
            "TRIAL",
            "ACTIVE",
            "PAST_DUE",
            "CANCELED",
        }:
            raise AuthError("Invalid subscription.")
        plan = str(payload["plan_code"]).strip().upper()
        ends = payload["ends_at"]
        if ends is not None:
            try:
                _parse(str(ends))
            except ValueError as exc:
                raise AuthError("Invalid subscription expiry.") from exc
        stamp = _iso(_now())
        try:
            with self._db() as db:
                db.execute(
                    "INSERT INTO subscriptions"
                    "(user_id,plan_code,status,starts_at,ends_at,updated_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                    "plan_code=excluded.plan_code,"
                    "status=excluded.status,ends_at=excluded.ends_at,updated_at=excluded.updated_at",
                    (payload["user_id"], plan, payload["status"], stamp, ends, stamp),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthError("Unknown user or plan.") from exc
        return {"status": "SUBSCRIPTION_SAVED", "user_id": payload["user_id"]}

    def redeem(self, user_id: int, code_value: object) -> dict[str, Any]:
        code = str(code_value).strip().upper()
        now = _now()
        with self._db() as db:
            promo = db.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
            if promo is None or not promo["active"]:
                raise AuthError("Promo code is invalid or inactive.")
            if promo["expires_at"] and _parse(promo["expires_at"]) <= now:
                raise AuthError("Promo code has expired.")
            if (
                promo["max_redemptions"] is not None
                and promo["redemption_count"] >= promo["max_redemptions"]
            ):
                raise AuthError("Promo code redemption limit was reached.")
            try:
                db.execute(
                    "INSERT INTO promo_redemptions(code,user_id,redeemed_at) VALUES(?,?,?)",
                    (code, user_id, _iso(now)),
                )
            except sqlite3.IntegrityError as exc:
                raise AuthError("This promo code was already used by this account.") from exc
            current = db.execute(
                "SELECT ends_at FROM subscriptions WHERE user_id=?", (user_id,)
            ).fetchone()
            base = now
            if current and current["ends_at"] and _parse(current["ends_at"]) > now:
                base = _parse(current["ends_at"])
            ends = base + timedelta(days=promo["bonus_days"]) if promo["bonus_days"] else None
            if ends is not None:
                db.execute(
                    "UPDATE subscriptions SET status='ACTIVE',ends_at=?,promo_code=?,"
                    "updated_at=? WHERE user_id=?",
                    (_iso(ends), code, _iso(now), user_id),
                )
            db.execute(
                "UPDATE promo_codes SET redemption_count=redemption_count+1 WHERE code=?", (code,)
            )
        return {
            "status": "PROMO_REDEEMED",
            "code": code,
            "percent_off": promo["percent_off"],
            "bonus_days": promo["bonus_days"],
            "subscription_ends_at": _iso(ends) if ends else None,
        }
