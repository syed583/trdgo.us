"""C.1: the routes behind a user's own MT5 connection.

Every route acts on the SIGNED-IN user and no one else: the username comes from the session, never from the
request body or a path parameter, so there is no identifier for a caller to change in order to reach another
person's account.

C.1 places no orders. The only writes are: save my account, set my kill switch, set my size, disconnect me.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request

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


@router.get("")
def my_mt5(request: Request) -> dict:
    """What the Settings tab renders. No secret, no full account number."""
    return dict(svc.status(_me(request)), risk_notice=RISK_NOTICE)


@router.post("/connect")
def connect(request: Request, payload: dict = Body(...)) -> dict:
    """Store my account, then test it once. `consent` must be true: it records that the user read the notice."""
    me = _me(request)
    saved = store.save(
        me,
        login=str(payload.get("login") or "").strip(),
        server=str(payload.get("server") or "").strip(),
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
    """The user's own kill switch. In C.1 it only records the choice - nothing acts on it yet."""
    return store.set_enabled(_me(request), bool(payload.get("enabled")))


@router.post("/size")
def set_size(request: Request, payload: dict = Body(...)) -> dict:
    try:
        shares = int(payload.get("max_shares"))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "BAD_VALUE", "detail": "max_shares must be a whole number"}
    return store.set_max_shares(_me(request), shares)


@router.post("/disconnect")
def disconnect(request: Request) -> dict:
    """Forget the account and the password outright."""
    me = _me(request)
    svc.forget(me)
    return store.disconnect(me)


def included_in(app, log: Optional[object] = None) -> None:
    app.include_router(router)
