"""FastAPI wrapper for the lease broker. NOT mounted by main.py: deploying it, choosing the host that runs it and setting UW_BROKER_SECRET are separate owner decisions.
Mount (when approved):  app.include_router(uw_lease_broker_routes.router)  on exactly ONE host that every machine can reach."""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from uw_lease_broker import LeaseTable, handle

router = APIRouter(prefix="/internal/uw-lease", tags=["uw-lease"])
TABLE = LeaseTable(max_leases=3)


async def _go(request: Request, method: str, path: str):
    try:
        body = await request.json() if method == "POST" else {}
    except Exception:  # noqa: BLE001
        body = {}
    code, out = handle(TABLE, os.environ.get("UW_BROKER_SECRET", ""), method, path, request.headers.get("X-Broker-Secret", ""), body)
    return JSONResponse(out, status_code=code)


@router.post("/acquire")
async def acquire(request: Request):
    return await _go(request, "POST", "/acquire")


@router.post("/release")
async def release(request: Request):
    return await _go(request, "POST", "/release")


@router.get("/status")
async def status(request: Request):
    return await _go(request, "GET", "/status")
