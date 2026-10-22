"""Real VIX index level from Interactive Brokers (read-only market data).

Uses the IBKR Client Portal Web API through a locally running gateway
(IBKR_CPAPI_URL, e.g. https://localhost:5000/v1/api, already logged in). Only two
read-only GETs are made -- the session check and one market-data snapshot for the
CBOE VIX index (conid 13455763, field 31 = last). No orders, no account or position
endpoints, no credentials handled here.

Returns None -- never a made-up number -- when the gateway is not configured, not
authenticated, or has no VIX subscription; the caller then falls back, labelled.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from typing import Optional

VIX_CONID = 13455763           # CBOE VIX index
_TTL = 60.0
_cache: dict = {"at": 0.0, "val": None}


def configured() -> bool:
    return bool((os.environ.get("IBKR_CPAPI_URL") or "").strip())


def _get(path: str, timeout: float = 4.0):
    base = os.environ["IBKR_CPAPI_URL"].strip().rstrip("/")
    ctx = None
    if base.startswith("https://localhost") or base.startswith("https://127.0.0.1"):
        ctx = ssl.create_default_context()           # the local gateway uses a self-signed cert
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(base + path, headers={"User-Agent": "TradeGo-VIX/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "ignore") or "null")


def get_vix() -> Optional[dict]:
    """{"value": float, "source": "IBKR VIX index"} or None."""
    if not configured():
        return None
    now = time.time()
    if now - _cache["at"] < _TTL:
        return _cache["val"]
    out: Optional[dict] = None
    try:
        auth = _get("/iserver/auth/status")
        if isinstance(auth, dict) and auth.get("authenticated"):
            # The first snapshot call subscribes; the second returns the values.
            for _ in range(2):
                snap = _get(f"/iserver/marketdata/snapshot?conids={VIX_CONID}&fields=31")
                row = snap[0] if isinstance(snap, list) and snap else {}
                raw = str(row.get("31") or "").lstrip("CH")      # a C/H prefix marks closed/halted
                try:
                    v = float(raw)
                except ValueError:
                    v = None
                if v and v > 0:
                    out = {"value": v, "source": "IBKR VIX index"}
                    break
                time.sleep(0.8)
    except Exception:  # noqa: BLE001 - any gateway problem means "no IBKR value"
        out = None
    _cache["at"], _cache["val"] = now, out
    return out
