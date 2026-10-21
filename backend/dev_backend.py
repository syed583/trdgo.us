"""Isolated DEVELOPMENT-ONLY backend for Institutional Intelligence (Phase 6.1).

This entry point physically cannot reach production:

* It NEVER imports ``database``, ``main`` or ``api_routes`` — those construct the
  production SQLAlchemy engine at import time from the prod ``.env``. The only
  database it can touch is the approved TRDGO-US-DEV project, through
  ``institutional_dashboard_service``'s own guarded engine.
* ``_require_dev_config()`` enforces ``TRDGO_ENV=development``, reads the dev DB
  URL ONLY from ``INSTITUTIONAL_DATABASE_URL`` / ``.env.dev`` (never the main
  ``.env``'s ``DATABASE_URL``), refuses the production project ref, rejects
  missing/malformed config, and requires SSL — before any app object is built.
* It starts NO warmers, NO scheduled jobs and NO background workers, so nothing
  can open a production connection behind the app's back.
* It reuses the real session middleware (``auth_service``), so the institutional
  endpoints carry the SAME authentication as the production app. Auth is never
  weakened.

Run:  TRDGO_ENV=development uvicorn dev_backend:app --host 127.0.0.1 --port 8098
"""
from __future__ import annotations

from dev_config import DEV_REF as _DEV_REF, require_dev_config

# Validate BEFORE importing anything that could build an engine.
_DEV_URL = require_dev_config()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pathlib import Path  # noqa: E402

import auth_service as auth  # noqa: E402  (stdlib + fastapi only; no DB at import)
import institutional_dashboard_service as ins  # noqa: E402  (own guarded dev engine)
import swr  # noqa: E402

app = FastAPI(title="TRDGO Institutional Intelligence — ISOLATED DEV backend")

_SPA = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"


# --------------------------------------------------------------------------- #
# Session gate: every /api request needs a valid session (same rule as prod).
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def _session_gate(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/") and auth.enabled()
            and request.method not in ("OPTIONS",)
            and not auth.is_public_path(path)):
        try:
            auth.require_session(request)
        except HTTPException:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


def _me_payload(user: dict) -> dict:
    role = user["role"]
    is_staff = auth.is_staff(role)
    return {"authenticated": True, "username": user["username"], "display_name": None,
            "role": role, "is_admin": is_staff, "full_access": is_staff,
            "permissions": auth.permissions_for(role)}


# --------------------------------------------------------------------------- #
# Auth (admin / ACCESS_PASSWORD path only — no DB, no OTP). Not weakened:
# a wrong or missing password still fails, every /api stays gated.
# --------------------------------------------------------------------------- #
@app.get("/auth/status")
def auth_status() -> dict:
    return {"required": auth.enabled()}


@app.get("/auth/me")
def auth_me(request: Request):
    user = auth.current_user(request)
    if not user:
        return {"authenticated": False}
    return _me_payload(user)


@app.post("/auth/login")
async def auth_login(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    user = auth.authenticate(str(body.get("username") or "admin"), str(body.get("password") or ""))
    if not user:
        return JSONResponse({"detail": "Incorrect username or password"}, status_code=401)
    resp = JSONResponse(_me_payload(user))
    resp.set_cookie(auth.COOKIE_NAME, auth.issue_token(user["username"], user["role"]),
                    httponly=True, samesite="lax", path="/")
    return resp


@app.post("/auth/logout")
def auth_logout():
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


# --------------------------------------------------------------------------- #
# Institutional Intelligence (read-only, dev engine). Identical paths to the
# production router so the real frontend works unchanged.
# --------------------------------------------------------------------------- #
def _guard():
    try:
        if ins.available():
            return None
    except Exception:  # noqa: BLE001
        pass
    return {"status": "UNAVAILABLE", "available": False,
            "detail": "Institutional Intelligence (SEC 13F) dev dataset is not configured.",
            "source": ins.SOURCE}


@app.get("/api/institutional-intel/health")
def i_health():
    try:
        return {"available": ins.available(), "source": ins.SOURCE, "disclosure": ins.DISCLOSURE}
    except Exception:  # noqa: BLE001
        return {"available": False, "source": ins.SOURCE}


@app.get("/api/institutional-intel/overview")
def i_overview():
    return _guard() or swr.serve("dev:inst:overview", lambda: ins.overview(), 300.0)


@app.get("/api/institutional-intel/stock")
def i_stock(ticker: str, as_of: str = ""):
    g = _guard()
    if g:
        return g
    tk = (ticker or "").upper().strip()[:12]
    return swr.serve(f"dev:inst:stock:{tk}:{as_of}", lambda: ins.stock(tk, as_of or None), 300.0)


@app.get("/api/institutional-intel/rankings")
def i_rankings(kind: str = "accumulation", limit: int = 20, offset: int = 0):
    return _guard() or swr.serve(f"dev:inst:rank:{kind}:{limit}:{offset}",
                                 lambda: ins.rankings(kind, limit, offset), 300.0)


@app.get("/api/institutional-intel/managers")
def i_managers(q: str = "", quarter: str = "2026-Q2", limit: int = 25, offset: int = 0):
    return _guard() or swr.serve(f"dev:inst:mgr:{q}:{quarter}:{limit}:{offset}",
                                 lambda: ins.managers(q, quarter, limit, offset), 300.0)


@app.get("/api/institutional-intel/history")
def i_history(ticker: str):
    g = _guard()
    if g:
        return g
    tk = (ticker or "").upper().strip()[:12]
    return swr.serve(f"dev:inst:hist:{tk}", lambda: ins.history(tk), 300.0)


@app.get("/api/institutional-intel/filing-metadata")
def i_filing():
    return _guard() or swr.serve("dev:inst:filing", lambda: ins.filing_metadata(), 300.0)


@app.get("/api/institutional-intel/mapping")
def i_mapping(confidence: str = "", limit: int = 100, offset: int = 0):
    return _guard() or swr.serve(f"dev:inst:map:{confidence}:{limit}:{offset}",
                                 lambda: ins.mapping(confidence, limit, offset), 300.0)


# --------------------------------------------------------------------------- #
# Optionally serve the built SPA (only if a production build exists). Dev review
# normally runs Vite against this backend instead.
# --------------------------------------------------------------------------- #
@app.get("/")
def _root():
    if _SPA.is_file():
        return FileResponse(_SPA)
    return {"status": "OK", "service": "isolated dev backend",
            "institutional_available": ins.available(), "dev_ref": _DEV_REF}
