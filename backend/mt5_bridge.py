"""Adapter over the mtapi.io MT5 Client RESTful API (cloud; no local terminal, no MetaTrader5 package).

Owner's API notes (2026-11-11): base https://mt5.mtapi.io/, every call GET with the session token as query param ``id``;
Connect / ConnectEx(user, password, server) returns the token; AccountSummary; Symbols; SymbolParams(symbol); GetQuote(symbol);
OrderSendSafe(symbol, operation 0=Buy 1=Sell, volume in LOTS, stoploss, takeprofit, slippage, comment, expertID);
OrderCloseSafe(ticket); OpenedOrders; HistoryDealsByPositionId(ticket); errors come back as HTTP 201 with a JSON
ExceptionResult {errorCode, message, details}.

Every function returns a plain dict with ``ok``; a provider refusal is a result, never an exception. The token appears only in
the query string of the outgoing request: it is never logged, never put into a result, and the audit sees paths only. Field
names in the responses are read tolerantly (several candidate keys, case-insensitive) because the owner's note gives the
semantics, not the exact JSON keys; ``raw`` is kept on failures so the first demo session shows what the API really sends."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

_log = logging.getLogger("mt5")
_lock = threading.RLock()

DEFAULT_BASE_URL = "https://mt5.mtapi.io/"
TIMEOUT_S = 25
_session: dict = {"base_url": DEFAULT_BASE_URL, "token": None, "login": None, "server": None}

OP_BUY, OP_SELL = 0, 1
SYMBOL_TRADE_MODE_DISABLED, SYMBOL_TRADE_MODE_LONGONLY, SYMBOL_TRADE_MODE_SHORTONLY, SYMBOL_TRADE_MODE_CLOSEONLY, SYMBOL_TRADE_MODE_FULL = 0, 1, 2, 3, 4
# refusal classes (by the API's errorCode / message text, which follow the MT5 trade return codes where they come from the server)
NO_RETRY_WORDS = ("invalid volume", "invalid price", "invalid stops", "trade disabled", "market closed", "not enough money", "no money",
                  "invalid fill", "only real", "limit", "long only", "short only", "close only", "hedge prohibited", "unknown symbol", "not found", "frozen")
REPRICE_WORDS = ("requote", "price changed", "off quotes", "price off")
RECONCILE_WORDS = ("timeout", "no connection", "too many requests", "locked", "busy", "network")


def available() -> bool:
    """A cloud API needs no local package: the bridge is always available; connectivity is a connect-time question."""
    return True


def not_installed() -> dict:
    return {"ok": False, "reason": "NOT_AVAILABLE", "detail": "mtapi.io bridge is always available; connect first"}


def _reset_for_tests(getter=None) -> None:
    """Tests replace the HTTP getter; production never calls this."""
    global _http_get
    _http_get = getter or _real_http_get
    _session.update(token=None, login=None, server=None)


# ----------------------------------------------------------------------------- HTTP
def _real_http_get(url: str, timeout: float = TIMEOUT_S) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "TRDGO-US mt5 bridge"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace") if e.fp else ""


_http_get = _real_http_get


def _call(path: str, params: Optional[dict] = None, *, with_token: bool = True, base_url: Optional[str] = None) -> dict:
    """One GET. Returns {"ok", "status", "data"|"error", "path"}; the token never leaves this function."""
    base = (base_url or _session["base_url"] or DEFAULT_BASE_URL).rstrip("/") + "/"
    q = dict(params or {})
    if with_token:
        if not _session.get("token"):
            return {"ok": False, "reason": "NOT_CONNECTED", "detail": "no session token: connect first", "path": path}
        q["id"] = _session["token"]
    url = base + path.lstrip("/") + ("?" + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None}) if q else "")
    safe_path = path + ("?" + urllib.parse.urlencode({k: ("***" if k in ("id", "password") else v) for k, v in q.items() if v is not None}) if q else "")
    try:
        status, body = _http_get(url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "TRANSPORT", "detail": "%s: %s" % (type(exc).__name__, exc), "path": safe_path}
    data: Any
    try:
        data = json.loads(body) if body.strip() else None
    except ValueError:
        data = body.strip().strip('"')
    if isinstance(data, dict) and ("errorCode" in data or ("message" in data and any(k in data for k in ("code", "nodeName", "stackTrace", "details")))):
        code = data.get("code", data.get("errorCode"))
        extra = data.get("details") or data.get("nodeName")
        return {"ok": False, "reason": "API_ERROR", "status": status, "error_code": code, "detail": "%s%s" % (data.get("message"), (" (" + str(extra) + ")") if extra else ""),
                "raw": {k: v for k, v in data.items() if k != "stackTrace"}, "path": safe_path}
    if status >= 400:
        return {"ok": False, "reason": "HTTP_%d" % status, "status": status, "detail": (body or "")[:300], "path": safe_path}
    return {"ok": True, "status": status, "data": data, "path": safe_path}


def _g(d: Any, *names, default=None):
    """Tolerant field read: first present key among `names`, case-insensitive."""
    if not isinstance(d, dict):
        return default
    low = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if n in d:
            return d[n]
        if n.lower() in low:
            return low[n.lower()]
    return default


def _f(x, default=None):
    try:
        return float(x) if x is not None and x != "" else default
    except (TypeError, ValueError):
        return default


def classify_refusal(res: dict) -> str:
    """NO_RETRY / REPRICE / RECONCILE / OTHER from the API's error text."""
    text = ("%s %s %s" % (res.get("error_code"), res.get("detail"), res.get("reason"))).lower()
    if any(w in text for w in REPRICE_WORDS):
        return "REPRICE"
    if any(w in text for w in NO_RETRY_WORDS):
        return "NO_RETRY"
    if any(w in text for w in RECONCILE_WORDS):
        return "RECONCILE"
    return "OTHER"


# ----------------------------------------------------------------------------- session
def connect(login: int, password: str, server: str, base_url: Optional[str] = None, **_ignored) -> dict:
    """ConnectEx(user, password, server) -> session token (kept in memory only)."""
    with _lock:
        base = (base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        _session.update(base_url=base, token=None, login=None, server=None)
        res = _call("ConnectEx", {"user": int(login), "password": password, "server": server}, with_token=False, base_url=base)
        if not res.get("ok"):
            return {"ok": False, "reason": res.get("reason"), "detail": res.get("detail"), "error_code": res.get("error_code"), "path": res.get("path")}
        tok = res.get("data")
        if isinstance(tok, dict):
            tok = _g(tok, "id", "token", "sessionId", "session")
        if not tok or not isinstance(tok, str):
            return {"ok": False, "reason": "NO_TOKEN", "detail": "ConnectEx answered without a session token", "raw": res.get("data")}
        _session.update(token=tok, login=int(login), server=server)
        acct = account()
        if not acct.get("ok"):
            _session["token"] = None
            return {"ok": False, "reason": "ACCOUNT_SUMMARY_FAILED", "detail": acct.get("detail")}
        return {"ok": True, "account": acct["account"]}


def disconnect() -> dict:
    with _lock:
        if _session.get("token"):
            _call("Disconnect")                                             # best effort; the token is dropped regardless
        _session.update(token=None, login=None, server=None)
    return {"ok": True}


def connected() -> bool:
    return bool(_session.get("token"))


def account() -> dict:
    res = _call("AccountSummary")
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason"), "detail": res.get("detail")}
    d = res["data"] if isinstance(res["data"], dict) else {}
    kind = _g(d, "type", "accountType", "tradeMode", "isDemo")
    return {"ok": True, "account": {"login": _session.get("login") or _g(d, "login", "user", "account"), "server": _session.get("server") or _g(d, "server"),
                                    "name": _g(d, "name", "userName"), "company": _g(d, "company"), "currency": _g(d, "currency"),
                                    "balance": _f(_g(d, "balance")), "equity": _f(_g(d, "equity")), "margin": _f(_g(d, "margin")), "margin_free": _f(_g(d, "freeMargin", "marginFree")),
                                    "leverage": _g(d, "leverage"), "account_kind": ("DEMO" if (kind is True or str(kind).lower() in ("demo", "0", "true")) else "REAL" if kind is not None else None), "raw_keys": sorted(d.keys())[:40]}}


def terminal() -> dict:
    return {"ok": True, "terminal": {"name": "mtapi.io cloud", "connected": connected(), "base_url": _session.get("base_url"), "server": _session.get("server")}}


# ----------------------------------------------------------------------------- symbols and prices
def symbol(sym: str, select: bool = True) -> dict:
    res = _call("SymbolParams", {"symbol": sym})
    if not res.get("ok"):
        text = ("%s" % res.get("detail")).lower()
        return {"ok": False, "reason": "UNKNOWN_SYMBOL" if ("not found" in text or "unknown" in text or "invalid symbol" in text) else res.get("reason"), "detail": res.get("detail"), "raw": res.get("raw")}
    d = res["data"] if isinstance(res["data"], dict) else {}
    # swagger: SymbolParams = {symbol, symbolInfo, symbolGroup}; symbolInfo carries contractSize/digits/tickSize, symbolGroup the
    # trading rules (tradeMode, minLots/maxLots/lotsStep or minVolume/maxVolume/volumeStep). Flat shapes are accepted too.
    info_part = _g(d, "symbolInfo", "info", "params")
    group_part = _g(d, "symbolGroup", "group")
    inner = dict(d)
    if isinstance(info_part, dict):
        inner.update(info_part)
    if isinstance(group_part, dict):
        inner.update(group_part)
    tm = _g(inner, "tradeMode", "trade_mode", "tradeMod")
    if isinstance(tm, str):
        tm = {"disabled": 0, "longonly": 1, "shortonly": 2, "closeonly": 3, "full": 4}.get(tm.replace("_", "").lower(), tm)
    contract = _f(_g(inner, "contractSize", "contract_size", "tradeContractSize", "lotSize"), 1.0) or 1.0
    out = {"ok": True, "symbol": sym, "description": _g(inner, "description"), "visible": True, "trade_mode": tm,
           "trade_mode_text": {0: "DISABLED", 1: "LONG_ONLY", 2: "SHORT_ONLY", 3: "CLOSE_ONLY", 4: "FULL"}.get(tm, str(tm)),
           "volume_min": _f(_g(inner, "minLots", "volumeMin", "minVolume", "volume_min", "lotMin", "minLot")), "volume_max": _f(_g(inner, "maxLots", "volumeMax", "maxVolume", "volume_max", "lotMax", "maxLot")),
           "volume_step": _f(_g(inner, "lotsStep", "volumeStep", "stepVolume", "volume_step", "lotStep", "stepLot")), "digits": _g(inner, "digits"), "point": _f(_g(inner, "point", "points")),
           "tick_size": _f(_g(inner, "tickSize", "tick_size")), "contract_size": contract, "stops_level": _g(inner, "stopsLevel", "tradeStopsLevel"),
           "currency_profit": _g(inner, "currencyProfit", "profitCurrency"), "raw_keys": sorted(inner.keys())[:80] if isinstance(inner, dict) else None, "group": _g(inner, "groupName")}
    if out["volume_min"] is None or out["volume_step"] is None:
        out["note"] = "volume rules not found in SymbolParams (keys: %s)" % out["raw_keys"]
    return out


def tick(sym: str) -> dict:
    res = _call("GetQuote", {"symbol": sym})
    if not res.get("ok"):
        return {"ok": False, "reason": "NO_TICK", "detail": res.get("detail")}
    d = res["data"] if isinstance(res["data"], dict) else {}
    bid, ask = _f(_g(d, "bid")), _f(_g(d, "ask"))
    if not bid or not ask:
        return {"ok": False, "reason": "NO_TICK", "detail": "no bid/ask for %s (keys: %s)" % (sym, sorted(d.keys())[:20])}
    t = _g(d, "time", "timestamp", "serverTime")
    age = None
    try:
        if isinstance(t, (int, float)):
            age = time.time() - (float(t) / 1000.0 if float(t) > 1e11 else float(t))
        elif isinstance(t, str) and t:
            import datetime as _dt
            ts = _dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_dt.timezone.utc)                 # the API stamps server time as UTC
            age = time.time() - ts.timestamp()
    except (TypeError, ValueError):
        age = None
    return {"ok": True, "bid": bid, "ask": ask, "last": _f(_g(d, "last")), "time": t, "age_s": age}


# ----------------------------------------------------------------------------- orders
def _order_fields(d: Any) -> dict:
    if isinstance(d, (int, float)) or (isinstance(d, str) and d.isdigit()):
        return {"ticket": int(d)}
    if not isinstance(d, dict):
        return {}
    return {"ticket": _g(d, "ticket", "orderTicket", "positionId", "order"), "volume": _f(_g(d, "lots", "volume")), "price": _f(_g(d, "openPrice", "price", "priceOpen")),
            "sl": _f(_g(d, "stopLoss", "sl")), "tp": _f(_g(d, "takeProfit", "tp")), "symbol": _g(d, "symbol"), "comment": _g(d, "comment"), "magic": _g(d, "expertId", "expertID", "magic"),
            "type": _g(d, "orderType", "type"), "profit": _f(_g(d, "profit")), "time": _g(d, "openTime", "time")}


def market_order(sym: str, side: str, volume: float, *, sl: Optional[float], tp: Optional[float], deviation_points: int, magic: int, comment: str,
                 sym_info: Optional[dict] = None, check_only: bool = False) -> dict:
    """OrderSendSafe: ``volume`` is already in LOTS (the policy converts shares with the contract size)."""
    info = sym_info or symbol(sym)
    if not info.get("ok"):
        return info
    t = tick(sym)
    if not t.get("ok"):
        return t
    buy = side.upper() in ("BUY", "LONG")
    req = {"symbol": sym, "operation": OP_BUY if buy else OP_SELL, "volume": float(volume), "slippage": int(deviation_points), "comment": comment[:31], "expertID": int(magic)}
    digits = int(info.get("digits") or 2)
    if sl is not None:
        req["stoploss"] = round(float(sl), digits)
    if tp is not None:
        req["takeprofit"] = round(float(tp), digits)
    if check_only:
        return {"ok": True, "request": req, "tick": t, "note": "mtapi.io has no order_check; validation is local"}
    res = _call("OrderSendSafe", req)
    if not res.get("ok"):
        return {"ok": False, "stage": "order_send", "request": req, "tick": t, "retcode": res.get("error_code"), "retcode_text": classify_refusal(res), "comment": res.get("detail"), "raw": res.get("raw")}
    o = _order_fields(res.get("data"))
    if not o.get("ticket"):
        return {"ok": False, "stage": "order_send", "request": req, "tick": t, "retcode": None, "retcode_text": "NO_TICKET", "comment": "OrderSendSafe answered without a ticket", "raw": res.get("data")}
    filled = o.get("volume")
    partial = filled is not None and filled < float(volume) - 1e-9
    return {"ok": True, "stage": "order_send", "request": req, "tick": t, "retcode": 0, "retcode_text": "DONE_PARTIAL" if partial else "DONE", "partial": partial,
            "order": o["ticket"], "deal": o["ticket"], "volume": filled if filled is not None else float(volume), "price": o.get("price") or (t["ask"] if buy else t["bid"]), "comment": o.get("comment")}


def positions(sym: Optional[str] = None, magic: Optional[int] = None) -> dict:
    res = _call("OpenedOrders")
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason"), "detail": res.get("detail")}
    rows = res["data"] if isinstance(res["data"], list) else (_g(res["data"], "orders", "positions", "items") or [])
    out = []
    for d in rows or []:
        o = _order_fields(d)
        if sym and o.get("symbol") != sym:
            continue
        if magic is not None and (o.get("magic") is None or int(o["magic"]) != int(magic)):
            continue
        ty = o.get("type")
        side = "BUY" if (ty in (0, "0", "Buy", "BUY", "ORDER_TYPE_BUY", "buy")) else "SELL" if (ty in (1, "1", "Sell", "SELL", "ORDER_TYPE_SELL", "sell")) else str(ty)
        out.append({"ticket": o.get("ticket"), "symbol": o.get("symbol"), "type": side, "volume": o.get("volume"), "price_open": o.get("price"), "sl": o.get("sl"), "tp": o.get("tp"),
                    "profit": o.get("profit"), "magic": o.get("magic"), "comment": o.get("comment"), "time": o.get("time")})
    return {"ok": True, "positions": out}


def close_position(ticket: int, *, deviation_points: int, magic: int, comment: str) -> dict:
    """OrderCloseSafe(ticket). A ticket that is no longer open counts as already closed."""
    live = positions()
    if live.get("ok") and not any(int(p.get("ticket") or 0) == int(ticket) for p in live["positions"]):
        return {"ok": True, "already_closed": True, "ticket": int(ticket)}
    res = _call("OrderCloseSafe", {"ticket": int(ticket), "slippage": int(deviation_points)})
    if not res.get("ok"):
        text = ("%s" % res.get("detail")).lower()
        if "not found" in text or "already closed" in text or "position closed" in text:
            return {"ok": True, "already_closed": True, "ticket": int(ticket)}
        return {"ok": False, "stage": "close", "ticket": int(ticket), "retcode": res.get("error_code"), "retcode_text": classify_refusal(res), "comment": res.get("detail"), "raw": res.get("raw")}
    o = _order_fields(res.get("data"))
    return {"ok": True, "stage": "close", "ticket": int(ticket), "retcode": 0, "retcode_text": "DONE", "volume": o.get("volume"), "price": o.get("price") or _f(_g(res.get("data"), "closePrice")), "partial": False}


def position_exit_deals(ticket: int, since_epoch: float) -> dict:
    res = _call("HistoryDealsByPositionId", {"ticket": int(ticket)})
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason"), "detail": res.get("detail")}
    rows = res["data"] if isinstance(res["data"], list) else (_g(res["data"], "deals", "items") or [])
    out = []
    for d in rows or []:
        direction = _g(d, "direction", "entry", "dealEntry")
        comment = str(_g(d, "comment") or "")
        cl = comment.lower()
        is_exit = direction in (1, 2, 3, "1", "2", "3", "Out", "OUT", "InOut", "OutBy", "out") or "[sl" in cl or "[tp" in cl or cl.startswith("so:") or "stop out" in cl
        if not is_exit:
            continue
        reason = _g(d, "reason", "dealReason")
        rtxt = str(reason).lower() if reason is not None else ""
        why = "SL" if ("[sl" in cl or reason in (4, "4") or "stop loss" in rtxt or "stoploss" in rtxt) else "TP" if ("[tp" in cl or reason in (5, "5") or "take profit" in rtxt or "takeprofit" in rtxt) \
            else "STOP_OUT" if (cl.startswith("so:") or "stop out" in cl or reason in (6, "6")) else "CLIENT"
        out.append({"deal": _g(d, "ticketNumber", "ticket", "deal"), "time": _g(d, "openTime", "historyTime", "time"), "price": _f(_g(d, "price", "openPrice")),
                    "volume": _f(_g(d, "lots", "volume")), "profit": _f(_g(d, "profit")), "reason": why, "comment": comment})
    return {"ok": True, "deals": out}


def symbols_list() -> dict:
    res = _call("Symbols")
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason"), "detail": res.get("detail")}
    rows = res["data"] if isinstance(res["data"], list) else (_g(res["data"], "symbols", "items") or [])
    return {"ok": True, "symbols": [s if isinstance(s, str) else _g(s, "symbol", "name") for s in rows]}
