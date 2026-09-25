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
from datetime import datetime, timezone
from typing import Optional

from database import SessionLocal
from models_user import AppUser, LoginEvent

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


def create_user(username: str, role: str = "user") -> dict:
    """
    Make an account and return the one-time password to give the person.

    The password is in the response once and nowhere else -- it is not stored,
    only its hash is.
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


def reset_password(username: str) -> dict:
    """Generate a new one-time password for an existing user."""
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND"}
        password = _generate_password()
        salt = secrets.token_hex(16)
        user.password_hash = _hash(password, salt)
        user.salt = salt
        db.commit()
    finally:
        db.close()
    return {"status": "OK", "username": username, "password": password,
            "detail": "New password; it is not shown again."}


def set_active(username: str, active: bool) -> dict:
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            return {"status": "NOT_FOUND"}
        user.active = bool(active)
        db.commit()
    finally:
        db.close()
    return {"status": "OK", "username": username, "active": bool(active)}


def delete_user(username: str) -> dict:
    username = (username or "").strip().lower()
    db = SessionLocal()
    try:
        deleted = (db.query(AppUser)
                   .filter(AppUser.username == username).delete())
        db.commit()
    finally:
        db.close()
    return {"status": "OK" if deleted else "NOT_FOUND", "username": username}


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


def list_users() -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.query(AppUser).order_by(AppUser.created_at.desc()).all()
        return [{
            "username": u.username,
            "role": u.role,
            "active": u.active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "last_login_ip": u.last_login_ip,
            "login_count": u.login_count or 0,
        } for u in rows]
    finally:
        db.close()


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
