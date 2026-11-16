"""C.1 / C.2: the routes behind a user's own MT5 connection and the live copying of our trades into it.

Every route acts on the SIGNED-IN user and no one else: the username comes from the session, never from the
request body or a path parameter, so there is no identifier for a caller to change in order to reach another
person's account.

C.1 placed no orders. C.2 does: once the user has connected, consented and switched Auto-copy ON, our intraday
entries and exits are mirrored into their account. The switches live here; the ordering lives in
user_copy_service, off the request thread.

The admin routes at the end are the operator's view: who is copying, and a pause for one user or for everyone.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request

import model_privacy as _privacy
import user_copy_service as copysvc
import user_mt5_service as svc
import user_mt5_store as store

router = APIRouter(prefix="/api/me/mt5", tags=["user-mt5"])

RISK_NOTICE = (
    "Connecting your MetaTrader account is voluntary. You stay responsible for your own account and for every "
    "profit or loss in it. Trading carries risk and you can lose money. Nothing here is financial advice."
)


def _me(request: Request) -> str:
    """The signed-in user, or 401. The only source of identity in this module."""
    import auth_service as auth
    user = auth.current_user(request)
    if not user or not user.get("username"):
        raise HTTPException(status_code=401, detail="Sign in to connect an account.")
    return str(user["username"])


COPY_NOTICE = (
    "With Auto-trade on, orders are placed in your own account automatically - the same stocks, side, stop and "
    "target our system trades, sized to the share count you set. They are real orders at your broker. You can "
    "switch Auto-trade off at any time; positions already open are closed by the same rules, including the "
    "end-of-day close at 15:45 New York time."
)


@router.get("")
def my_mt5(request: Request) -> dict:
    """What the My Broker page renders. No secret, no full account number."""
    me = _me(request)
    out = dict(svc.status(me), risk_notice=RISK_NOTICE, copy_notice=COPY_NOTICE)
    out["copy_trading"] = copysvc.user_status(me)
    return out


@router.post("/connect")
def connect(request: Request, payload: dict = Body(...)) -> dict:
    """Store my account, then test it once. `consent` must be true: it records that the user read the notice."""
    me = _me(request)
    login = str(payload.get("login") or "").strip()
    server = str(payload.get("server") or "").strip()
    # C.2a: not the account our own engine trades. Both sides would place orders into it, the positions would
    # be indistinguishable, and our 15:45 sweep would close theirs. Checked before anything is stored.
    if svc.is_operator_account(login, server):
        return {"ok": False, "reason": "OPERATOR_ACCOUNT",
                "detail": "That is the account this service trades itself. Connect a different account - "
                          "copying into the same account would place two sets of orders in it."}
    saved = store.save(
        me,
        login=login,
        server=server,
        password=str(payload.get("password") or ""),
        consent=bool(payload.get("consent")),
    )
    if not saved.get("ok"):
        return saved
    svc.forget(me)                       # never reuse a session opened with older credentials
    checked = svc.verify(me)
    return {"ok": True, "saved": True, "verified": bool(checked.get("ok")),
            "reason": checked.get("reason"), "detail": checked.get("detail"),
            "account": checked.get("account"), "status": svc.status(me)}


@router.post("/test")
def test_connection(request: Request) -> dict:
    me = _me(request)
    svc.forget(me)                       # a test must really reconnect, not read a cached session
    return svc.verify(me)


@router.get("/account")
def my_account(request: Request) -> dict:
    return svc.account(_me(request))


@router.get("/positions")
def my_positions(request: Request) -> dict:
    return svc.positions(_me(request))


@router.post("/enabled")
def set_enabled(request: Request, payload: dict = Body(...)) -> dict:
    """The user's own kill switch. Off means no order is placed for them, whatever Auto-copy says."""
    return store.set_enabled(_me(request), bool(payload.get("enabled")))


@router.post("/size")
def set_size(request: Request, payload: dict = Body(...)) -> dict:
    """The ONE share count used for every stock. `max_shares` is C.1's name for it and still works."""
    raw = payload.get("shares", payload.get("max_shares"))
    return store.update_settings(_me(request), {"shares": raw})


@router.post("/settings")
def set_copy_settings(request: Request, payload: dict = Body(...)) -> dict:
    """shares / max_positions / daily_loss_pct / auto_copy. Only the keys sent are changed.

    Auto-copy can only be switched ON by a user who has connected an account and ticked the consent box: the
    switch is the last step, never the first.
    """
    me = _me(request)
    patch = {k: payload[k] for k in ("shares", "max_positions", "daily_loss_pct", "auto_copy") if k in payload}
    if patch.get("auto_copy"):
        row = store.get(me)
        if not row:
            return {"ok": False, "reason": "NO_ACCOUNT", "detail": "connect an account first"}
        if not row.get("consent_at"):
            return {"ok": False, "reason": "NO_CONSENT", "detail": "accept the risk notice first"}
    return store.update_settings(me, patch)


@router.get("/preview")
def size_preview(request: Request) -> dict:
    """For each stock we trade: my share count and the lot that will be sent at MY broker."""
    return copysvc.preview(_me(request))


@router.get("/orders")
def my_orders(request: Request, limit: int = 100) -> dict:
    """My own copy log: every order placed, skipped or closed, with the reason, slippage and P&L.

    The row says WHICH of our models triggered the trade. B.1 / A.26: a reader who is not an administrator sees
    the Agent label, never the model's name - the same rule Strategy Trades follows.
    """
    rows = store.orders_of(_me(request), limit)
    for r in rows:
        if r.get("strategy"):
            r["strategy"] = _privacy.agent_label(r["strategy"])
        # key="detail": an attribution field, so a vendor OR a model name in our own text is replaced
        r["reason"] = _privacy.scrub_text(r.get("reason"), key="detail")
    return {"ok": True, "orders": rows}


@router.post("/disconnect")
def disconnect(request: Request) -> dict:
    """Forget the account and the password outright."""
    me = _me(request)
    svc.forget(me)
    return store.disconnect(me)


# --------------------------------------------------------------------------- the operator's view
admin_router = APIRouter(prefix="/api/admin/copy-trading", tags=["user-mt5-admin"])


def _operator(request: Request) -> None:
    """Admin only, failing closed. Reuses the MT5 operator check so there is one definition of 'operator'."""
    import mt5_routes
    mt5_routes._require_operator(request)                                    # noqa: SLF001


@admin_router.get("/users")
def copy_users(request: Request) -> dict:
    """Every connected account and whether it is copying. No passwords, no full account numbers."""
    _operator(request)
    rows = []
    for row in store.all_accounts():
        name = row["username"]
        st = copysvc.user_status(name)
        rows.append(dict(row, copying=st["copying"], open_positions=st["open_positions"],
                         realized_today=st["realized_today"]))
    running = bool(copysvc._worker and copysvc._worker.is_alive())               # noqa: SLF001
    return {"ok": True, "users": rows, "queue_depth": copysvc.queue_depth(),
            "worker_running": running,
            "worker_error": (None if running else copysvc.worker_error())}


@admin_router.post("/pause")
def pause_user(request: Request, payload: dict = Body(...)) -> dict:
    """Pause or resume one user, or every user at once when `all` is true. Does not close anything."""
    _operator(request)
    paused = bool(payload.get("paused", True))
    if payload.get("all"):
        return store.set_admin_paused(None, paused)
    name = str(payload.get("username") or "").strip()
    if not name:
        return {"ok": False, "reason": "NO_USER", "detail": "name a user, or send all=true"}
    return store.set_admin_paused(name, paused)


@admin_router.get("/orders")
def user_orders(request: Request, username: str, limit: int = 200) -> dict:
    """One user's copy log, for support."""
    _operator(request)
    return {"ok": True, "username": username, "orders": store.orders_of(username, limit)}


def included_in(app, log: Optional[object] = None) -> None:
    app.include_router(router)
    app.include_router(admin_router)
