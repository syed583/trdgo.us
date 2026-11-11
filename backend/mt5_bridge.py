"""Thin adapter over the official ``MetaTrader5`` Python package (Windows only; needs a running MT5 terminal on the
same machine). Every call goes through one lock (the package is not thread-safe), returns plain dicts, and never raises
for a provider-side refusal: a refusal is a result with ``ok=False`` and a reason, so the caller can log it verbatim.

The package is imported lazily: on a machine without it (this laptop, a VPS before the terminal is installed) the
bridge reports ``available() == False`` and every operation answers NOT_INSTALLED. Nothing here decides *whether* to
trade; see mt5_autotrade.py for the policy (mapping, switch, sizing, retries, audit)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

_log = logging.getLogger("mt5")
_lock = threading.RLock()
_mt5: Any = None
_import_error: Optional[str] = None

# MT5 constants (the package exposes the same names; these fallbacks keep the module importable without it)
TRADE_ACTION_DEAL = 1
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
SYMBOL_FILLING_FOK, SYMBOL_FILLING_IOC = 1, 2
SYMBOL_TRADE_MODE_DISABLED, SYMBOL_TRADE_MODE_LONGONLY, SYMBOL_TRADE_MODE_SHORTONLY, SYMBOL_TRADE_MODE_CLOSEONLY, SYMBOL_TRADE_MODE_FULL = 0, 1, 2, 3, 4
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
DEAL_ENTRY_OUT = 1
DEAL_REASON_SL, DEAL_REASON_TP, DEAL_REASON_SO = 4, 5, 6
RET_DONE, RET_DONE_PARTIAL, RET_PLACED = 10009, 10010, 10008
RET_REQUOTE, RET_PRICE_CHANGED, RET_PRICE_OFF = 10004, 10020, 10021
RET_TIMEOUT, RET_CONNECTION, RET_LOCKED, RET_TOO_MANY = 10012, 10031, 10028, 10024
RETCODE_TEXT = {10004: "REQUOTE", 10006: "REJECT", 10007: "CANCEL", 10008: "PLACED", 10009: "DONE", 10010: "DONE_PARTIAL", 10011: "ERROR",
                10012: "TIMEOUT", 10013: "INVALID", 10014: "INVALID_VOLUME", 10015: "INVALID_PRICE", 10016: "INVALID_STOPS", 10017: "TRADE_DISABLED",
                10018: "MARKET_CLOSED", 10019: "NO_MONEY", 10020: "PRICE_CHANGED", 10021: "PRICE_OFF", 10022: "INVALID_EXPIRATION", 10023: "ORDER_CHANGED",
                10024: "TOO_MANY_REQUESTS", 10025: "NO_CHANGES", 10026: "SERVER_DISABLES_AT", 10027: "CLIENT_DISABLES_AT", 10028: "LOCKED", 10029: "FROZEN",
                10030: "INVALID_FILL", 10031: "CONNECTION", 10032: "ONLY_REAL", 10033: "LIMIT_ORDERS", 10034: "LIMIT_VOLUME", 10035: "INVALID_ORDER",
                10036: "POSITION_CLOSED", 10038: "INVALID_CLOSE_VOLUME", 10039: "CLOSE_ORDER_EXIST", 10040: "LIMIT_POSITIONS", 10041: "REJECT_CANCEL",
                10042: "LONG_ONLY", 10043: "SHORT_ONLY", 10044: "CLOSE_ONLY", 10045: "FIFO_CLOSE", 10046: "HEDGE_PROHIBITED"}
# refusals that must never be retried: the request itself (or the account / market state) is wrong
NO_RETRY = {10013, 10014, 10015, 10016, 10017, 10018, 10019, 10022, 10030, 10032, 10033, 10034, 10035, 10040, 10042, 10043, 10044, 10045, 10046, 10029}
# a price moved under the request: re-price once, then give up
RETRY_ONCE_REPRICE = {10004, 10020}
# the request may or may not have reached the server: reconcile before any resend
RECONCILE_FIRST = {10012, 10031, 10028, 10024, 10006, 10011}


def _load() -> bool:
    global _mt5, _import_error
    if _mt5 is not None:
        return True
    if _import_error is not None:
        return False
    try:
        import MetaTrader5 as mt5  # type: ignore
        _mt5 = mt5
        return True
    except Exception as exc:  # noqa: BLE001 - ImportError on non-Windows / not installed
        _import_error = "%s: %s" % (type(exc).__name__, exc)
        return False


def _reset_for_tests(module=None) -> None:
    """Tests inject a fake module; production never calls this."""
    global _mt5, _import_error
    _mt5, _import_error = module, None


def available() -> bool:
    return _load()


def not_installed() -> dict:
    return {"ok": False, "reason": "NOT_INSTALLED", "detail": "the MetaTrader5 Python package is not installed on this machine (pip install MetaTrader5; a running MT5 terminal is required)" + (" [%s]" % _import_error if _import_error else "")}


def _c(name: str, default):
    return getattr(_mt5, name, default) if _mt5 is not None else default


def _err() -> str:
    try:
        code, text = _mt5.last_error()
        return "%s %s" % (code, text)
    except Exception:  # noqa: BLE001
        return "unknown"


def _d(obj) -> Optional[dict]:
    if obj is None:
        return None
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    if isinstance(obj, dict):
        return dict(obj)
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_") and not callable(getattr(obj, k))}


# ----------------------------------------------------------------------------- session
def connect(login: int, password: str, server: str, terminal_path: Optional[str] = None, timeout_ms: int = 30_000) -> dict:
    """Initialise the terminal link and log in. The password is passed through, never logged or returned."""
    if not _load():
        return not_installed()
    with _lock:
        kw = {"login": int(login), "password": password, "server": server, "timeout": timeout_ms}
        try:
            ok = _mt5.initialize(terminal_path, **kw) if terminal_path else _mt5.initialize(**kw)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "INITIALIZE_RAISED", "detail": "%s: %s" % (type(exc).__name__, exc)}
        if not ok:
            return {"ok": False, "reason": "INITIALIZE_FAILED", "detail": _err()}
        acct = _d(_mt5.account_info())
        if not acct or int(acct.get("login") or 0) != int(login):
            detail = "logged-in account %s is not the requested %s" % ((acct or {}).get("login"), login) if acct else _err()
            try:
                if not _mt5.login(int(login), password=password, server=server):
                    return {"ok": False, "reason": "LOGIN_FAILED", "detail": _err()}
                acct = _d(_mt5.account_info())
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "reason": "LOGIN_RAISED", "detail": "%s: %s" % (type(exc).__name__, exc)}
            if not acct or int(acct.get("login") or 0) != int(login):
                return {"ok": False, "reason": "WRONG_ACCOUNT", "detail": detail}
        return {"ok": True, "account": account_summary(acct)}


def account_summary(acct: Optional[dict]) -> Optional[dict]:
    if not acct:
        return None
    keys = ("login", "server", "name", "company", "currency", "balance", "equity", "margin", "margin_free", "leverage", "trade_allowed", "trade_mode", "margin_mode")
    out = {k: acct.get(k) for k in keys if k in acct}
    tm = acct.get("trade_mode")
    out["account_kind"] = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(tm, str(tm)) if tm is not None else None
    return out


def disconnect() -> dict:
    if not _load():
        return not_installed()
    with _lock:
        try:
            _mt5.shutdown()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "SHUTDOWN_RAISED", "detail": str(exc)}
    return {"ok": True}


def account() -> dict:
    if not _load():
        return not_installed()
    with _lock:
        acct = _d(_mt5.account_info())
    if not acct:
        return {"ok": False, "reason": "NOT_CONNECTED", "detail": _err()}
    return {"ok": True, "account": account_summary(acct)}


def terminal() -> dict:
    if not _load():
        return not_installed()
    with _lock:
        t = _d(_mt5.terminal_info())
    if not t:
        return {"ok": False, "reason": "NO_TERMINAL", "detail": _err()}
    return {"ok": True, "terminal": {k: t.get(k) for k in ("connected", "trade_allowed", "tradeapi_disabled", "ping_last", "company", "name", "path", "build") if k in t}}


# ----------------------------------------------------------------------------- symbols and prices
def symbol(sym: str, select: bool = True) -> dict:
    """The broker's view of a symbol: whether it exists, is tradable, its volume rules and filling modes."""
    if not _load():
        return not_installed()
    with _lock:
        info = _d(_mt5.symbol_info(sym))
        if info is None:
            return {"ok": False, "reason": "UNKNOWN_SYMBOL", "detail": "symbol %r is not offered by this broker (%s)" % (sym, _err())}
        if select and not info.get("visible"):
            try:
                _mt5.symbol_select(sym, True)
            except Exception:  # noqa: BLE001
                pass
            info = _d(_mt5.symbol_info(sym)) or info
    fm = int(info.get("filling_mode") or 0)
    tm = info.get("trade_mode")
    return {"ok": True, "symbol": sym, "description": info.get("description"), "visible": bool(info.get("visible")), "trade_mode": tm,
            "trade_mode_text": {0: "DISABLED", 1: "LONG_ONLY", 2: "SHORT_ONLY", 3: "CLOSE_ONLY", 4: "FULL"}.get(tm, str(tm)),
            "volume_min": info.get("volume_min"), "volume_max": info.get("volume_max"), "volume_step": info.get("volume_step"),
            "digits": info.get("digits"), "point": info.get("point"), "contract_size": info.get("trade_contract_size"),
            "filling_fok": bool(fm & SYMBOL_FILLING_FOK), "filling_ioc": bool(fm & SYMBOL_FILLING_IOC), "filling_mode_raw": fm,
            "stops_level": info.get("trade_stops_level"), "freeze_level": info.get("trade_freeze_level"), "bid": info.get("bid"), "ask": info.get("ask"),
            "currency_profit": info.get("currency_profit"), "path": info.get("path")}


def tick(sym: str) -> dict:
    if not _load():
        return not_installed()
    with _lock:
        t = _d(_mt5.symbol_info_tick(sym))
    if not t or not t.get("bid") or not t.get("ask"):
        return {"ok": False, "reason": "NO_TICK", "detail": "no bid/ask for %s (%s)" % (sym, _err())}
    return {"ok": True, "bid": float(t["bid"]), "ask": float(t["ask"]), "time": t.get("time"), "age_s": (time.time() - float(t["time"])) if t.get("time") else None}


def filling_for(sym_info: dict) -> int:
    """IOC when the broker allows it (fills what is available, the rest is cancelled: partial fills are then explicit),
    else FOK, else RETURN."""
    if sym_info.get("filling_ioc"):
        return _c("ORDER_FILLING_IOC", ORDER_FILLING_IOC)
    if sym_info.get("filling_fok"):
        return _c("ORDER_FILLING_FOK", ORDER_FILLING_FOK)
    return _c("ORDER_FILLING_RETURN", ORDER_FILLING_RETURN)


# ----------------------------------------------------------------------------- orders
def _result(res) -> dict:
    r = _d(res) or {}
    code = int(r.get("retcode") or 0)
    return {"retcode": code, "retcode_text": RETCODE_TEXT.get(code, str(code)), "comment": r.get("comment"), "deal": r.get("deal"), "order": r.get("order"),
            "volume": r.get("volume"), "price": r.get("price"), "bid": r.get("bid"), "ask": r.get("ask"), "request_id": r.get("request_id")}


def market_order(sym: str, side: str, volume: float, *, sl: Optional[float], tp: Optional[float], deviation_points: int, magic: int, comment: str,
                 sym_info: Optional[dict] = None, check_only: bool = False) -> dict:
    """One market order with server-side SL/TP. ``check_only`` runs the broker's order_check (no order is created)."""
    if not _load():
        return not_installed()
    info = sym_info or symbol(sym)
    if not info.get("ok"):
        return info
    t = tick(sym)
    if not t.get("ok"):
        return t
    buy = side.upper() in ("BUY", "LONG")
    req = {"action": _c("TRADE_ACTION_DEAL", TRADE_ACTION_DEAL), "symbol": sym, "volume": float(volume),
           "type": _c("ORDER_TYPE_BUY", ORDER_TYPE_BUY) if buy else _c("ORDER_TYPE_SELL", ORDER_TYPE_SELL),
           "price": t["ask"] if buy else t["bid"], "deviation": int(deviation_points), "magic": int(magic), "comment": comment[:31],
           "type_time": _c("ORDER_TIME_GTC", ORDER_TIME_GTC), "type_filling": filling_for(info)}
    if sl is not None:
        req["sl"] = round(float(sl), int(info.get("digits") or 2))
    if tp is not None:
        req["tp"] = round(float(tp), int(info.get("digits") or 2))
    with _lock:
        try:
            if check_only:
                res = _result(_mt5.order_check(req))
                res["ok"] = res["retcode"] == 0
                res["request"] = req
                return res
            chk = _result(_mt5.order_check(req))
            if chk["retcode"] != 0:
                return {"ok": False, "stage": "order_check", "request": req, **chk}
            res = _result(_mt5.order_send(req))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "order_send", "request": req, "retcode": -1, "retcode_text": "RAISED", "comment": "%s: %s" % (type(exc).__name__, exc)}
    res["ok"] = res["retcode"] in (_c("TRADE_RETCODE_DONE", RET_DONE), _c("TRADE_RETCODE_DONE_PARTIAL", RET_DONE_PARTIAL))
    res["partial"] = res["retcode"] == _c("TRADE_RETCODE_DONE_PARTIAL", RET_DONE_PARTIAL) or (res["ok"] and res.get("volume") is not None and float(res["volume"]) < float(volume) - 1e-9)
    res["stage"] = "order_send"
    res["request"] = req
    res["tick"] = t
    return res


def positions(sym: Optional[str] = None, magic: Optional[int] = None) -> dict:
    if not _load():
        return not_installed()
    with _lock:
        try:
            rows = _mt5.positions_get(symbol=sym) if sym else _mt5.positions_get()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "POSITIONS_RAISED", "detail": str(exc)}
    out = []
    for p in rows or []:
        d = _d(p) or {}
        if magic is not None and int(d.get("magic") or 0) != int(magic):
            continue
        out.append({"ticket": d.get("ticket"), "symbol": d.get("symbol"), "type": "BUY" if d.get("type") == _c("POSITION_TYPE_BUY", POSITION_TYPE_BUY) else "SELL",
                    "volume": d.get("volume"), "price_open": d.get("price_open"), "sl": d.get("sl"), "tp": d.get("tp"), "profit": d.get("profit"),
                    "magic": d.get("magic"), "comment": d.get("comment"), "time": d.get("time")})
    return {"ok": True, "positions": out}


def close_position(ticket: int, *, deviation_points: int, magic: int, comment: str) -> dict:
    """Close one position by ticket at market (opposite deal of the full volume)."""
    if not _load():
        return not_installed()
    with _lock:
        try:
            rows = _mt5.positions_get(ticket=int(ticket))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "POSITIONS_RAISED", "detail": str(exc)}
    if not rows:
        return {"ok": True, "already_closed": True, "ticket": int(ticket)}
    p = _d(rows[0]) or {}
    sym = p["symbol"]
    info = symbol(sym)
    if not info.get("ok"):
        return info
    t = tick(sym)
    if not t.get("ok"):
        return t
    is_buy = p.get("type") == _c("POSITION_TYPE_BUY", POSITION_TYPE_BUY)
    req = {"action": _c("TRADE_ACTION_DEAL", TRADE_ACTION_DEAL), "symbol": sym, "volume": float(p["volume"]), "position": int(ticket),
           "type": _c("ORDER_TYPE_SELL", ORDER_TYPE_SELL) if is_buy else _c("ORDER_TYPE_BUY", ORDER_TYPE_BUY),
           "price": t["bid"] if is_buy else t["ask"], "deviation": int(deviation_points), "magic": int(magic), "comment": comment[:31],
           "type_time": _c("ORDER_TIME_GTC", ORDER_TIME_GTC), "type_filling": filling_for(info)}
    with _lock:
        try:
            res = _result(_mt5.order_send(req))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "close", "request": req, "retcode": -1, "retcode_text": "RAISED", "comment": "%s: %s" % (type(exc).__name__, exc)}
    res["ok"] = res["retcode"] in (_c("TRADE_RETCODE_DONE", RET_DONE), _c("TRADE_RETCODE_DONE_PARTIAL", RET_DONE_PARTIAL))
    res["partial"] = res["retcode"] == _c("TRADE_RETCODE_DONE_PARTIAL", RET_DONE_PARTIAL)
    res["stage"] = "close"
    res["request"] = req
    res["ticket"] = int(ticket)
    return res


def position_exit_deals(ticket: int, since_epoch: float) -> dict:
    """Deals that closed (part of) a position: tells a server-side SL / TP / stop-out apart from our own close."""
    if not _load():
        return not_installed()
    import datetime as _dt
    with _lock:
        try:
            rows = _mt5.history_deals_get(position=int(ticket))
            if rows is None:
                rows = _mt5.history_deals_get(_dt.datetime.fromtimestamp(since_epoch), _dt.datetime.now() + _dt.timedelta(days=1), position=int(ticket))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "HISTORY_RAISED", "detail": str(exc)}
    out = []
    for d0 in rows or []:
        d = _d(d0) or {}
        if d.get("entry") != _c("DEAL_ENTRY_OUT", DEAL_ENTRY_OUT):
            continue
        reason = d.get("reason")
        out.append({"deal": d.get("ticket"), "time": d.get("time"), "price": d.get("price"), "volume": d.get("volume"), "profit": d.get("profit"),
                    "reason": {_c("DEAL_REASON_SL", DEAL_REASON_SL): "SL", _c("DEAL_REASON_TP", DEAL_REASON_TP): "TP", _c("DEAL_REASON_SO", DEAL_REASON_SO): "STOP_OUT"}.get(reason, "CLIENT" if reason in (0, 1, 2, 3) else str(reason)),
                    "comment": d.get("comment")})
    return {"ok": True, "deals": out}
