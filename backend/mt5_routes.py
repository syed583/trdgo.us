"""MT5 auto-trading routes: operator only (the admin role, or the username in env MT5_OPERATOR). The password is read from
the request body once, handed to the bridge, and never stored in a response, a log line or the audit file."""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, HTTPException, Request

import mt5_autotrade as AT

router = APIRouter(prefix="/api/mt5", tags=["mt5"])


def is_operator(user: dict | None) -> bool:
    if not user:
        return False
    if user.get("role") in ("admin", "super_admin"):
        return True
    op = (os.getenv("MT5_OPERATOR") or "").strip().lower()
    return bool(op) and (user.get("username") or "").strip().lower() == op


def _require_operator(request: Request) -> dict:
    import auth_service as auth
    user = auth.current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not is_operator(user):
        raise HTTPException(status_code=403, detail="MT5 auto-trading is for the operator account only.")
    return user


@router.get("/status")
def mt5_status(request: Request) -> dict:
    _require_operator(request)
    return {"status": "OK", **AT.status(), "open_positions": list(AT.open_positions().values())}


@router.post("/connect")
def mt5_connect(request: Request, payload: dict = Body(...)) -> dict:
    _require_operator(request)
    try:
        login = int(str(payload.get("login") or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="Account ID must be a number.")
    password = str(payload.get("password") or "")
    server = str(payload.get("server") or AT.DEFAULT_SERVER).strip() or AT.DEFAULT_SERVER
    if not password:
        raise HTTPException(status_code=422, detail="Password is required.")
    base_url = str(payload.get("base_url") or "").strip() or None
    res = AT.connect(login, password, server, remember=bool(payload.get("remember", True)), base_url=base_url)
    return {"status": "OK" if res.get("ok") else "FAILED", **{k: v for k, v in res.items() if k != "password"}, **AT.status()}


@router.post("/reconnect")
def mt5_reconnect(request: Request) -> dict:
    _require_operator(request)
    res = AT.reconnect_from_saved()
    return {"status": "OK" if res.get("ok") else "FAILED", **res, **AT.status()}


@router.post("/disconnect")
def mt5_disconnect(request: Request, payload: dict = Body(default={})) -> dict:
    _require_operator(request)
    res = AT.disconnect(forget=bool(payload.get("forget")))
    return {"status": "OK" if res.get("ok") else "FAILED", **res, **AT.status()}


@router.post("/pause")
def mt5_pause(request: Request, payload: dict = Body(default={})) -> dict:
    user = _require_operator(request)
    AT.set_paused(bool(payload.get("paused", True)), who=user.get("username") or "operator")
    return {"status": "OK", "paused": AT.paused()}


@router.get("/symbols")
def mt5_symbols(request: Request) -> dict:
    _require_operator(request)
    return {"status": "OK", **AT.mapping_rows(), "connected": AT.is_connected()}        # in-memory only: no broker call, no database query per poll


@router.post("/symbols/map")
def mt5_symbols_map(request: Request, payload: dict = Body(...)) -> dict:
    _require_operator(request)
    sym = str(payload.get("symbol") or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")
    res = AT.set_mapping(sym, payload.get("broker_symbol"), source="manual")
    return {**res, "map_status": res.get("status"), "status": "OK"}


@router.post("/symbols/automap")
def mt5_symbols_automap(request: Request) -> dict:
    _require_operator(request)
    res = AT.start_automap()
    return {"status": "OK" if res.get("ok") else "FAILED", **res}


@router.post("/symbols/verify")
def mt5_symbols_verify(request: Request, payload: dict = Body(...)) -> dict:
    _require_operator(request)
    return {"status": "OK", **AT.verify_broker_symbol(str(payload.get("broker_symbol") or "").strip())}


@router.get("/audit")
def mt5_audit(request: Request, limit: int = 200, event: str = "", symbol: str = "") -> dict:
    _require_operator(request)
    return {"status": "OK", "rows": AT.audit_rows(limit=limit, event=event or None, symbol=(symbol or "").upper() or None), "auto_trade": AT.auto_trade_enabled(), "paused": AT.paused(), "connected": AT.is_connected()}


@router.post("/reconcile")
def mt5_reconcile(request: Request) -> dict:
    _require_operator(request)
    res = AT.reconcile()
    return {"status": "OK" if res.get("ok") else "FAILED", **res}
