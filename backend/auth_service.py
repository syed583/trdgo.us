"""
Access control for the shared deployment.

The app was written as a single-operator tool with no authentication, which is
fine on localhost. The moment it is reachable from the internet, every endpoint
is public: the watchlist, the trade journal, the alerts and the strategy
configuration are all readable and writable by anyone who has the URL.

So exposure is gated on a password. Set ACCESS_PASSWORD in backend/.env and the
whole app requires a login; leave it empty and the app stays open, which is
only safe while it is bound to localhost.

This is deliberately simple - one shared password, signed cookie, no user
accounts - because it protects one person's research tool. It is not an
identity system and should not be reused as one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

COOKIE_NAME = "usr_session"
SESSION_TTL = 30 * 24 * 3600  # 30 days

# Paths reachable without a session: the login page itself, the endpoint that
# checks the password, and the static assets needed to render the form.
# Exact public paths: reachable with no session because the login screen
# needs them. Matched exactly, not by prefix -- a prefix of "/health" would
# also open any future "/health-internal" route by accident.
PUBLIC_PATHS = frozenset({
    "/auth/login",
    "/auth/otp/request",   # phone signup: request a WhatsApp code
    "/auth/register",      # phone signup: verify code + create account
    "/auth/reset/request", # forgot password: request a WhatsApp code
    "/auth/reset",         # forgot password: verify code + set new password
    "/auth/status",
    "/favicon.ico",
    "/health",
})

# The one prefix that must stay a prefix: hashed build assets live under it
# and there is no session yet when the login page loads them.
PUBLIC_PREFIXES = ("/assets/",)


def _password() -> str:
    return (os.getenv("ACCESS_PASSWORD") or "").strip()


# Where a generated session secret is persisted when SESSION_SECRET is unset.
# Lives in the gitignored .sec_cache/ (same dir sec_service uses), so it is
# never committed and survives restarts/deploys (the dir is not wiped by a
# git pull). SEC_CACHE_DIR overrides the location, matching sec_service.
_SECRET_FILE = (Path(os.getenv("SEC_CACHE_DIR") or (Path(__file__).parent / ".sec_cache"))
                / "session_secret")
_secret_lock = threading.Lock()
_secret_cache: Optional[bytes] = None


def _persisted_secret() -> Optional[bytes]:
    """A stable random secret from disk, generated once on first use.

    Returns None only if the file cannot be read or created (e.g. a read-only
    filesystem), in which case the caller falls back to the password.
    """
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    with _secret_lock:
        if _secret_cache is not None:
            return _secret_cache
        try:
            val = ""
            if _SECRET_FILE.exists():
                val = _SECRET_FILE.read_text(encoding="utf-8").strip()
            if not val:
                _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
                val = new_secret()
                _SECRET_FILE.write_text(val, encoding="utf-8")
                try:
                    os.chmod(_SECRET_FILE, 0o600)
                except OSError:
                    pass  # best effort; Windows/locked-down FS may refuse
            _secret_cache = val.encode("utf-8")
            return _secret_cache
        except OSError:
            return None


def _secret() -> bytes:
    """
    Key used to sign session cookies.

    Priority: an explicit SESSION_SECRET env var (lets several instances share
    one key); else a random secret persisted to .sec_cache/ and generated once,
    so cookie integrity never rests on the admin password's entropy; else, only
    if that file is unwritable, derive from the password as a last resort.
    """
    explicit = (os.getenv("SESSION_SECRET") or "").strip()
    if explicit:
        return explicit.encode("utf-8")
    persisted = _persisted_secret()
    if persisted:
        return persisted
    return f"usr::{_password()}".encode("utf-8")


def enabled() -> bool:
    """True when a password is configured and access is therefore restricted."""
    return bool(_password())


# A session cookie now carries WHO is logged in, not just that someone is.
# Payload is "username|role|expires"; the signature covers all three, so a
# user cannot edit their own cookie to become admin.
def _sign(payload: str) -> str:
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256)
    return f"{payload}.{mac.hexdigest()}"


def issue_token(username: str = "admin", role: str = "admin") -> str:
    payload = f"{username}|{role}|{int(time.time()) + SESSION_TTL}"
    return _sign(payload)


def read_token(token: Optional[str]) -> Optional[dict]:
    """Return {username, role} from a valid token, or None."""
    if not token or token.count(".") < 1:
        return None
    payload, _, _mac = token.rpartition(".")
    parts = payload.split("|")
    if len(parts) != 3:
        # A pre-upgrade cookie ("expires.mac") carries no username or role.
        # Granting it admin -- as an earlier version did -- means any stale
        # session from before the multi-user upgrade silently opens the admin
        # panel. Reject it instead: the holder logs in again with a real
        # username and password and gets the role that account actually has.
        return None
    username, role, stamp = parts
    try:
        expires_at = int(stamp)
    except ValueError:
        return None
    if expires_at < time.time():
        return None
    # compare_digest: constant time, so a wrong signature cannot be guessed by
    # timing how long the comparison takes.
    if not hmac.compare_digest(token, _sign(payload)):
        return None
    return {"username": username, "role": role}


def valid_token(token: Optional[str]) -> bool:
    return read_token(token) is not None


def authenticate(username: str, password: str) -> Optional[dict]:
    """
    Check a login. Returns {username, role} or None.

    The admin is ACCESS_PASSWORD under the username 'admin'; everyone else is
    a stored account. An empty username is treated as the admin so the plain
    password login the app shipped with still works.
    """
    username = (username or "").strip().lower() or "admin"
    if username == "admin":
        return {"username": "admin", "role": "admin"} if check_password(password) else None
    import user_service
    return user_service.check_credentials(username, password)


# --- login throttle --------------------------------------------------------
# One shared password is brute-forceable if a caller may try it without limit.
# Failed attempts are counted per client, and after a threshold that client
# waits out a lockout. In-memory and per-process, which is enough for a
# single-instance deployment; it is not a distributed rate limiter.
_MAX_FAILS = 8
_LOCKOUT = 300.0  # seconds a client is refused after too many failures
_WINDOW = 300.0   # failures older than this no longer count

# A per-client counter can be evaded by spoofing X-Forwarded-For, so a global
# ceiling backs it up: once this many failures land across all clients inside
# the window, every login waits out the lockout. It is high enough not to trip
# in normal single-operator use, and it caps a distributed guessing attack.
_MAX_FAILS_GLOBAL = 40

_fail_lock = threading.Lock()
_fails: dict[str, list[float]] = {}
_fails_global: list[float] = []


def _prune(times: list[float], now: float) -> list[float]:
    return [t for t in times if now - t < _WINDOW]


def login_blocked(client: str) -> Optional[int]:
    """Seconds the caller must wait, or None if a login may be attempted."""
    now = time.time()
    with _fail_lock:
        glob = _prune(_fails_global, now)
        _fails_global[:] = glob
        if len(glob) >= _MAX_FAILS_GLOBAL:
            wait = int(_LOCKOUT - (now - glob[-1]))
            if wait > 0:
                return wait

        times = _prune(_fails.get(client, []), now)
        _fails[client] = times
        if len(times) >= _MAX_FAILS:
            wait = int(_LOCKOUT - (now - times[-1]))
            return wait if wait > 0 else None
    return None


def note_login_failure(client: str) -> None:
    now = time.time()
    with _fail_lock:
        _fails[client] = _prune(_fails.get(client, []), now) + [now]
        _fails_global[:] = _prune(_fails_global, now) + [now]


def note_login_success(client: str) -> None:
    with _fail_lock:
        _fails.pop(client, None)


def check_password(candidate: str) -> bool:
    expected = _password()
    if not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256((candidate or "").encode()).hexdigest(),
        hashlib.sha256(expected.encode()).hexdigest(),
    )


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def current_user(request: Request) -> Optional[dict]:
    """The logged-in user {username, role}, or None. Admin when auth is off."""
    if not enabled():
        return {"username": "admin", "role": "admin"}
    user = read_token(request.cookies.get(COOKIE_NAME))
    if user:
        # 'admin' is the env password account, not a DB row, so it is always
        # valid. For a stored account, the signed cookie is not enough: if the
        # account has since been deleted or deactivated, its session must stop
        # working (within _ACCESS_TTL), not linger until the cookie expires.
        if user.get("username") == "admin":
            return user
        try:
            import user_service
            if user_service.account_active(user["username"]):
                return user
            return None
        except Exception:  # noqa: BLE001 - never hard-fail auth on a DB blip
            return user
    # A bearer token (the admin password) is accepted too, so scripts and curl
    # can use the same access without the cookie flow.
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer ") and check_password(header[7:].strip()):
        return {"username": "admin", "role": "admin"}
    return None


def require_session(request: Request) -> None:
    """Raise 401 unless the request carries a valid session."""
    if current_user(request) is None:
        raise HTTPException(status_code=401, detail="Authentication required")


def require_admin(request: Request) -> dict:
    """Raise unless the caller is the admin. Returns the admin user."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only.")
    return user


def new_secret() -> str:
    """A fresh SESSION_SECRET value, for the setup instructions."""
    return secrets.token_urlsafe(32)
