"""MT5 auto-trading for the operator's own account, driven by the paper tracker's Strategy Trades (demo_trade_service).

Policy (owner spec 2026-11-11):
  * master switch ``AUTO_TRADE`` (env, default 0) - nothing is ever sent while it is 0; a runtime pause file stops entries too;
  * only MAPPED symbols trade (software symbol -> broker symbol, verified against the broker, saved in mt5_state/symbol_map.json);
  * quantity = the paper trade's share count, rounded DOWN to the broker's volume step; below the minimum -> skip + log;
  * BUY -> market buy, SELL -> market sell, with server-side SL / TP = the paper stop / target at entry;
  * a paper exit (SCORE_EXIT / EOD_CLOSE / CLEANUP / STOP / TARGET) closes the MT5 position: up to 3 attempts, then a logged failure;
  * if the broker closed it first (SL / TP / stop-out) that is logged, not retried;
  * every validation failure or broker refusal is logged with the full reason and skipped; price-moved refusals are re-priced once;
  * one MT5 position per symbol and side at a time (the four models often agree on a name: the first paper entry trades, the
    others are logged SKIPPED_DUPLICATE); an opposite-direction entry while a position is open is skipped (netting accounts
    would otherwise close the first one);
  * every event is appended to mt5_state/audit.jsonl and shown on the Audit Report page.
Nothing here changes scoring or the paper tracker's own rules: it only listens to the tracker's ENTRY / EXIT events.
Execution venue: the mtapi.io MT5 Client REST API (cloud; see mt5_bridge.py) - no local terminal, no MetaTrader5 package.
Volume: the API trades in LOTS; shares -> lots = shares / contract size (SymbolParams), rounded DOWN to the volume step,
skipped (and logged) when below the minimum. Both the share count and the lots are in the audit record.
Credentials: entered on the account page by the operator (endpoint, account ID, password, server), stored under
backend/.sec_cache/mt5_credentials.json (owner-only file, password obfuscated with the app's session secret), or supplied
through env MT5_LOGIN / MT5_PASSWORD / MT5_SERVER / MT5_BASE_URL. They are never logged, never returned by any route."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import mt5_bridge as B

_log = logging.getLogger("mt5")
try:
    import app_logging as _al
    _al.file_logger("mt5")                                                   # logs/mt5.log: every audit line is mirrored here (A.24)
except Exception:  # noqa: BLE001
    pass
_lock = threading.RLock()

DEFAULT_SERVER = "STGlobalMarketsLtd-Live-1"
MAGIC = int(os.getenv("MT5_MAGIC") or 240911)                       # identifies our orders on the account
STATE_DIR = Path(os.getenv("MT5_STATE_DIR") or (Path(__file__).parent / "mt5_state"))
CRED_FILE = Path(os.getenv("SEC_CACHE_DIR") or (Path(__file__).parent / ".sec_cache")) / "mt5_credentials.json"
MAP_FILE, AUDIT_FILE, POS_FILE, PAUSE_FILE = "symbol_map.json", "audit.jsonl", "positions.json", "PAUSE"
SETTINGS_FILE, HALT_FILE, DAILY_FILE = "settings.json", "HALT", "daily.json"
# A.21 (owner decisions 2026-11-12): one magic number per model so each model's positions are its own on the broker.
MAGIC_BY_MODEL = {"grok": 240911, "meta": 240912, "kimi": 240913, "gemini": 240914}
MAGIC_OTHER = 240919
SETTINGS_DEFAULTS = {"shares_per_trade": 10, "max_open_positions": 20, "daily_loss_pct": 1.0}
# A.22: the MT5 engine closes on its OWN clock (owner decision 2026-11-13 after the 2026-11-12 paper close ran 19-27 min late and
# the broker refused every close with "Market is closed"): sweep every open position of ours from EOD_SWEEP_START, retrying
# until EOD_SWEEP_END; a close refused with MARKET_CLOSED waits in the pending-close queue and is retried from PENDING_RETRY_FROM.
EOD_SWEEP_START, EOD_SWEEP_END, PENDING_RETRY_FROM = (15, 45), (15, 58), (9, 31)
SWEEP_POLL_S = 30.0
SETTINGS_LIMITS = {"shares_per_trade": (1, 10000), "max_open_positions": (1, 500), "daily_loss_pct": (0.1, 25.0)}
MAX_CLOSE_ATTEMPTS = 3
DEVIATION_POINTS = int(os.getenv("MT5_DEVIATION_POINTS") or 50)       # max slippage for a market order, in broker points
AUDIT_TAIL = 2000


# ----------------------------------------------------------------------------- switches and state files
def auto_trade_enabled() -> bool:
    """The master switch: env AUTO_TRADE=1. Default OFF."""
    return (os.getenv("AUTO_TRADE") or "0").strip().lower() in ("1", "true", "yes", "on")


def paused() -> bool:
    return (STATE_DIR / PAUSE_FILE).exists()


def halted() -> bool:
    """Kill switch: a HALT file blocks every new order until an operator removes the file by hand (never cleared by the app)."""
    return (STATE_DIR / HALT_FILE).exists()


def dry_run() -> bool:
    """MT5_DRY_RUN=1: compute everything, write ORDER_DRY / CLOSE_DRY audit lines, send nothing to the broker."""
    return (os.getenv("MT5_DRY_RUN") or "").strip() == "1"


def magic_for(strategy: Optional[str]) -> int:
    return MAGIC_BY_MODEL.get((strategy or "").lower().strip(), MAGIC_OTHER)


def all_magics() -> set:
    return set(MAGIC_BY_MODEL.values()) | {MAGIC_OTHER, MAGIC}


# ----------------------------------------------------------------------------- operator settings (admin page, no deploy)
def mt5_settings() -> dict:
    """Shares per trade, max open positions, daily loss % of balance; stored in mt5_state/settings.json (survives deploys)."""
    cur = _read_json(STATE_DIR / SETTINGS_FILE, {})
    out = dict(SETTINGS_DEFAULTS)
    for k in SETTINGS_DEFAULTS:
        v = (cur or {}).get(k)
        if v is not None:
            try:
                lo, hi = SETTINGS_LIMITS[k]
                f = float(v)
                out[k] = int(f) if k != "daily_loss_pct" else f
                if not (lo <= f <= hi):
                    out[k] = SETTINGS_DEFAULTS[k]
            except (TypeError, ValueError):
                pass
    return out


def update_mt5_settings(patch: dict, who: str = "operator") -> dict:
    """Validate and persist the operator settings; every change is audited with old and new values."""
    cur = mt5_settings()
    new = dict(cur)
    errors = {}
    for k, v in (patch or {}).items():
        if k not in SETTINGS_DEFAULTS:
            continue
        lo, hi = SETTINGS_LIMITS[k]
        try:
            f = float(v)
        except (TypeError, ValueError):
            errors[k] = "not a number"
            continue
        if k != "daily_loss_pct" and abs(f - round(f)) > 1e-9:
            errors[k] = "must be a whole number"
            continue
        if not (lo <= f <= hi):
            errors[k] = "must be between %s and %s" % (lo, hi)
            continue
        new[k] = int(round(f)) if k != "daily_loss_pct" else round(f, 2)
    if errors:
        return {"ok": False, "errors": errors, "settings": cur}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(STATE_DIR / SETTINGS_FILE, dict(new, updated_at=_now_iso(), updated_by=who))
    changed = {k: {"from": cur[k], "to": new[k]} for k in new if new[k] != cur[k]}
    if changed:
        audit("SETTINGS", detail={"by": who, "changed": changed})
    return {"ok": True, "settings": new, "changed": changed}


# ----------------------------------------------------------------------------- exact-share sizing
def sizing(contract_size, volume_min, volume_step, volume_max, shares: int) -> dict:
    """volume = shares / contract size, rounded to the step; valid only when it is >= min, <= max, on the step grid and equals
    EXACTLY the requested shares. Anything else is SKIPPED_SIZE (never a different share count)."""
    out = {"shares": int(shares), "contract_size": contract_size, "volume": None, "shares_sent": None, "valid": False, "reason": None}
    try:
        cs = float(contract_size or 0)
        if cs <= 0:
            out["reason"] = "contract size unknown"
            return out
        raw = float(shares) / cs
        step = float(volume_step) if volume_step else None
        vmin = float(volume_min) if volume_min is not None else None
        vmax = float(volume_max) if volume_max else None
        if step is None or step <= 0 or vmin is None:
            out["reason"] = "volume rules unknown (re-run auto-map)"
            return out
        vol = round(round(raw / step) * step, 8)
        out["volume"] = vol
        out["shares_sent"] = round(vol * cs, 6)
        if abs(vol * cs - float(shares)) > 1e-6:
            out["reason"] = "%s shares is not a multiple of the volume step %s x contract size %s" % (shares, step, cs)
            return out
        if vol < vmin - 1e-9:
            out["reason"] = "volume %s below the broker minimum %s" % (vol, vmin)
            return out
        if vmax and vol > vmax + 1e-9:
            out["reason"] = "volume %s above the broker maximum %s" % (vol, vmax)
            return out
        out["valid"] = True
        return out
    except (TypeError, ValueError) as exc:
        out["reason"] = "sizing error: %s" % exc
        return out


def sizing_table() -> dict:
    """The per-symbol volume table for the admin / MT5 page, from the stored mapping (no broker call)."""
    st = mt5_settings()
    shares = st["shares_per_trade"]
    full = load_map()
    m, ex = full["map"], set(full.get("excluded") or [])
    rows = []
    for s in software_symbols():
        e = m.get(s) or {}
        if s in ex:
            rows.append({"symbol": s, "broker_symbol": e.get("broker_symbol"), "status": "EXCLUDED", "contract_size": None, "volume": None, "shares_sent": None, "valid": False, "reason": "excluded"})
            continue
        if not (e.get("broker_symbol") and e.get("verified")):
            rows.append({"symbol": s, "broker_symbol": e.get("broker_symbol"), "status": "UNMAPPED", "contract_size": None, "volume": None, "shares_sent": None, "valid": False, "reason": "not mapped"})
            continue
        z = sizing(e.get("contract_size"), e.get("volume_min"), e.get("volume_step"), e.get("volume_max"), shares)
        rows.append({"symbol": s, "broker_symbol": e.get("broker_symbol"), "status": "MAPPED", "contract_size": e.get("contract_size"), "contract_size_source": e.get("contract_size_source"),
                     "volume_min": e.get("volume_min"), "volume_step": e.get("volume_step"), "volume": z["volume"], "shares_sent": z["shares_sent"], "valid": z["valid"], "reason": z["reason"]})
    return {"shares_per_trade": shares, "rows": rows, "valid": sum(1 for r in rows if r["valid"]), "invalid": sum(1 for r in rows if r["status"] == "MAPPED" and not r["valid"]), "total": len(rows)}


# ----------------------------------------------------------------------------- daily realized P&L (safety)
def _ny_date() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def daily_state() -> dict:
    d = _read_json(STATE_DIR / DAILY_FILE, {}) or {}
    if d.get("ny_date") != _ny_date():
        d = {"ny_date": _ny_date(), "realized": 0.0, "closed": 0, "limit_hit_at": None}
    return d


def _book_realized(pnl: float) -> dict:
    d = daily_state()
    d["realized"] = round(float(d.get("realized") or 0.0) + float(pnl), 2)
    d["closed"] = int(d.get("closed") or 0) + 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(STATE_DIR / DAILY_FILE, d)
    return d


def daily_loss_basis() -> tuple:
    """(basis name, value): the balance when positive, else the equity (a credit-funded demo shows a negative balance)."""
    acct = _conn.get("account") or {}
    try:
        bal = float(acct.get("balance")) if acct.get("balance") is not None else None
    except (TypeError, ValueError):
        bal = None
    try:
        eq = float(acct.get("equity")) if acct.get("equity") is not None else None
    except (TypeError, ValueError):
        eq = None
    if bal is not None and bal > 0:
        return "balance", bal
    if eq is not None and eq > 0:
        return "equity", eq
    return "none", None


def daily_loss_limit_usd() -> Optional[float]:
    name, value = daily_loss_basis()
    if value is None:
        return None
    try:
        return round(float(value) * float(mt5_settings()["daily_loss_pct"]) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def daily_loss_reached() -> bool:
    lim = daily_loss_limit_usd()
    return bool(lim is not None and float(daily_state().get("realized") or 0.0) <= -lim)


def _realized_for(p: dict, close_price) -> Optional[float]:
    try:
        sgn = 1 if p.get("side") in ("BUY", "LONG") else -1
        cs = float(p.get("contract_size") or 1.0)
        return round((float(close_price) - float(p["price"])) * sgn * float(p.get("volume") or 0) * cs, 2)
    except (TypeError, ValueError, KeyError):
        return None


def set_paused(flag: bool, who: str = "operator") -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = STATE_DIR / PAUSE_FILE
    if flag:
        p.write_text("paused by %s at %s" % (who, _now_iso()), encoding="utf-8")
    elif p.exists():
        p.unlink()
    audit("PAUSE" if flag else "RESUME", detail="entries %s by %s" % ("paused" if flag else "resumed", who))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


# ----------------------------------------------------------------------------- audit log (append-only)
def audit(event: str, *, symbol: Optional[str] = None, broker_symbol: Optional[str] = None, action: Optional[str] = None, quantity=None,
          result: Optional[str] = None, reason: Optional[str] = None, ticket=None, trade_id=None, strategy: Optional[str] = None,
          detail: Any = None, price=None) -> dict:
    rec = {"at": _now_iso(), "event": event, "symbol": symbol, "broker_symbol": broker_symbol, "action": action, "quantity": quantity,
           "result": result, "reason": reason, "ticket": ticket, "trade_id": trade_id, "strategy": strategy, "price": price, "detail": detail}
    rec = {k: v for k, v in rec.items() if v is not None}
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        _log.exception("mt5 audit write failed")
    _log.info("MT5 %s %s", event, {k: v for k, v in rec.items() if k not in ("at", "event")})
    return rec


def audit_rows(limit: int = 200, event: Optional[str] = None, symbol: Optional[str] = None) -> list:
    p = STATE_DIR / AUDIT_FILE
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f.readlines()[-AUDIT_TAIL:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if event and r.get("event") != event:
                continue
            if symbol and r.get("symbol") != symbol:
                continue
            rows.append(r)
    return rows[-max(1, min(int(limit), AUDIT_TAIL)):][::-1]


# ----------------------------------------------------------------------------- credentials
def _keystream(n: int) -> bytes:
    import auth_service
    key = auth_service._secret() if hasattr(auth_service, "_secret") else b"mt5"
    if isinstance(key, str):
        key = key.encode("utf-8")
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key, b"mt5-credentials:%d" % i, hashlib.sha256).digest()
        i += 1
    return out[:n]


def _obf(s: str) -> str:
    b = s.encode("utf-8")
    return bytes(x ^ y for x, y in zip(b, _keystream(len(b)))).hex()


def _deobf(h: str) -> str:
    b = bytes.fromhex(h)
    return bytes(x ^ y for x, y in zip(b, _keystream(len(b)))).decode("utf-8")


def save_credentials(login: int, password: str, server: str, base_url: Optional[str] = None) -> None:
    """Owner-only file; the password is obfuscated with the session secret (not plaintext, not strong encryption: the file
    permissions are the real protection)."""
    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _write_json(CRED_FILE, {"login": int(login), "server": server, "base_url": base_url or B.DEFAULT_BASE_URL, "password_obf": _obf(password), "saved_at": _now_iso()})
    try:
        os.chmod(CRED_FILE, 0o600)
    except OSError:
        pass


def clear_credentials() -> None:
    try:
        CRED_FILE.unlink()
    except FileNotFoundError:
        pass


def load_credentials() -> Optional[dict]:
    """env wins (MT5_LOGIN / MT5_PASSWORD / MT5_SERVER), else the saved file. Never log the result."""
    if os.getenv("MT5_LOGIN") and os.getenv("MT5_PASSWORD"):
        return {"login": int(os.getenv("MT5_LOGIN")), "password": os.getenv("MT5_PASSWORD"), "server": os.getenv("MT5_SERVER") or DEFAULT_SERVER, "base_url": os.getenv("MT5_BASE_URL") or B.DEFAULT_BASE_URL, "source": "env"}
    d = _read_json(CRED_FILE, None)
    if not d:
        return None
    try:
        return {"login": int(d["login"]), "password": _deobf(d["password_obf"]), "server": d.get("server") or DEFAULT_SERVER, "base_url": d.get("base_url") or B.DEFAULT_BASE_URL, "source": "file"}
    except (KeyError, ValueError):
        return None


# ----------------------------------------------------------------------------- connection
_conn: dict = {"connected": False, "account": None, "since": None, "last_error": None, "server": None, "base_url": None}
_ignore_automap_on_connect = False          # tests set this; production auto-maps right after a successful connect


def connect(login: int, password: str, server: str, remember: bool = True, base_url: Optional[str] = None) -> dict:
    with _lock:
        base = (base_url or B.DEFAULT_BASE_URL).strip()
        res = B.connect(int(login), password, server, base_url=base)
        if res.get("ok"):
            _conn.update(connected=True, account=res["account"], since=_now_iso(), last_error=None, server=server, base_url=base)
            global _acct_refreshed_at
            _acct_refreshed_at = time.time()
            if remember:
                save_credentials(int(login), password, server, base)
            audit("CONNECTED", detail={"login": int(login), "server": server, "base_url": base, "account_kind": (res["account"] or {}).get("account_kind")})
            if not _ignore_automap_on_connect:
                start_automap()
        else:
            _conn.update(connected=False, account=None, last_error="%s: %s" % (res.get("reason"), res.get("detail")), server=server, base_url=base)
            audit("CONNECT_FAILED", reason=res.get("reason"), detail=res.get("detail"))
        return {k: v for k, v in res.items() if k != "password"}


def reconnect_from_saved() -> dict:
    c = load_credentials()
    if not c:
        return {"ok": False, "reason": "NO_CREDENTIALS", "detail": "no saved MT5 credentials (enter them on the account page or set MT5_LOGIN / MT5_PASSWORD / MT5_SERVER / MT5_BASE_URL)"}
    return connect(c["login"], c["password"], c["server"], remember=False, base_url=c.get("base_url"))


def disconnect(forget: bool = False) -> dict:
    with _lock:
        res = B.disconnect()
        _conn.update(connected=False, account=None, since=None)
        if forget:
            clear_credentials()
        audit("DISCONNECTED", detail={"credentials_forgotten": bool(forget)})
        return res


ACCOUNT_REFRESH_S = 30.0
_acct_refreshed_at = 0.0


def is_connected() -> bool:
    """In-memory answer, no network."""
    return bool(_conn["connected"])


def status(refresh: bool = True) -> dict:
    """Page status. The broker account is re-read at most every ACCOUNT_REFRESH_S seconds (one HTTPS call), never per poll,
    and never while holding the module lock: a status read must not queue behind anything (2026-11-11: a poll per 3 s with a
    broker call and a database query each took the whole site down)."""
    global _acct_refreshed_at
    if True:
        if refresh and _conn["connected"] and (time.time() - _acct_refreshed_at) >= ACCOUNT_REFRESH_S:
            _acct_refreshed_at = time.time()
            live = B.account()
            if not live.get("ok"):
                _conn.update(connected=False, account=None, last_error="%s: %s" % (live.get("reason"), live.get("detail")))
            else:
                _conn["account"] = live["account"]
        return {"installed": True, "install_note": None, "provider": "mtapi.io MT5 Client REST API", "base_url": _conn["base_url"] or B.DEFAULT_BASE_URL,
                "connected": bool(_conn["connected"]), "account": _conn["account"], "server": _conn["server"] or DEFAULT_SERVER, "since": _conn["since"],
                "last_error": _conn["last_error"], "credentials_saved": CRED_FILE.exists() or bool(os.getenv("MT5_LOGIN")),
                "auto_trade": auto_trade_enabled(), "paused": paused(), "halted": halted(), "dry_run": dry_run(), "magic": MAGIC, "magic_by_model": MAGIC_BY_MODEL, "default_server": DEFAULT_SERVER,
                "margin_mode": (_conn.get("account") or {}).get("margin_mode"), "settings": mt5_settings(), "open_positions": len(_positions()["open"]),
                "daily": dict(daily_state(), loss_limit_usd=daily_loss_limit_usd(), limit_reached=daily_loss_reached(), loss_basis=daily_loss_basis()[0], loss_basis_value=daily_loss_basis()[1]),
                "pending_close": list(_positions().get("pending_close", {}).values()), "sweep": dict(_sweep_state),
                "terminal": (B.terminal().get("terminal") if _conn["connected"] else None)}


# ----------------------------------------------------------------------------- symbol mapping
SYMBOLS_CACHE_S = 60.0
_symbols_cache: dict = {"at": 0.0, "symbols": None}


def software_symbols() -> list:
    """Every symbol the Strategy Trades tab can trade: the intraday boards' liquid universe plus any symbol already in the paper
    book. Cached for SYMBOLS_CACHE_S: the mapping page polls, and a database query per poll exhausts the 15-connection pool."""
    if _symbols_cache["symbols"] is not None and (time.time() - _symbols_cache["at"]) < SYMBOLS_CACHE_S:
        return list(_symbols_cache["symbols"])
    out = _software_symbols_uncached()
    _symbols_cache.update(at=time.time(), symbols=list(out))
    return out


def _software_symbols_uncached() -> list:
    syms = set()
    try:
        import ai_trade_service as ats
        syms.update(s.upper() for s in getattr(ats, "UNIVERSE", []))
    except Exception:  # noqa: BLE001
        pass
    try:
        import database
        from models_demo_trades import DemoTrade
        s = database.SessionLocal()
        try:
            syms.update(r[0] for r in s.query(DemoTrade.symbol).distinct().all() if r and r[0])
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        pass
    return sorted(syms)


# Symbols the operator ruled out for this broker (2026-11-11: "not on this broker / LP ... must stay UNMAPPED. Do not force-map
# them"). Seeded into the map file's "excluded" list the first time it is read without one; MT5_EXCLUDED_SYMBOLS overrides the seed.
# 2026-11-11 afternoon the broker's symbol list changed (65 -> 1,029 names) and the startup auto-map mapped these twelve by their
# plain names; four of them then traded on the demo account. An excluded symbol is never auto-mapped, never mapped by hand
# without un-excluding it first, and never sent as an order.
DEFAULT_EXCLUDED = ("ADBE", "AMD", "AVGO", "CAT", "COP", "COST", "LLY", "MU", "ORACLE", "ORCL", "QQQ", "SPY", "UNH")


def _seed_excluded() -> list:
    raw = (os.getenv("MT5_EXCLUDED_SYMBOLS") or "").strip()
    src = [x for x in raw.replace(";", ",").split(",")] if raw else list(DEFAULT_EXCLUDED)
    return sorted({x.strip().upper() for x in src if x and x.strip()})


def load_map() -> dict:
    m = _read_json(STATE_DIR / MAP_FILE, {"version": 1, "map": {}})
    if "excluded" not in m:
        m["excluded"] = _seed_excluded()                                        # first read after A.18: the owner's list
    return m


def excluded_symbols() -> list:
    return sorted(set(load_map().get("excluded") or []))


def is_excluded(symbol: str) -> bool:
    return (symbol or "").upper().strip() in set(load_map().get("excluded") or [])


def set_excluded(symbol: str, flag: bool, who: str = "operator") -> dict:
    """Operator switch. Excluding a symbol also drops its mapping (so it shows UNMAPPED/EXCLUDED at once and can never trade)."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"ok": False, "reason": "NO_SYMBOL"}
    with _map_lock:
        m = load_map()
        ex = set(m.get("excluded") or [])
        if flag:
            ex.add(symbol)
            m["map"].pop(symbol, None)
        else:
            ex.discard(symbol)
        m["excluded"] = sorted(ex)
        save_map(m)
    audit("EXCLUDED" if flag else "UNEXCLUDED", symbol=symbol, detail={"by": who})
    return {"ok": True, "symbol": symbol, "excluded": flag}


def apply_exclusions(who: str = "startup") -> list:
    """Drop any mapping that exists for an excluded symbol (the seed arriving after mappings were made). Returns what was dropped."""
    dropped = []
    with _map_lock:
        m = load_map()
        for s in sorted(set(m.get("excluded") or [])):
            if s in m["map"]:
                dropped.append({"symbol": s, "broker_symbol": (m["map"].get(s) or {}).get("broker_symbol")})
                m["map"].pop(s, None)
        save_map(m)                                                               # also persists the seeded list
    for d in dropped:
        audit("EXCLUDED", symbol=d["symbol"], broker_symbol=d["broker_symbol"], detail={"by": who, "note": "mapping dropped: symbol is on the exclusion list"})
    return dropped


def save_map(m: dict) -> None:
    _write_json(STATE_DIR / MAP_FILE, m)


def mapping_rows() -> dict:
    full = load_map()
    m, ex = full["map"], set(full.get("excluded") or [])
    rows = []
    for s in software_symbols():
        e = m.get(s) or {}
        st = "EXCLUDED" if s in ex else ("MAPPED" if e.get("broker_symbol") and e.get("verified") else ("UNVERIFIED" if e.get("broker_symbol") else "UNMAPPED"))
        rows.append({"symbol": s, "broker_symbol": e.get("broker_symbol"), "status": st, "excluded": s in ex,
                     "verified_at": e.get("verified_at"), "source": e.get("source"), "volume_min": e.get("volume_min"), "volume_step": e.get("volume_step"), "trade_mode": e.get("trade_mode_text"), "note": e.get("note")})
    mapped = sum(1 for r in rows if r["status"] == "MAPPED")
    tradable = [r for r in rows if not r["excluded"]]
    return {"rows": rows, "mapped": mapped, "total": len(tradable), "excluded": sorted(ex),
            "summary": "%d of %d symbols mapped (%d excluded)" % (mapped, len(tradable), len(ex)), "automap": automap_state()}


def verify_broker_symbol(broker_symbol: str) -> dict:
    """Does the broker offer it and can it be traded? Needs a connection."""
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED", "detail": "connect the MT5 account first"}
    info = B.symbol(broker_symbol)
    if not info.get("ok"):
        return info
    if info.get("trade_mode") in (B.SYMBOL_TRADE_MODE_DISABLED, B.SYMBOL_TRADE_MODE_CLOSEONLY):
        return {"ok": False, "reason": "NOT_TRADABLE", "detail": "broker symbol %s is %s" % (broker_symbol, info.get("trade_mode_text")), "info": info}
    return {"ok": True, "info": info}


def set_mapping(symbol: str, broker_symbol: Optional[str], source: str = "manual") -> dict:
    """Manual override. An empty broker symbol unmaps. Verified against the broker when connected; otherwise saved UNVERIFIED
    (an unverified mapping never trades)."""
    symbol = (symbol or "").upper().strip()
    broker_symbol = (broker_symbol or "").strip()
    if not broker_symbol:
        with _map_lock:
            m = load_map()
            m["map"].pop(symbol, None)
            save_map(m)
        audit("UNMAPPED", symbol=symbol, detail={"source": source})
        return {"ok": True, "symbol": symbol, "status": "UNMAPPED"}
    if is_excluded(symbol):
        audit("MAP_REFUSED", symbol=symbol, broker_symbol=broker_symbol, reason="EXCLUDED: on the operator's exclusion list (un-exclude it first)", detail={"source": source})
        return {"ok": False, "symbol": symbol, "status": "EXCLUDED", "reason": "EXCLUDED", "note": "on the operator's exclusion list; un-exclude it first"}
    v = verify_broker_symbol(broker_symbol) if _conn["connected"] else {"ok": False, "reason": "NOT_CONNECTED"}   # network, no lock held
    e = {"broker_symbol": broker_symbol, "source": source, "verified": bool(v.get("ok")), "verified_at": _now_iso() if v.get("ok") else None, "note": None if v.get("ok") else "%s: %s" % (v.get("reason"), v.get("detail", ""))}
    if v.get("ok"):
        i = v["info"]
        e.update(volume_min=i.get("volume_min"), volume_max=i.get("volume_max"), volume_step=i.get("volume_step"), digits=i.get("digits"), trade_mode_text=i.get("trade_mode_text"),
                 filling_ioc=i.get("filling_ioc"), filling_fok=i.get("filling_fok"), contract_size=i.get("contract_size"), contract_size_source=("broker" if i.get("raw_contract") else "fallback-100"))
    with _map_lock:
        m = load_map()
        m["map"][symbol] = e
        save_map(m)
    audit("MAPPED" if e["verified"] else "MAP_UNVERIFIED", symbol=symbol, broker_symbol=broker_symbol, reason=e.get("note"), detail={"source": source})
    return {"ok": True, "symbol": symbol, "broker_symbol": broker_symbol, "status": "MAPPED" if e["verified"] else "UNVERIFIED", "note": e.get("note")}


_automap: dict = {"running": False, "started_at": None, "finished_at": None, "done": 0, "total": 0, "mapped": [], "unmapped": [], "error": None, "broker_symbols": None}
_automap_lock = threading.Lock()
_map_lock = threading.Lock()


def automap_state() -> dict:
    return {k: v for k, v in _automap.items() if k != "broker_symbols"} | {"broker_symbols": _automap["broker_symbols"]}


def _suffixes() -> list:
    return [x.strip() for x in (os.getenv("MT5_SYMBOL_SUFFIXES") or ",US.m,.US,.NAS,.NYSE,.m,US").split(",")]


_SUFFIX_RE = re.compile(r"^(US)?([._-][A-Z]{1,4})?$")        # "US.m", "US", ".NAS", "_m": the exchange tag and/or one dotted suffix; "DJPY" is not a suffix


def match_broker_symbol(sym: str, broker_names: list, previous: Optional[str] = None) -> Optional[str]:
    """Pure: the broker name for a software symbol, from the broker's own list. Exact (case-insensitive) first, then the
    configured suffixes, then a UNIQUE 'SYMBOL.<anything>' candidate. None when the broker does not offer it."""
    by_upper = {n.upper(): n for n in broker_names if n}
    for cand in ([previous] if previous else []) + [sym] + [sym + suf for suf in _suffixes() if suf]:
        hit = by_upper.get(cand.upper())
        if hit:
            return hit
    # last resort: a UNIQUE broker name that is the symbol plus a short suffix ("AMZNUS.m", "AAPL.NAS", "KO_m"); ambiguity -> None
    up = sym.upper()
    pref = [n for u, n in by_upper.items() if u != up and u.startswith(up) and _SUFFIX_RE.match(u[len(up):])]
    return pref[0] if len(pref) == 1 else None


def run_automap() -> dict:
    """ONE Symbols call, local matching, then one SymbolParams verification per matched symbol (never per candidate).
    Runs without the module lock so status / mapping reads keep answering. Symbols the broker does not offer stay UNMAPPED."""
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED", "detail": "connect the MT5 account first"}
    with _automap_lock:
        if _automap["running"]:
            return {"ok": False, "reason": "ALREADY_RUNNING"}
        _automap.update(running=True, started_at=_now_iso(), finished_at=None, done=0, total=0, mapped=[], unmapped=[], error=None)
    try:
        lst = B.symbols_list()
        if not lst.get("ok"):
            _automap.update(error="%s: %s" % (lst.get("reason"), lst.get("detail")))
            audit("AUTOMAP_FAILED", reason=_automap["error"])
            return {"ok": False, "reason": lst.get("reason"), "detail": lst.get("detail")}
        names = lst["symbols"]
        _automap["broker_symbols"] = len(names)
        offered = set(names)
        stale = []
        with _map_lock:                                                           # a verified mapping whose broker name vanished is stale
            m = load_map()
            for s, e in list(m["map"].items()):
                if e.get("verified") and e.get("broker_symbol") and e["broker_symbol"] not in offered:
                    e.update(verified=False, verified_at=None, note="STALE: the broker no longer offers %s" % e["broker_symbol"])
                    stale.append({"symbol": s, "broker_symbol": e["broker_symbol"]})
            if stale:
                save_map(m)
        for d in stale:
            audit("MAPPING_STALE", symbol=d["symbol"], broker_symbol=d["broker_symbol"], reason="the broker no longer offers this name; re-map or auto-map")
        _automap["stale"] = len(stale)
        ex = set(m.get("excluded") or [])
        skipped_excluded = [s for s in software_symbols() if s in ex]
        todo = [s for s in software_symbols() if s not in ex and not (m["map"].get(s) or {}).get("verified")]
        _automap["total"] = len(todo)
        for s in todo:
            prev = (m["map"].get(s) or {}).get("broker_symbol")
            hit = match_broker_symbol(s, names, prev)
            if hit:
                r = set_mapping(s, hit, source="auto")                       # one SymbolParams call (verification + volume rules)
                (_automap["mapped"] if r.get("status") == "MAPPED" else _automap["unmapped"]).append({"symbol": s, "broker_symbol": hit, "note": r.get("note")})
            else:
                _automap["unmapped"].append({"symbol": s, "broker_symbol": None, "note": "not offered by the broker"})
            _automap["done"] += 1
        res = {"ok": True, "mapped": list(_automap["mapped"]), "unmapped": list(_automap["unmapped"])}
        mr = mapping_rows()
        res.update(mapped_count=mr["mapped"], total=mr["total"], summary=mr["summary"])
        audit("AUTOMAP", detail={"broker_symbols": len(names), "mapped": len(_automap["mapped"]), "unmapped": len(_automap["unmapped"]), "stale": len(stale), "excluded_skipped": len(skipped_excluded), "summary": mr["summary"]})
        return res
    except Exception as exc:  # noqa: BLE001
        _automap["error"] = "%s: %s" % (type(exc).__name__, exc)
        audit("AUTOMAP_FAILED", reason=_automap["error"])
        return {"ok": False, "reason": "ERROR", "detail": _automap["error"]}
    finally:
        _automap.update(running=False, finished_at=_now_iso())


def start_automap() -> dict:
    """Background job: returns at once with the job state; the mapping page polls it."""
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED", "detail": "connect the MT5 account first", **automap_state()}
    if _automap["running"]:
        return {"ok": True, "started": False, **automap_state()}
    threading.Thread(target=run_automap, name="mt5-automap", daemon=True).start()
    time.sleep(0.05)
    return {"ok": True, "started": True, **automap_state()}


def automap() -> dict:
    """Synchronous variant (tests, scripts)."""
    return run_automap()


def mapped(symbol: str) -> Optional[dict]:
    e = load_map()["map"].get((symbol or "").upper())
    return e if e and e.get("verified") and e.get("broker_symbol") else None


# ----------------------------------------------------------------------------- sizing
def shares_to_lots(shares: float, contract_size: Optional[float]) -> float:
    """The API trades in lots; a US stock CFD lot is ``contract_size`` shares (1 or 100 depending on the broker)."""
    c = float(contract_size or 1.0) or 1.0
    return float(shares) / c


def round_volume(lots: float, volume_min: float, volume_step: float, volume_max: Optional[float] = None) -> Optional[float]:
    """Round DOWN to the broker's step; None when below the minimum (skip)."""
    try:
        step = float(volume_step or 0) or 1.0
        vmin = float(volume_min or 0) or step
        v = math.floor((float(lots) + 1e-9) / step) * step
        v = round(v, 8)
        if volume_max:
            v = min(v, float(volume_max))
        return v if v >= vmin - 1e-9 else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- open positions ledger (paper trade id -> MT5 ticket)
def _positions() -> dict:
    return _read_json(STATE_DIR / POS_FILE, {"version": 1, "open": {}})


def _save_positions(d: dict) -> None:
    _write_json(STATE_DIR / POS_FILE, d)


def open_positions() -> dict:
    return _positions()["open"]


# ----------------------------------------------------------------------------- the engine: paper events -> MT5
def _entry_allowed(ev: dict) -> Optional[str]:
    """Reason not to send, or None. Checked in this order so the audit says exactly why."""
    if not auto_trade_enabled():
        return "AUTO_TRADE_OFF"
    if paused():
        return "PAUSED"
    if halted():
        return "HALTED"
    if not B.available():
        return "NOT_INSTALLED"
    if not _conn["connected"]:
        return "NOT_CONNECTED"
    if (_conn.get("account") or {}).get("margin_mode") == "NETTING":
        return "MARGIN_MODE_NETTING"
    if is_excluded(ev["symbol"]):
        return "EXCLUDED"
    if not mapped(ev["symbol"]):
        return "UNMAPPED"
    if len(_positions()["open"]) >= int(mt5_settings()["max_open_positions"]):
        return "MAX_OPEN_POSITIONS"
    if daily_loss_reached():
        return "DAILY_LOSS_LIMIT"
    return None


def on_paper_event(ev: dict) -> Optional[dict]:
    """Called by demo_trade_service after a commit. ev = {"type": "ENTRY"|"EXIT", "trade_id", "strategy", "symbol", "side", "shares",
    "entry"/"exit_price", "stop", "target", "reason"}. Never raises into the tracker."""
    try:
        with _lock:
            if ev.get("type") == "ENTRY":
                return _handle_entry(ev)
            if ev.get("type") == "EXIT":
                return _handle_exit(ev)
    except Exception as exc:  # noqa: BLE001
        audit("ERROR", symbol=ev.get("symbol"), trade_id=ev.get("trade_id"), reason="%s: %s" % (type(exc).__name__, exc))
    return None


def _handle_entry(ev: dict) -> dict:
    sym, side, tid, strat = ev["symbol"], ev["side"], ev["trade_id"], ev.get("strategy")
    why = _entry_allowed(ev)
    if why:
        if why in ("AUTO_TRADE_OFF",):
            return {"ok": False, "reason": why}                              # switch off: silent (no audit noise every tick)
        audit("SKIPPED", symbol=sym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat, reason=why)
        return {"ok": False, "reason": why}
    m = mapped(sym)
    bsym = m["broker_symbol"]
    pos = _positions()
    # A.21: one order per model trigger. Three models buying the same stock = three positions; opposite sides may be open together.
    info = B.symbol(bsym)
    if not info.get("ok"):
        audit("REJECTED", symbol=sym, broker_symbol=bsym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat, reason="%s: %s" % (info.get("reason"), info.get("detail")))
        return {"ok": False, "reason": info.get("reason")}
    if info.get("volume_min") is None or info.get("volume_step") is None:
        audit("REJECTED", symbol=sym, broker_symbol=bsym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat, reason="NO_VOLUME_RULES: %s" % info.get("note"), detail={"raw_keys": info.get("raw_keys")})
        return {"ok": False, "reason": "NO_VOLUME_RULES"}
    shares = int(mt5_settings()["shares_per_trade"])
    z = sizing(info.get("contract_size"), info.get("volume_min"), info.get("volume_step"), info.get("volume_max"), shares)
    if not z["valid"]:
        audit("SKIPPED", symbol=sym, broker_symbol=bsym, action=side, quantity=shares, trade_id=tid, strategy=strat, reason="SKIPPED_SIZE: %s" % z["reason"],
              detail={"shares_per_trade": shares, "contract_size": info.get("contract_size"), "volume": z["volume"], "shares_sent": z["shares_sent"], "volume_min": info.get("volume_min"), "volume_step": info.get("volume_step")})
        return {"ok": False, "reason": "SKIPPED_SIZE"}
    vol = z["volume"]
    magic = magic_for(strat)
    comment = "TRDGO %s #%s" % ((strat or "")[:6], tid)
    t = B.tick(bsym)
    ref = (t.get("ask") if side in ("BUY", "LONG") else t.get("bid")) if t.get("ok") else None
    if dry_run():
        pos["open"][str(tid)] = {"trade_id": tid, "symbol": sym, "broker_symbol": bsym, "side": side, "volume": vol, "requested": vol, "ticket": None, "deal": None, "price": ref, "sl": ev.get("stop"),
                                 "tp": ev.get("target"), "opened_at": _now_iso(), "opened_epoch": time.time(), "strategy": strat, "magic": magic, "contract_size": info.get("contract_size"), "shares": shares, "dry": True}
        _save_positions(pos)
        audit("ORDER_DRY", symbol=sym, broker_symbol=bsym, action=side, quantity=vol, trade_id=tid, strategy=strat, price=ref, result="NOT_SENT",
              reason="dry run: would send %s %s lots (= %s shares) magic %s" % (side, vol, shares, magic),
              detail={"sl": ev.get("stop"), "tp": ev.get("target"), "paper_shares": ev.get("shares"), "shares_per_trade": shares, "lots": vol, "contract_size": info.get("contract_size"), "magic": magic, "comment": comment, "bid": t.get("bid"), "ask": t.get("ask")})
        return {"ok": True, "dry": True, "volume": vol}
    res = B.market_order(bsym, side, vol, sl=ev.get("stop"), tp=ev.get("target"), deviation_points=DEVIATION_POINTS, magic=magic, comment=comment, sym_info=info)
    if not res.get("ok") and res.get("retcode_text") == "REPRICE":
        audit("REPRICE", symbol=sym, broker_symbol=bsym, action=side, quantity=vol, trade_id=tid, strategy=strat, reason="%s: %s" % (res.get("retcode_text"), res.get("comment")))
        res = B.market_order(bsym, side, vol, sl=ev.get("stop"), tp=ev.get("target"), deviation_points=DEVIATION_POINTS, magic=magic, comment=comment, sym_info=info)
    if not res.get("ok"):
        audit("REJECTED", symbol=sym, broker_symbol=bsym, action=side, quantity=vol, trade_id=tid, strategy=strat,
              reason="%s (%s): %s" % (res.get("retcode_text") or res.get("reason"), res.get("retcode"), res.get("comment") or res.get("detail")),
              detail={"stage": res.get("stage"), "request": _safe_request(res.get("request")), "magic": magic, "requested_price": ref})
        return {"ok": False, "reason": res.get("retcode_text") or res.get("reason")}
    filled = float(res.get("volume") or vol)
    ticket = res.get("order") or res.get("deal")
    fill = res.get("price")
    slip = None
    if fill is not None and ref:
        try:
            sgn = 1 if side in ("BUY", "LONG") else -1
            slip = {"usd": round((float(fill) - float(ref)) * sgn, 4), "bps": round((float(fill) - float(ref)) * sgn / float(ref) * 1e4, 2), "requested_price": ref}
        except (TypeError, ValueError, ZeroDivisionError):
            slip = None
    pos["open"][str(tid)] = {"trade_id": tid, "symbol": sym, "broker_symbol": bsym, "side": side, "volume": filled, "requested": vol, "ticket": ticket, "deal": res.get("deal"),
                             "price": fill, "sl": ev.get("stop"), "tp": ev.get("target"), "opened_at": _now_iso(), "opened_epoch": time.time(), "strategy": strat, "magic": magic,
                             "contract_size": info.get("contract_size"), "shares": shares}
    _save_positions(pos)
    audit("PARTIAL_FILL" if res.get("partial") else "FILLED", symbol=sym, broker_symbol=bsym, action=side, quantity=filled, trade_id=tid, strategy=strat, ticket=ticket, price=fill,
          result=res.get("retcode_text"), reason=("requested %s lots, filled %s" % (vol, filled)) if res.get("partial") else None,
          detail={"sl": ev.get("stop"), "tp": ev.get("target"), "paper_shares": ev.get("shares"), "shares_per_trade": shares, "lots": vol, "contract_size": info.get("contract_size"), "magic": magic, "slippage": slip})
    return {"ok": True, "ticket": ticket, "volume": filled}


def _handle_exit(ev: dict) -> dict:
    tid, reason = ev["trade_id"], ev.get("reason")
    pos = _positions()
    p = pos["open"].get(str(tid))
    if not p:
        return {"ok": True, "reason": "NO_MT5_POSITION"}                    # the paper trade never traded on MT5 (switch off, unmapped, skipped ...)
    sym, bsym, ticket = p["symbol"], p["broker_symbol"], p["ticket"]
    magic = int(p.get("magic") or MAGIC)
    if p.get("dry"):
        t = B.tick(bsym) if B.available() else {}
        px = (t.get("bid") if p["side"] in ("BUY", "LONG") else t.get("ask")) if t.get("ok") else None
        pnl = _realized_for(p, px) if px is not None else None
        audit("CLOSE_DRY", symbol=sym, broker_symbol=bsym, action=("SELL" if p["side"] in ("BUY", "LONG") else "BUY"), quantity=p.get("volume"), trade_id=tid, strategy=p.get("strategy"), price=px,
              result="NOT_SENT", reason="dry run: would close for paper exit %s" % reason, detail={"realized_usd": pnl, "magic": magic})
        if pnl is not None:
            _book_realized(pnl)
        pos["open"].pop(str(tid), None)
        _save_positions(pos)
        return {"ok": True, "dry": True}
    # did the broker close it already (server-side SL / TP / stop-out)?
    deals = B.position_exit_deals(int(ticket), p.get("opened_epoch") or 0) if B.available() else {"ok": False}
    exits = (deals.get("deals") or []) if deals.get("ok") else []
    still = B.positions(bsym).get("positions") or [] if B.available() else []
    still_open = any(int(x.get("ticket") or 0) == int(ticket) for x in still)
    if exits and not still_open:
        why = exits[-1].get("reason")
        audit("CLOSED_BY_BROKER", symbol=sym, broker_symbol=bsym, action=("SELL" if p["side"] in ("BUY", "LONG") else "BUY"), quantity=p.get("volume"), trade_id=tid, strategy=p.get("strategy"),
              ticket=ticket, price=exits[-1].get("price"), result=why, reason="%s hit on MT5 (paper exit: %s)" % (why, reason), detail={"deals": exits, "realized_usd": _realized_for(p, exits[-1].get("price"))})
        pnl = _realized_for(p, exits[-1].get("price"))
        if pnl is not None:
            d = _book_realized(pnl)
            if daily_loss_reached() and not d.get("limit_hit_at"):
                d["limit_hit_at"] = _now_iso(); _write_json(STATE_DIR / DAILY_FILE, d)
                audit("DAILY_LOSS_LIMIT", reason="realized %.2f today reached the limit %.2f (%.2f%% of balance): no new orders until the next New York day" % (d["realized"], daily_loss_limit_usd() or 0, mt5_settings()["daily_loss_pct"]))
        pos["open"].pop(str(tid), None)
        _save_positions(pos)
        return {"ok": True, "closed_by": why}
    last = None
    t0 = B.tick(bsym) if B.available() else {}
    ref = (t0.get("bid") if p["side"] in ("BUY", "LONG") else t0.get("ask")) if t0.get("ok") else None
    for attempt in range(1, MAX_CLOSE_ATTEMPTS + 1):
        res = B.close_position(int(ticket), deviation_points=DEVIATION_POINTS * attempt, magic=magic, comment="TRDGO %s #%s" % ((reason or "EXIT")[:10], tid))
        last = res
        if res.get("ok"):
            fill = res.get("price")
            slip = None
            if fill is not None and ref:
                try:
                    sgn = 1 if p["side"] in ("BUY", "LONG") else -1                                  # a long closes at the bid: a lower fill is adverse
                    slip = {"usd": round((float(ref) - float(fill)) * sgn, 4), "bps": round((float(ref) - float(fill)) * sgn / float(ref) * 1e4, 2), "requested_price": ref}
                except (TypeError, ValueError, ZeroDivisionError):
                    slip = None
            pnl = _realized_for(p, fill) if fill is not None else None
            audit("CLOSED", symbol=sym, broker_symbol=bsym, action=("SELL" if p["side"] in ("BUY", "LONG") else "BUY"), quantity=res.get("volume") or p.get("volume"), trade_id=tid,
                  strategy=p.get("strategy"), ticket=ticket, price=fill, result=res.get("retcode_text") or ("ALREADY_CLOSED" if res.get("already_closed") else "DONE"),
                  reason="paper exit %s (attempt %d)" % (reason, attempt), detail={"slippage": slip, "realized_usd": pnl, "magic": magic})
            if pnl is not None:
                d = _book_realized(pnl)
                if daily_loss_reached() and not d.get("limit_hit_at"):
                    d["limit_hit_at"] = _now_iso(); _write_json(STATE_DIR / DAILY_FILE, d)
                    audit("DAILY_LOSS_LIMIT", reason="realized %.2f today reached the limit %.2f (%.2f%% of balance): no new orders until the next New York day" % (d["realized"], daily_loss_limit_usd() or 0, mt5_settings()["daily_loss_pct"]))
            pos["open"].pop(str(tid), None)
            _save_positions(pos)
            return {"ok": True, "attempts": attempt}
        audit("CLOSE_RETRY", symbol=sym, broker_symbol=bsym, trade_id=tid, strategy=p.get("strategy"), ticket=ticket,
              reason="attempt %d/%d: %s (%s): %s" % (attempt, MAX_CLOSE_ATTEMPTS, res.get("retcode_text") or res.get("reason"), res.get("retcode"), res.get("comment") or res.get("detail")))
        if res.get("retcode_text") == "NO_RETRY":
            break
        time.sleep(0.5 * attempt)
    audit("CLOSE_FAILED", symbol=sym, broker_symbol=bsym, trade_id=tid, strategy=p.get("strategy"), ticket=ticket,
          reason="could not close after %d attempts; last: %s (%s): %s -- POSITION STILL OPEN ON MT5" % (MAX_CLOSE_ATTEMPTS, (last or {}).get("retcode_text") or (last or {}).get("reason"), (last or {}).get("retcode"), (last or {}).get("comment") or (last or {}).get("detail")))
    return {"ok": False, "reason": "CLOSE_FAILED"}


def _attempt_close(p: dict, reason: str, *, attempts: int = MAX_CLOSE_ATTEMPTS) -> dict:
    """Close one ledger position on the broker (or log CLOSE_DRY for a dry-run position). Returns the last broker result with
    ``ok``; a refusal whose text says the market is closed carries ``market_closed=True``."""
    sym, bsym, ticket, tid = p["symbol"], p["broker_symbol"], p.get("ticket"), p["trade_id"]
    magic = int(p.get("magic") or MAGIC)
    if p.get("dry") or ticket is None:
        t = B.tick(bsym) if B.available() else {}
        px = (t.get("bid") if p["side"] in ("BUY", "LONG") else t.get("ask")) if t.get("ok") else None
        pnl = _realized_for(p, px) if px is not None else None
        audit("CLOSE_DRY", symbol=sym, broker_symbol=bsym, action=("SELL" if p["side"] in ("BUY", "LONG") else "BUY"), quantity=p.get("volume"), trade_id=tid, strategy=p.get("strategy"), price=px,
              result="NOT_SENT", reason="dry run: would close (%s)" % reason, detail={"realized_usd": pnl, "magic": magic})
        if pnl is not None:
            _book_realized(pnl)
        return {"ok": True, "dry": True, "price": px}
    t0 = B.tick(bsym) if B.available() else {}
    ref = (t0.get("bid") if p["side"] in ("BUY", "LONG") else t0.get("ask")) if t0.get("ok") else None
    last = {}
    for attempt in range(1, attempts + 1):
        res = B.close_position(int(ticket), deviation_points=DEVIATION_POINTS * attempt, magic=magic, comment="TRDGO %s #%s" % ((reason or "EXIT")[:10], tid))
        last = res
        if res.get("ok"):
            fill = res.get("price")
            slip = None
            if fill is not None and ref:
                try:
                    sgn = 1 if p["side"] in ("BUY", "LONG") else -1
                    slip = {"usd": round((float(ref) - float(fill)) * sgn, 4), "bps": round((float(ref) - float(fill)) * sgn / float(ref) * 1e4, 2), "requested_price": ref}
                except (TypeError, ValueError, ZeroDivisionError):
                    slip = None
            pnl = _realized_for(p, fill) if fill is not None else None
            audit("CLOSED", symbol=sym, broker_symbol=bsym, action=("SELL" if p["side"] in ("BUY", "LONG") else "BUY"), quantity=res.get("volume") or p.get("volume"), trade_id=tid,
                  strategy=p.get("strategy"), ticket=ticket, price=fill, result=res.get("retcode_text") or ("ALREADY_CLOSED" if res.get("already_closed") else "DONE"),
                  reason="%s (attempt %d)" % (reason, attempt), detail={"slippage": slip, "realized_usd": pnl, "magic": magic})
            if pnl is not None:
                d = _book_realized(pnl)
                if daily_loss_reached() and not d.get("limit_hit_at"):
                    d["limit_hit_at"] = _now_iso(); _write_json(STATE_DIR / DAILY_FILE, d)
                    audit("DAILY_LOSS_LIMIT", reason="realized %.2f today reached the limit %.2f (%.2f%% of %s): no new orders until the next New York day" % (d["realized"], daily_loss_limit_usd() or 0, mt5_settings()["daily_loss_pct"], daily_loss_basis()[0]))
            return dict(res, price=fill, realized_usd=pnl)
        text = ("%s %s %s" % (res.get("retcode_text"), res.get("comment"), res.get("detail"))).lower()
        if "market is closed" in text or "market closed" in text or "trade_market_closed" in text:
            res["market_closed"] = True
            return res
        audit("CLOSE_RETRY", symbol=sym, broker_symbol=bsym, trade_id=tid, strategy=p.get("strategy"), ticket=ticket,
              reason="attempt %d/%d: %s (%s): %s" % (attempt, attempts, res.get("retcode_text") or res.get("reason"), res.get("retcode"), res.get("comment") or res.get("detail")))
        if res.get("retcode_text") == "NO_RETRY":
            break
        time.sleep(0.5 * attempt)
    return last or {"ok": False, "reason": "NO_ATTEMPT"}


def _queue_pending(pos: dict, tid: str, p: dict, res: dict, source: str) -> None:
    q = pos.setdefault("pending_close", {})
    entry = dict(p, pending_since=p.get("pending_since") or _now_iso(), attempts=int((q.get(tid) or {}).get("attempts", 0)) + 1, last_reason="%s: %s" % (res.get("retcode_text") or res.get("reason"), res.get("comment") or res.get("detail")), source=source)
    q[tid] = entry
    pos["open"].pop(tid, None)
    audit("CLOSE_PENDING", symbol=p["symbol"], broker_symbol=p["broker_symbol"], trade_id=p["trade_id"], strategy=p.get("strategy"), ticket=p.get("ticket"),
          reason="%s: the broker refused the close (market closed); queued, retried from %02d:%02d ET next session" % (source, PENDING_RETRY_FROM[0], PENDING_RETRY_FROM[1]), detail={"attempts": entry["attempts"]})


def _et_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


_sweep_state: dict = {"last_sweep_date": None, "last_sweep_at": None, "last_retry_at": None, "closed_today": 0, "pending_retried": 0}


def eod_sweep(now_et=None, who: str = "eod-sweep") -> dict:
    """Close every open ledger position (and any broker position carrying one of our magic numbers that the ledger does not know)."""
    now_et = now_et or _et_now()
    with _lock:
        pos = _positions()
        closed, pending, failed, dry = [], [], [], []
        for tid, p in list(pos["open"].items()):
            res = _attempt_close(p, "EOD_SWEEP" if who == "eod-sweep" else who)
            if res.get("dry"):
                dry.append(tid); pos["open"].pop(tid, None)
            elif res.get("ok"):
                closed.append(tid); pos["open"].pop(tid, None)
            elif res.get("market_closed"):
                _queue_pending(pos, tid, p, res, who); pending.append(tid)
            else:
                failed.append(tid)
                audit("CLOSE_FAILED", symbol=p["symbol"], broker_symbol=p["broker_symbol"], trade_id=p["trade_id"], strategy=p.get("strategy"), ticket=p.get("ticket"),
                      reason="%s: %s (%s): %s -- POSITION STILL OPEN ON MT5" % (who, res.get("retcode_text") or res.get("reason"), res.get("retcode"), res.get("comment") or res.get("detail")))
        _save_positions(pos)
        # broker positions with our magic numbers that the ledger does not know (e.g. left over after a restart)
        extra = []
        if _conn["connected"] and not dry_run():
            br = B.positions()
            if br.get("ok"):
                ours = {int(x["ticket"]) for x in pos["open"].values() if x.get("ticket") is not None} | {int(x["ticket"]) for x in pos.get("pending_close", {}).values() if x.get("ticket") is not None}
                for x in br["positions"]:
                    if x.get("magic") is not None and int(x["magic"]) in all_magics() and x.get("ticket") is not None and int(x["ticket"]) not in ours:
                        p2 = {"trade_id": "ext-%s" % x["ticket"], "symbol": x.get("symbol"), "broker_symbol": x.get("symbol"), "side": ("LONG" if str(x.get("type", "")).lower() in ("0", "buy", "long") else "SHORT"),
                              "volume": x.get("volume"), "ticket": x["ticket"], "price": x.get("price_open") or x.get("price"), "magic": int(x["magic"]), "strategy": None, "contract_size": None}
                        res = _attempt_close(p2, who + "-unknown-ticket")
                        if res.get("ok"):
                            extra.append(x["ticket"])
                        elif res.get("market_closed"):
                            pos = _positions(); _queue_pending(pos, str(p2["trade_id"]), p2, res, who); _save_positions(pos)
        _sweep_state.update(last_sweep_date=now_et.date().isoformat(), last_sweep_at=_now_iso(), closed_today=len(closed) + len(extra))
        audit("EOD_SWEEP" if who == "eod-sweep" else "CLOSE_ALL", detail={"by": who, "closed": closed, "dry": dry, "pending": pending, "failed": failed, "unknown_tickets_closed": extra, "at_et": now_et.strftime("%H:%M")})
        return {"ok": not failed, "closed": closed, "dry": dry, "pending": pending, "failed": failed, "unknown_tickets_closed": extra}


def retry_pending(now_et=None) -> dict:
    """Retry every queued close (market was closed when the close was due). Called from the sweeper from PENDING_RETRY_FROM."""
    now_et = now_et or _et_now()
    with _lock:
        pos = _positions()
        q = pos.get("pending_close", {})
        done, still = [], []
        for tid, p in list(q.items()):
            res = _attempt_close(p, "PENDING_CLOSE_RETRY", attempts=1)
            if res.get("ok") or res.get("dry"):
                done.append(tid); q.pop(tid, None)
            else:
                p["attempts"] = int(p.get("attempts", 0)) + 1
                p["last_reason"] = "%s: %s" % (res.get("retcode_text") or res.get("reason"), res.get("comment") or res.get("detail"))
                still.append(tid)
                audit("CLOSE_RETRY_PENDING", symbol=p["symbol"], broker_symbol=p["broker_symbol"], trade_id=p["trade_id"], strategy=p.get("strategy"), ticket=p.get("ticket"),
                      reason="pending close attempt %d: %s" % (p["attempts"], p["last_reason"]))
        _save_positions(pos)
        _sweep_state.update(last_retry_at=_now_iso(), pending_retried=_sweep_state.get("pending_retried", 0) + len(done))
        return {"ok": True, "closed": done, "still_pending": still}


def close_all(who: str = "operator") -> dict:
    """Operator button: close every open position of ours now (ledger and broker), queue the refused ones."""
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED"}
    return eod_sweep(who="close-all:%s" % who)


def sweeper_tick(now_et=None) -> Optional[str]:
    """One decision of the background sweeper (pure: tests call it with a clock). Returns what it did, or None."""
    now_et = now_et or _et_now()
    if now_et.weekday() >= 5 or not _conn["connected"]:
        return None
    hm = (now_et.hour, now_et.minute)
    today = now_et.date().isoformat()
    if EOD_SWEEP_START <= hm < EOD_SWEEP_END:
        if _positions()["open"]:
            eod_sweep(now_et)
            return "sweep"
        if _sweep_state.get("last_sweep_date") != today:
            _sweep_state["last_sweep_date"] = today
        return None
    if PENDING_RETRY_FROM <= hm < EOD_SWEEP_START and _positions().get("pending_close"):
        last = _sweep_state.get("last_retry_at")
        if last and (time.time() - datetime.fromisoformat(last).timestamp()) < 55:
            return None
        retry_pending(now_et)
        return "retry"
    return None


_sweeper_started = {"done": False}


def start_sweeper() -> None:
    if _sweeper_started["done"]:
        return
    _sweeper_started["done"] = True

    def loop() -> None:
        time.sleep(20)
        while True:
            try:
                sweeper_tick()
            except Exception as exc:  # noqa: BLE001 - the sweeper must never die
                try:
                    audit("ERROR", reason="sweeper: %s: %s" % (type(exc).__name__, exc))
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(SWEEP_POLL_S)
    threading.Thread(target=loop, name="mt5-sweeper", daemon=True).start()


def refresh_broker_rules(who: str = "operator") -> dict:
    """Re-read every verified mapping from the broker: contract size (fallback 100 when the broker answers none) and volume rules.
    Audits REQUESTED first, then RULES_REFRESHED or ERROR: a click can never vanish silently (A.24)."""
    audit("RULES_REFRESH_REQUESTED", detail={"by": who, "connected": bool(_conn["connected"])})
    if not _conn["connected"]:
        audit("ERROR", reason="rules refresh refused: NOT_CONNECTED (connect the MT5 account first)", detail={"by": who})
        return {"ok": False, "reason": "NOT_CONNECTED", "detail": "connect the MT5 account first"}
    m = load_map()
    done, failed = [], []
    for s, e in list(m["map"].items()):
        if not (e.get("broker_symbol") and e.get("verified")):
            continue
        info = B.symbol(e["broker_symbol"])
        if not info.get("ok"):
            failed.append({"symbol": s, "reason": "%s: %s" % (info.get("reason"), info.get("detail"))})
            continue
        with _map_lock:
            m2 = load_map()
            ent = m2["map"].get(s)
            if ent:
                ent.update(contract_size=info.get("contract_size"), contract_size_source=("broker" if info.get("raw_contract") else "fallback-100"), volume_min=info.get("volume_min"), volume_max=info.get("volume_max"),
                           volume_step=info.get("volume_step"), digits=info.get("digits"), trade_mode_text=info.get("trade_mode_text"), verified_at=_now_iso())
                save_map(m2)
        done.append(s)
    audit("RULES_REFRESHED", detail={"by": who, "refreshed": len(done), "failed": failed, "sizing_valid": sizing_table()["valid"]})
    return {"ok": True, "refreshed": done, "failed": failed, "sizing": sizing_table()}


def raw_symbol_params(symbol: str) -> dict:
    """Operator diagnostics: the broker's raw SymbolParams answer for one software symbol (or broker symbol), plus what the bridge
    reads from it. Read-only; the session token never appears in the output."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"ok": False, "reason": "NO_SYMBOL"}
    e = (load_map()["map"].get(sym) or {})
    bsym = e.get("broker_symbol") or sym
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED", "broker_symbol": bsym}
    raw = B._call("SymbolParams", {"symbol": bsym})
    parsed = B.symbol(bsym)
    return {"ok": bool(raw.get("ok")), "symbol": sym, "broker_symbol": bsym, "raw": raw.get("data") if raw.get("ok") else {"reason": raw.get("reason"), "detail": raw.get("detail")},
            "parsed": {k: parsed.get(k) for k in ("contract_size", "raw_contract", "volume_min", "volume_max", "volume_step", "digits", "trade_mode_text", "raw_keys")}, "stored": {k: e.get(k) for k in ("contract_size", "contract_size_source", "volume_min", "volume_step", "verified_at")}}


def _safe_request(req: Optional[dict]) -> Optional[dict]:
    return {k: v for k, v in (req or {}).items()} if req else None


def reconcile() -> dict:
    """Operator button: compare our ledger with the broker's open positions (our magic); report positions closed by the broker
    while we were not looking and tickets we no longer see."""
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED"}
    broker = B.positions()
    if not broker.get("ok"):
        return broker
    ours_magic = all_magics()
    broker["positions"] = [x for x in broker["positions"] if x.get("magic") is None or int(x["magic"]) in ours_magic]
    live = {int(p["ticket"]): p for p in broker["positions"] if p.get("ticket") is not None}
    pos = _positions()
    gone = []
    for tid, p in list(pos["open"].items()):
        if p.get("dry") or p.get("ticket") is None:
            continue                                                                   # dry-run positions exist only in our ledger
        if int(p["ticket"]) not in live:
            deals = B.position_exit_deals(int(p["ticket"]), p.get("opened_epoch") or 0)
            why = ((deals.get("deals") or [{}])[-1].get("reason") if deals.get("ok") and deals.get("deals") else "UNKNOWN")
            audit("CLOSED_BY_BROKER", symbol=p["symbol"], broker_symbol=p["broker_symbol"], trade_id=p["trade_id"], ticket=p["ticket"], result=why, reason="found closed during reconcile")
            gone.append({"trade_id": p["trade_id"], "ticket": p["ticket"], "reason": why})
            pos["open"].pop(tid, None)
    _save_positions(pos)
    ours = {int(p["ticket"]) for p in pos["open"].values() if p.get("ticket") is not None} | {int(p["ticket"]) for p in pos.get("pending_close", {}).values() if p.get("ticket") is not None}
    unknown = [p for t, p in live.items() if t not in ours]
    audit("RECONCILE", detail={"ledger_open": len(pos["open"]), "broker_open": len(live), "closed_meanwhile": len(gone), "unknown_tickets": [p["ticket"] for p in unknown]})
    return {"ok": True, "ledger_open": list(pos["open"].values()), "broker_open": broker["positions"], "closed_meanwhile": gone, "unknown_on_broker": unknown}


_started = {"done": False}


def startup() -> None:
    """At service start: reconnect from saved credentials when the switch is on (never sends an order by itself). Runs once per
    process (the hook was firing twice and every STARTUP line was duplicated)."""
    if _started["done"]:
        return
    _started["done"] = True
    try:
        apply_exclusions(who="startup")
    except Exception as exc:  # noqa: BLE001
        _log.warning("MT5 auto-trade: exclusion list not applied: %s", exc)
    start_sweeper()
    if not auto_trade_enabled():
        _log.info("MT5 auto-trade: switch AUTO_TRADE is off")
        return
    res = reconnect_from_saved()
    audit("STARTUP", result="CONNECTED" if res.get("ok") else "NOT_CONNECTED", reason=None if res.get("ok") else "%s: %s" % (res.get("reason"), res.get("detail")))
