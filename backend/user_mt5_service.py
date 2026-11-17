"""C.1: a user's own MT5 account - connect, and READ. No order is placed from here, ever.

C.1 is deliberately read-only. Copying trades is C.2 (recorded only, nothing sent) and C.3 (live, opt in per
user). Keeping them apart means the plumbing - per-user credentials, encryption, sessions - is proven before
anything can move money.

The operator's connection and a user's connection must never touch each other: mt5_bridge keeps ONE
module-level session for the operator, so every call here passes its own token explicitly.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import mt5_bridge as bridge
import user_mt5_store as store
import user_secret_box as box

_log = logging.getLogger("user_mt5")

# A user's session token, in memory only, never written down. {username: (token, obtained_at, login)}
# F.1: the LOGIN is kept beside the token. A token on its own cannot say which account it opens, so nothing
# could check that an order was going to the right place - and on 2026-11-16, 25 of them did not.
_tokens: dict = {}
_lock = threading.Lock()
TOKEN_TTL_S = 20 * 60.0


def _cached_entry(username: str):
    with _lock:
        got = _tokens.get(username)
    if not got:
        return None
    token, at, login = got
    if time.time() - at > TOKEN_TTL_S:
        with _lock:
            _tokens.pop(username, None)
        return None
    return token, login


def _cached_token(username: str) -> Optional[str]:
    got = _cached_entry(username)
    return got[0] if got else None


def _store_token(username: str, token: str, login: str = "") -> None:
    with _lock:
        _tokens[username] = (token, time.time(), str(login))


def forget(username: str) -> None:
    with _lock:
        _tokens.pop(username, None)


def _connect(username: str) -> dict:
    """Open a session for this user from the stored credentials. The password stays inside this function."""
    creds = store.secret_of(username)
    if not creds:
        return {"ok": False, "reason": "RECONNECT_NEEDED",
                "detail": "the stored password cannot be read; connect the account again"}
    login, server, password = creds
    # C.2a: a dict of OUR own, so the operator's module session is never read or written by this call. Before
    # this, bridge.connect() blanked the operator's token for the length of the ConnectEx call and every admin
    # request in that window failed with "no session token: connect first" (owner, 2026-11-16).
    sess: dict = {"base_url": None, "token": None, "login": None, "server": None}
    try:
        res = bridge.connect(int(login), password, server, session=sess)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "BAD_LOGIN", "detail": "the stored account number is not a number"}
    finally:
        del password                                   # out of this frame as early as possible
    if not res.get("ok"):
        store.record_check(username, "FAILED", res.get("detail") or res.get("reason"))
        return {"ok": False, "reason": res.get("reason") or "CONNECT_FAILED", "detail": res.get("detail")}

    token = sess.get("token")
    if not token:
        store.record_check(username, "FAILED", "no session token")
        return {"ok": False, "reason": "NO_TOKEN", "detail": "the broker did not return a session"}
    _store_token(username, token, login)
    store.record_check(username, "OK", None)
    return {"ok": True}


def _token_for(username: str) -> tuple:
    token = _cached_token(username)
    if token:
        return (token, None)
    res = _connect(username)
    if not res.get("ok"):
        return (None, res)
    return (_cached_token(username), None)


def verify(username: str) -> dict:
    """Connect once and read the account summary. This is what 'Test connection' does."""
    acct = account(username)
    return {"ok": acct.get("ok"), "reason": acct.get("reason"), "detail": acct.get("detail"),
            "account": acct.get("account")}


def account(username: str) -> dict:
    """Balance, equity and margin for the user's own account. Read-only."""
    if not bridge.available():
        return {"ok": False, "reason": "PROVIDER_UNAVAILABLE", "detail": "the broker bridge is not configured"}
    token, err = _token_for(username)
    if err:
        return err
    res = bridge._call("AccountSummary", token=token)                        # noqa: SLF001
    if not res.get("ok"):
        forget(username)                                                     # a dead token must not stick
        store.record_check(username, "FAILED", res.get("detail") or res.get("reason"))
        return {"ok": False, "reason": res.get("reason") or "READ_FAILED", "detail": res.get("detail")}
    d = res.get("data") or {}
    return {"ok": True, "account": {
        "balance": bridge._f(bridge._g(d, "balance", "Balance")),            # noqa: SLF001
        "equity": bridge._f(bridge._g(d, "equity", "Equity")),               # noqa: SLF001
        "margin": bridge._f(bridge._g(d, "margin", "Margin")),               # noqa: SLF001
        "free_margin": bridge._f(bridge._g(d, "freeMargin", "free_margin")),  # noqa: SLF001
        "currency": bridge._g(d, "currency", "Currency"),                    # noqa: SLF001
    }}


def positions(username: str) -> dict:
    """The user's own open positions. Read-only: nothing here can open or close one."""
    if not bridge.available():
        return {"ok": False, "reason": "PROVIDER_UNAVAILABLE", "detail": "the broker bridge is not configured"}
    token, err = _token_for(username)
    if err:
        return err
    res = bridge._call("OpenedOrders", token=token)                          # noqa: SLF001
    if not res.get("ok"):
        forget(username)
        return {"ok": False, "reason": res.get("reason") or "READ_FAILED", "detail": res.get("detail")}
    rows = res.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("orders") or rows.get("positions") or []
    out = []
    for r in rows if isinstance(rows, list) else []:
        out.append({
            "ticket": bridge._g(r, "ticket", "Ticket"),                      # noqa: SLF001
            "symbol": bridge._g(r, "symbol", "Symbol"),                      # noqa: SLF001
            "volume": bridge._f(bridge._g(r, "lots", "volume", "Volume")),   # noqa: SLF001
            "open_price": bridge._f(bridge._g(r, "openPrice", "open_price")),  # noqa: SLF001
            "profit": bridge._f(bridge._g(r, "profit", "Profit")),           # noqa: SLF001
        })
    return {"ok": True, "positions": out}


def status(username: str) -> dict:
    """Everything the Settings tab shows. Never includes the password or the full account number."""
    row = store.get(username)
    return {
        "storage_ready": box.available(),
        "storage_problem": box.why_unavailable(),
        "provider_ready": bridge.available(),
        "account": row,
        "copy_trading": {"stage": "C.1", "orders_enabled": False,
                         "note": "C.1 only reads this account. Copying trades arrives in a later release."},
    }


# --------------------------------------------------------------------------- the operator's own account
def is_operator_account(login: str, server: str) -> bool:
    """True when these are the credentials OUR engine trades with.

    A user must not connect the account our own book runs on: both sides would place orders into it, the
    positions would be indistinguishable, and our EOD sweep would close theirs. Compared on the account
    number and the server only - the saved password is never read here.
    """
    try:
        import mt5_autotrade as AT
        saved = AT.load_credentials()
    except Exception:  # noqa: BLE001 - no saved credentials, or the file cannot be read: nothing to clash with
        return False
    if not saved:
        return False
    try:
        same_login = int(str(login).strip()) == int(saved.get("login"))
    except (TypeError, ValueError):
        return False
    ours = str(saved.get("server") or "").strip().lower()
    theirs = str(server or "").strip().lower()
    return same_login and (ours == theirs or not ours or not theirs)


# --------------------------------------------------------------------------- F.1: whose account is this?
def session_for(username: str) -> tuple:
    """(token, None) for a session that is proven to be THIS user's, or (None, refusal).

    Three things must hold before a single order is sent:
      * we have a session, and we know which login opened it;
      * that login is the account the user has stored;
      * that account is not the operator's own.

    Any doubt refuses. The alternative - acting on a session we cannot name - is what put 25 positions in
    account 250086 on 2026-11-16 under a user's name.
    """
    row = store.get(username)
    if not row:
        return None, {"ok": False, "reason": "NO_ACCOUNT", "detail": "no account is connected"}

    token, err = _token_for(username)
    if err:
        return None, err
    if not token:
        return None, {"ok": False, "reason": "NO_USER_SESSION", "detail": "no session for this user"}

    got = _cached_entry(username)
    session_login = (got[1] if got else "") or ""
    creds = store.secret_of(username)
    stored_login = (creds[0] if creds else "") or ""
    if not session_login or not stored_login:
        forget(username)
        return None, {"ok": False, "reason": "SESSION_UNVERIFIED",
                      "detail": "cannot confirm which account this session belongs to"}
    try:
        same = int(str(session_login).strip()) == int(str(stored_login).strip())
    except (TypeError, ValueError):
        same = str(session_login).strip() == str(stored_login).strip()
    if not same:
        forget(username)                       # the account changed under us: never reuse the old session
        return None, {"ok": False, "reason": "SESSION_ACCOUNT_MISMATCH",
                      "detail": "the open session is not the account now stored for you"}
    if is_operator_account(stored_login, creds[1] if creds else ""):
        return None, {"ok": False, "reason": "OPERATOR_ACCOUNT",
                      "detail": "this is the account the service trades itself; it cannot also be copied into"}
    return token, None
