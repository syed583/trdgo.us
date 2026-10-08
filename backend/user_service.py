"""
User accounts: create them, check them, and record who logged in from where.

The admin is not an account here -- it logs in with ACCESS_PASSWORD (see
auth_service). Everyone in this table is a non-admin user the admin invited.

Passwords are stored as a scrypt hash with a per-user salt, using only the
standard library -- no bcrypt/argon2 dependency to install on the server. The
plaintext password exists for exactly one moment: when the admin creates the
account and is shown it once to hand to the person. It is never stored and
cannot be recovered; a forgotten password is reset by generating a new one.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from database import SessionLocal
from models_user import AppUser, LoginEvent

# Roles. 'user' is a normal account; the rest are admin tiers. The env
# ACCESS_PASSWORD account is the implicit super_admin. Permissions per role live
# in auth_service (the enforcement layer); these are the valid names.
ADMIN_ROLES = ("super_admin", "operations", "finance", "marketing", "support")
ROLES = ("user",) + ADMIN_ROLES

# One-time, idempotent add of the display_name column for phone-OTP accounts.
# create_all() does not alter an existing table, so add it explicitly.
_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        db = SessionLocal()
        try:
            db.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS display_name VARCHAR(80)"))
            db.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS age INTEGER"))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - a transient DB blip retries next call
        return
    _schema_ready = True


def create_phone_user(phone: str, name: str, password: str,
                      age: Optional[int] = None) -> dict:
    """
    Create a self-service account from a verified phone signup. The username is
    the normalized phone number; `name` is the display name. Phone-OTP accounts
    get full access immediately (per product decision).
    """
    import whatsapp_service as wa
    _ensure_schema()
    username = wa.normalize_phone(phone)
    if len(username) < 8:
        return {"status": "INVALID", "detail": "Enter a valid phone number with country code."}
    if not valid_password(password):
        return {"status": "INVALID", "detail": "Password must be 6-128 characters."}
    name = (name or "").strip()[:80] or username
    try:
        age_val = int(age) if age not in (None, "") else None
        if age_val is not None and not (1 <= age_val <= 120):
            age_val = None
    except (TypeError, ValueError):
        age_val = None
    salt = secrets.token_hex(16)
    db = SessionLocal()
    try:
        existing = db.query(AppUser).filter(AppUser.username == username).first()
        if existing:
            return {"status": "EXISTS",
                    "detail": "This number already has an account — sign in with your password."}
        db.add(AppUser(username=username, display_name=name, age=age_val,
                       password_hash=_hash(password, salt), salt=salt,
                       role="user", active=True, full_access=True))
        db.commit()
    finally:
        db.close()
    _access_invalidate(username)
    _valid_invalidate(username)
    return {"status": "OK", "username": username, "display_name": name}


def phone_exists(phone: str) -> bool:
    import whatsapp_service as wa
    username = wa.normalize_phone(phone)
    if len(username) < 8:
        return False
    db = SessionLocal()
    try:
        return db.query(AppUser).filter(AppUser.username == username).first() is not None
    except Exception:  # noqa: BLE001
        return False
    finally:
        db.close()


def display_name_for(username: str) -> Optional[str]:
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        u = db.query(AppUser).filter(AppUser.username == username).first()
        return (u.display_name if u else None)
    except Exception:  # noqa: BLE001
        return None
    finally:
        db.close()

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,39}$")

# scrypt parameters. These are the interactive-login defaults: strong enough
# that a stolen hash is expensive to crack, cheap enough to verify per login.
_N, _R, _P = 16384, 8, 1


def _hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"),
                          n=_N, r=_R, p=_P, dklen=32).hex()


def valid_username(username: str) -> bool:
    return bool(_USERNAME.match((username or "").strip().lower()))


def _generate_password() -> str:
    """A readable but strong one-time password to hand to a new user."""
    return secrets.token_urlsafe(12)


def valid_password(password: str) -> bool:
    """A password the admin typed must be long enough to be worth having."""
    return isinstance(password, str) and 6 <= len(password) <= 128


def create_user(username: str, role: str = "user",
                password: Optional[str] = None) -> dict:
    """
    Make an account and return the password to give the person.

    When the admin supplies a password, that is the one set (and echoed back
    so it can be handed over). When none is given, a strong one is generated.
    The plaintext is in the response once and nowhere else -- only its hash is
    stored.
    """
    username = (username or "").strip().lower()
    if not valid_username(username):
        return {"status": "INVALID",
                "detail": "Username: 3-40 chars, letters/digits/._- , start "
                          "with a letter or digit."}
    if username == "admin":
        return {"status": "INVALID", "detail": "'admin' is reserved."}
    if role not in ("user",):
        role = "user"

    if password:
        if not valid_password(password):
            return {"status": "INVALID",
                    "detail": "Password must be 6-128 characters."}
    else:
        password = _generate_password()
    salt = secrets.token_hex(16)
    db = SessionLocal()
    try:
        if db.query(AppUser).filter(AppUser.username == username).first():
            return {"status": "EXISTS", "detail": f"{username} already exists."}
        db.add(AppUser(username=username, password_hash=_hash(password, salt),
                       salt=salt, role=role, active=True))
        db.commit()
    finally:
        db.close()
    return {"status": "OK", "username": username, "password": password,
            "detail": "Give this password to the user now; it is not shown again."}


def reset_password(username: str, password: Optional[str] = None) -> dict:
    """
    Set a new password for an existing user.

    The admin may pass the exact password to set; otherwise a strong one is
    generated. Either way the plaintext is returned once so it can be handed
    over.
    """
    username = (username or "").strip().lower()
    if password and not valid_password(password):
        return {"status": "INVALID",
                "detail": "Password must be 6-128 characters."}
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND"}
        password = password or _generate_password()
        salt = secrets.token_hex(16)
        user.password_hash = _hash(password, salt)
        user.salt = salt
        db.commit()
    finally:
        db.close()
    return {"status": "OK", "username": username, "password": password,
            "detail": "New password; it is not shown again."}


def set_full_access(username: str, full: bool) -> dict:
    """Grant or revoke full access (running analysis and changing data)."""
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND"}
        user.full_access = bool(full)
        db.commit()
    finally:
        db.close()
    _access_invalidate(username)  # so the change is seen immediately
    return {"status": "OK", "username": username, "full_access": bool(full)}


# The full-access check runs on /auth/me and the access gate -- i.e. on the hot
# path -- and the database can be remote (a query is a round trip). Cache the
# answer briefly so a page does not pay that round trip every time; a grant or
# revoke takes effect within _ACCESS_TTL seconds.
_ACCESS_TTL = 30.0
_access_cache: dict[str, tuple[float, bool]] = {}
_access_lock = __import__("threading").Lock()


def _access_cached(username: str):
    with _access_lock:
        hit = _access_cache.get(username)
    if hit and (time.time() - hit[0]) < _ACCESS_TTL:
        return hit[1]
    return None


def _access_store(username: str, value: bool) -> None:
    with _access_lock:
        _access_cache[username] = (time.time(), value)


def _access_invalidate(username: str) -> None:
    with _access_lock:
        _access_cache.pop(username, None)


def has_full_access(username: str) -> bool:
    """True when the account exists, is active, and has been granted full access."""
    username = (username or "").strip().lower()
    cached = _access_cached(username)
    if cached is not None:
        return cached
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        value = bool(user and user.active and user.full_access)
    finally:
        db.close()
    _access_store(username, value)
    return value


def set_active(username: str, active: bool, reason: Optional[str] = None) -> dict:
    """Enable/disable (block) an account. A block can carry a short reason."""
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND"}
        user.active = bool(active)
        if active:
            user.blocked_reason = None
        elif reason:
            user.blocked_reason = str(reason)[:200]
        db.commit()
    finally:
        db.close()
    _access_invalidate(username)
    _valid_invalidate(username)
    return {"status": "OK", "username": username, "active": bool(active)}


def set_role(username: str, role: str) -> dict:
    """Set an account's role (admin sub-roles or back to user)."""
    username = (username or "").strip().lower()
    role = (role or "user").strip().lower()
    if role not in ROLES:
        return {"status": "INVALID", "detail": f"Unknown role: {role}"}
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND"}
        user.role = role
        # An admin role implies full access; a demotion to plain user does not
        # auto-revoke it (the admin can do that separately).
        if role in ADMIN_ROLES:
            user.full_access = True
        db.commit()
    finally:
        db.close()
    _access_invalidate(username)
    _valid_invalidate(username)
    return {"status": "OK", "username": username, "role": role}


def delete_user(username: str, hard: bool = True) -> dict:
    """Remove an account. `hard=True` (the default) permanently deletes the row
    AND the user's own workspace data (watchlist/alerts/journal/strategy), so the
    phone number is freed to sign up again and nothing is left orphaned.
    `hard=False` soft-deletes instead (row kept, deactivated, excluded)."""
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND", "username": username}
        if hard:
            db.delete(user)
            # Free the user's own records too, so a deleted account leaves no
            # orphaned rows behind (the username is the owner key).
            try:
                from models_user import (AlertRule, JournalEntry,
                                         StrategySetting, WatchlistItem)
                for Model in (WatchlistItem, AlertRule, JournalEntry, StrategySetting):
                    db.query(Model).filter(Model.owner == username).delete()
            except Exception:  # noqa: BLE001 - never block the user delete
                pass
        else:
            user.deleted_at = datetime.now(timezone.utc)
            user.active = False
        db.commit()
    finally:
        db.close()
    # Drop the cached access/validity so the deleted account's live session stops
    # being honoured within _ACCESS_TTL instead of lingering until the cookie
    # expires.
    _access_invalidate(username)
    _valid_invalidate(username)
    return {"status": "OK", "username": username, "hard": bool(hard)}


def restore_user(username: str) -> dict:
    """Undo a soft delete: clear deleted_at and re-enable the account."""
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND"}
        user.deleted_at = None
        user.active = True
        db.commit()
    finally:
        db.close()
    _access_invalidate(username)
    _valid_invalidate(username)
    return {"status": "OK", "username": username}


# Session validity (exists + active), cached like access so current_user can
# reject a deleted/deactivated account's still-signed cookie without a DB hit on
# every request. Separate from has_full_access, which also requires full_access.
_valid_cache: dict[str, tuple[float, bool]] = {}


def _valid_invalidate(username: str) -> None:
    with _access_lock:
        _valid_cache.pop(username, None)


def account_active(username: str) -> bool:
    """True when the account currently exists and is active. 30s cached.

    Fails open on a DB error: a flaky far-region DB must not log everyone out;
    an invalidation (delete/deactivate) clears the cache so the next check hits
    the DB and, when it is reachable, returns the real answer."""
    username = (username or "").strip().lower()
    if not username:
        return False
    with _access_lock:
        hit = _valid_cache.get(username)
    if hit and (time.time() - hit[0]) < _ACCESS_TTL:
        return hit[1]
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        value = bool(user and user.active)
    except Exception:  # noqa: BLE001
        return True
    finally:
        db.close()
    with _access_lock:
        _valid_cache[username] = (time.time(), value)
    return value


def check_credentials(username: str, password: str) -> Optional[dict]:
    """Return the user (as a dict) when the password matches and is active."""
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user or not user.active:
            return None
        expected = user.password_hash
        got = _hash(password or "", user.salt)
        if not hmac.compare_digest(expected, got):
            return None
        return {"username": user.username, "role": user.role}
    finally:
        db.close()


def record_login(username: str, ok: bool, ip: Optional[str],
                 user_agent: Optional[str]) -> None:
    """Log an attempt, and on success stamp the user's last-login fields."""
    # The test suite exercises the login gate with wrong passwords through
    # FastAPI's TestClient (ip/agent "testclient"), which otherwise fills the
    # real login log with fake failed "admin" attempts. Skip recording during
    # tests, and skip the TestClient sentinel regardless.
    import os
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if (ip or "") == "testclient" or (user_agent or "") == "testclient":
        return
    db = SessionLocal()
    try:
        db.add(LoginEvent(username=(username or "")[:40], ok=bool(ok),
                          ip=(ip or "")[:64], user_agent=(user_agent or "")[:256]))
        if ok:
            user = (db.query(AppUser)
                    .filter(AppUser.username == (username or "").lower()).first())
            if user:
                user.last_login_at = datetime.now(timezone.utc)
                user.last_login_ip = (ip or "")[:64]
                user.login_count = (user.login_count or 0) + 1
        db.commit()
    except Exception:  # noqa: BLE001 - login must not fail because logging did
        db.rollback()
    finally:
        db.close()


def _user_dict(u: AppUser) -> dict:
    return {
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "active": u.active,
        "full_access": bool(u.full_access),
        "blocked_reason": u.blocked_reason,
        "deleted": u.deleted_at is not None,
        "deleted_at": u.deleted_at.isoformat() if u.deleted_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "last_login_ip": u.last_login_ip,
        "login_count": u.login_count or 0,
    }


def list_users(search: Optional[str] = None,
               include_deleted: bool = False) -> list[dict]:
    """All accounts, newest first. Soft-deleted rows are excluded unless asked
    for; `search` matches username or display name (case-insensitive)."""
    db = SessionLocal()
    try:
        q = db.query(AppUser)
        if not include_deleted:
            q = q.filter(AppUser.deleted_at.is_(None))
        s = (search or "").strip().lower()
        if s:
            like = f"%{s}%"
            from sqlalchemy import func, or_
            q = q.filter(or_(func.lower(AppUser.username).like(like),
                             func.lower(AppUser.display_name).like(like)))
        rows = q.order_by(AppUser.created_at.desc()).all()
        return [_user_dict(u) for u in rows]
    finally:
        db.close()


def stats() -> dict:
    """Headline numbers for the admin dashboard."""
    now = datetime.now(timezone.utc)
    day = now - timedelta(hours=24)
    week = now - timedelta(days=7)
    db = SessionLocal()
    try:
        from sqlalchemy import func  # noqa: F401
        live = AppUser.deleted_at.is_(None)
        total = db.query(AppUser).filter(live).count()
        new_24h = db.query(AppUser).filter(live, AppUser.created_at >= day).count()
        new_7d = db.query(AppUser).filter(live, AppUser.created_at >= week).count()
        active = db.query(AppUser).filter(live, AppUser.active.is_(True)).count()
        blocked = db.query(AppUser).filter(live, AppUser.active.is_(False)).count()
        full = db.query(AppUser).filter(live, AppUser.full_access.is_(True)).count()
        admins = db.query(AppUser).filter(live, AppUser.role != "user").count()
        failed_24h = (db.query(LoginEvent)
                      .filter(LoginEvent.ok.is_(False), LoginEvent.at >= day).count())
        logins_24h = (db.query(LoginEvent)
                      .filter(LoginEvent.ok.is_(True), LoginEvent.at >= day).count())
        recent = (db.query(AppUser).filter(live)
                  .order_by(AppUser.created_at.desc()).limit(8).all())
        recent_signups = [_user_dict(u) for u in recent]
    finally:
        db.close()
    return {
        "total_users": total,
        "new_24h": new_24h,
        "new_7d": new_7d,
        "active": active,
        "blocked": blocked,
        "full_access": full,
        "admins": admins,
        "failed_logins_24h": failed_24h,
        "logins_24h": logins_24h,
        "recent_signups": recent_signups,
        "status": "OK",
    }


def user_detail(username: str) -> dict:
    """One account with its own login history (for the user-detail view)."""
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        u = db.query(AppUser).filter(AppUser.username == username).first()
        if not u:
            return {"status": "NOT_FOUND"}
        logins = (db.query(LoginEvent)
                  .filter(LoginEvent.username == username)
                  .order_by(LoginEvent.at.desc()).limit(50).all())
        events = [{
            "ok": e.ok, "ip": e.ip, "user_agent": e.user_agent,
            "at": e.at.isoformat() if e.at else None,
        } for e in logins]
    finally:
        db.close()
    return {"status": "OK", "user": _user_dict(u), "logins": events}


def recent_logins(limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (db.query(LoginEvent).order_by(LoginEvent.at.desc())
                .limit(max(1, min(limit, 200))).all())
        return [{
            "username": e.username,
            "ok": e.ok,
            "ip": e.ip,
            "user_agent": e.user_agent,
            "at": e.at.isoformat() if e.at else None,
        } for e in rows]
    finally:
        db.close()
