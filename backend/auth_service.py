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
    "/auth/status",
    "/favicon.ico",
    "/health",
})

# The one prefix that must stay a prefix: hashed build assets live under it
# and there is no session yet when the login page loads them.
PUBLIC_PREFIXES = ("/assets/",)


def _password() -> str:
    return (os.getenv("ACCESS_PASSWORD") or "").strip()


def _secret() -> bytes:
    """
    Key used to sign session cookies.

    Derived from the password when no explicit SESSION_SECRET is set, so
    changing the password invalidates every existing session -- which is what
    you want when revoking access.
    """
    explicit = (os.getenv("SESSION_SECRET") or "").strip()
    return (explicit or f"usr::{_password()}").encode("utf-8")


def enabled() -> bool:
    """True when a password is configured and access is therefore restricted."""
    return bool(_password())


def _sign(expires_at: int) -> str:
    mac = hmac.new(_secret(), str(expires_at).encode(), hashlib.sha256)
    return f"{expires_at}.{mac.hexdigest()}"


def issue_token() -> str:
    return _sign(int(time.time()) + SESSION_TTL)


def valid_token(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    stamp, _, _mac = token.partition(".")
    try:
        expires_at = int(stamp)
    except ValueError:
        return False
    if expires_at < time.time():
        return False
    # compare_digest: constant time, so a wrong signature cannot be guessed by
    # timing how long the comparison takes.
    return hmac.compare_digest(token, _sign(expires_at))


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


def require_session(request: Request) -> None:
    """Raise 401 unless the request carries a valid session."""
    if not enabled():
        return
    if valid_token(request.cookies.get(COOKIE_NAME)):
        return
    # A bearer token is accepted too, so scripts and curl can use the same
    # password without driving the cookie flow.
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        if check_password(header[7:].strip()):
            return
    raise HTTPException(status_code=401, detail="Authentication required")


def new_secret() -> str:
    """A fresh SESSION_SECRET value, for the setup instructions."""
    return secrets.token_urlsafe(32)
