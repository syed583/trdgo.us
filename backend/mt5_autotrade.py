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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import mt5_bridge as B

_log = logging.getLogger("mt5")
_lock = threading.RLock()

DEFAULT_SERVER = "STGlobalMarketsLtd-Live-1"
MAGIC = int(os.getenv("MT5_MAGIC") or 240911)                       # identifies our orders on the account
STATE_DIR = Path(os.getenv("MT5_STATE_DIR") or (Path(__file__).parent / "mt5_state"))
CRED_FILE = Path(os.getenv("SEC_CACHE_DIR") or (Path(__file__).parent / ".sec_cache")) / "mt5_credentials.json"
MAP_FILE, AUDIT_FILE, POS_FILE, PAUSE_FILE = "symbol_map.json", "audit.jsonl", "positions.json", "PAUSE"
MAX_CLOSE_ATTEMPTS = 3
DEVIATION_POINTS = int(os.getenv("MT5_DEVIATION_POINTS") or 50)       # max slippage for a market order, in broker points
AUDIT_TAIL = 2000


# ----------------------------------------------------------------------------- switches and state files
def auto_trade_enabled() -> bool:
    """The master switch: env AUTO_TRADE=1. Default OFF."""
    return (os.getenv("AUTO_TRADE") or "0").strip().lower() in ("1", "true", "yes", "on")


def paused() -> bool:
    return (STATE_DIR / PAUSE_FILE).exists()


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


def connect(login: int, password: str, server: str, remember: bool = True, base_url: Optional[str] = None) -> dict:
    with _lock:
        base = (base_url or B.DEFAULT_BASE_URL).strip()
        res = B.connect(int(login), password, server, base_url=base)
        if res.get("ok"):
            _conn.update(connected=True, account=res["account"], since=_now_iso(), last_error=None, server=server, base_url=base)
            if remember:
                save_credentials(int(login), password, server, base)
            audit("CONNECTED", detail={"login": int(login), "server": server, "base_url": base, "account_kind": (res["account"] or {}).get("account_kind")})
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


def status() -> dict:
    with _lock:
        live = B.account() if (_conn["connected"] and B.available()) else None
        if live and not live.get("ok"):
            _conn.update(connected=False, account=None, last_error="%s: %s" % (live.get("reason"), live.get("detail")))
        elif live:
            _conn["account"] = live["account"]
        return {"installed": True, "install_note": None, "provider": "mtapi.io MT5 Client REST API", "base_url": _conn["base_url"] or B.DEFAULT_BASE_URL,
                "connected": bool(_conn["connected"]), "account": _conn["account"], "server": _conn["server"] or DEFAULT_SERVER, "since": _conn["since"],
                "last_error": _conn["last_error"], "credentials_saved": CRED_FILE.exists() or bool(os.getenv("MT5_LOGIN")),
                "auto_trade": auto_trade_enabled(), "paused": paused(), "magic": MAGIC, "default_server": DEFAULT_SERVER,
                "terminal": (B.terminal().get("terminal") if (_conn["connected"] and B.available()) else None)}


# ----------------------------------------------------------------------------- symbol mapping
def software_symbols() -> list:
    """Every symbol the Strategy Trades tab can trade: the intraday boards' liquid universe plus any symbol already in the paper book."""
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


def load_map() -> dict:
    return _read_json(STATE_DIR / MAP_FILE, {"version": 1, "map": {}})


def save_map(m: dict) -> None:
    _write_json(STATE_DIR / MAP_FILE, m)


def mapping_rows() -> dict:
    m = load_map()["map"]
    rows = []
    for s in software_symbols():
        e = m.get(s) or {}
        rows.append({"symbol": s, "broker_symbol": e.get("broker_symbol"), "status": "MAPPED" if e.get("broker_symbol") and e.get("verified") else ("UNVERIFIED" if e.get("broker_symbol") else "UNMAPPED"),
                     "verified_at": e.get("verified_at"), "source": e.get("source"), "volume_min": e.get("volume_min"), "volume_step": e.get("volume_step"), "trade_mode": e.get("trade_mode_text"), "note": e.get("note")})
    mapped = sum(1 for r in rows if r["status"] == "MAPPED")
    return {"rows": rows, "mapped": mapped, "total": len(rows), "summary": "%d of %d symbols mapped" % (mapped, len(rows))}


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
    m = load_map()
    if not broker_symbol:
        m["map"].pop(symbol, None)
        save_map(m)
        audit("UNMAPPED", symbol=symbol, detail={"source": source})
        return {"ok": True, "symbol": symbol, "status": "UNMAPPED"}
    v = verify_broker_symbol(broker_symbol) if _conn["connected"] else {"ok": False, "reason": "NOT_CONNECTED"}
    e = {"broker_symbol": broker_symbol, "source": source, "verified": bool(v.get("ok")), "verified_at": _now_iso() if v.get("ok") else None, "note": None if v.get("ok") else "%s: %s" % (v.get("reason"), v.get("detail", ""))}
    if v.get("ok"):
        i = v["info"]
        e.update(volume_min=i.get("volume_min"), volume_max=i.get("volume_max"), volume_step=i.get("volume_step"), digits=i.get("digits"), trade_mode_text=i.get("trade_mode_text"),
                 filling_ioc=i.get("filling_ioc"), filling_fok=i.get("filling_fok"))
    m["map"][symbol] = e
    save_map(m)
    audit("MAPPED" if e["verified"] else "MAP_UNVERIFIED", symbol=symbol, broker_symbol=broker_symbol, reason=e.get("note"), detail={"source": source})
    return {"ok": True, "symbol": symbol, "broker_symbol": broker_symbol, "status": "MAPPED" if e["verified"] else "UNVERIFIED", "note": e.get("note")}


def automap(candidates: Optional[dict] = None) -> dict:
    """For every software symbol without a verified mapping, try the plain ticker and common broker suffixes."""
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED", "detail": "connect the MT5 account first"}
    suffixes = [x.strip() for x in (os.getenv("MT5_SYMBOL_SUFFIXES") or ",.NAS,.NYSE,.US,.m").split(",")]
    m = load_map()
    done, failed = [], []
    for s in software_symbols():
        e = m["map"].get(s) or {}
        if e.get("verified"):
            continue
        tried = []
        hit = None
        for suf in ([e["broker_symbol"]] if e.get("broker_symbol") else []) + [s + suf for suf in suffixes]:
            tried.append(suf)
            v = verify_broker_symbol(suf)
            if v.get("ok"):
                hit = suf
                break
        if hit:
            set_mapping(s, hit, source="auto")
            done.append({"symbol": s, "broker_symbol": hit})
        else:
            failed.append({"symbol": s, "tried": tried})
    audit("AUTOMAP", detail={"mapped": len(done), "unmapped": len(failed)})
    mr = mapping_rows()
    return {"ok": True, "mapped": done, "unmapped": failed, "mapped_count": mr["mapped"], "total": mr["total"], "summary": mr["summary"]}


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
    if not B.available():
        return "NOT_INSTALLED"
    if not _conn["connected"]:
        return "NOT_CONNECTED"
    if not mapped(ev["symbol"]):
        return "UNMAPPED"
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
    same = [p for p in pos["open"].values() if p["symbol"] == sym]
    if any(p["side"] == side for p in same):
        audit("SKIPPED", symbol=sym, broker_symbol=bsym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat, reason="SKIPPED_DUPLICATE: an MT5 position for this symbol and side is already open (trade #%s)" % same[0]["trade_id"])
        return {"ok": False, "reason": "SKIPPED_DUPLICATE"}
    if same:
        audit("SKIPPED", symbol=sym, broker_symbol=bsym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat, reason="SKIPPED_OPPOSITE_OPEN: an MT5 position in the other direction is open (trade #%s)" % same[0]["trade_id"])
        return {"ok": False, "reason": "SKIPPED_OPPOSITE_OPEN"}
    info = B.symbol(bsym)
    if not info.get("ok"):
        audit("REJECTED", symbol=sym, broker_symbol=bsym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat, reason="%s: %s" % (info.get("reason"), info.get("detail")))
        return {"ok": False, "reason": info.get("reason")}
    if info.get("volume_min") is None or info.get("volume_step") is None:
        audit("REJECTED", symbol=sym, broker_symbol=bsym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat, reason="NO_VOLUME_RULES: %s" % info.get("note"), detail={"raw_keys": info.get("raw_keys")})
        return {"ok": False, "reason": "NO_VOLUME_RULES"}
    lots = shares_to_lots(ev["shares"], info.get("contract_size"))
    vol = round_volume(lots, info.get("volume_min"), info.get("volume_step"), info.get("volume_max"))
    if vol is None:
        audit("SKIPPED", symbol=sym, broker_symbol=bsym, action=side, quantity=ev.get("shares"), trade_id=tid, strategy=strat,
              reason="BELOW_MIN_VOLUME: %s shares = %.4f lots (contract size %s) rounds below the broker minimum %s lots (step %s)" % (ev.get("shares"), lots, info.get("contract_size"), info.get("volume_min"), info.get("volume_step")))
        return {"ok": False, "reason": "BELOW_MIN_VOLUME"}
    comment = "TRDGO %s #%s" % ((strat or "")[:6], tid)
    res = B.market_order(bsym, side, vol, sl=ev.get("stop"), tp=ev.get("target"), deviation_points=DEVIATION_POINTS, magic=MAGIC, comment=comment, sym_info=info)
    if not res.get("ok") and res.get("retcode_text") == "REPRICE":
        audit("REPRICE", symbol=sym, broker_symbol=bsym, action=side, quantity=vol, trade_id=tid, strategy=strat, reason="%s: %s" % (res.get("retcode_text"), res.get("comment")))
        res = B.market_order(bsym, side, vol, sl=ev.get("stop"), tp=ev.get("target"), deviation_points=DEVIATION_POINTS, magic=MAGIC, comment=comment, sym_info=info)
    if not res.get("ok"):
        audit("REJECTED", symbol=sym, broker_symbol=bsym, action=side, quantity=vol, trade_id=tid, strategy=strat,
              reason="%s (%s): %s" % (res.get("retcode_text") or res.get("reason"), res.get("retcode"), res.get("comment") or res.get("detail")),
              detail={"stage": res.get("stage"), "request": _safe_request(res.get("request"))})
        return {"ok": False, "reason": res.get("retcode_text") or res.get("reason")}
    filled = float(res.get("volume") or vol)
    ticket = res.get("order") or res.get("deal")
    pos["open"][str(tid)] = {"trade_id": tid, "symbol": sym, "broker_symbol": bsym, "side": side, "volume": filled, "requested": vol, "ticket": ticket, "deal": res.get("deal"),
                             "price": res.get("price"), "sl": ev.get("stop"), "tp": ev.get("target"), "opened_at": _now_iso(), "opened_epoch": time.time(), "strategy": strat}
    _save_positions(pos)
    audit("PARTIAL_FILL" if res.get("partial") else "FILLED", symbol=sym, broker_symbol=bsym, action=side, quantity=filled, trade_id=tid, strategy=strat, ticket=ticket, price=res.get("price"),
          result=res.get("retcode_text"), reason=("requested %s lots, filled %s" % (vol, filled)) if res.get("partial") else None,
          detail={"sl": ev.get("stop"), "tp": ev.get("target"), "paper_shares": ev.get("shares"), "lots": vol, "contract_size": info.get("contract_size")})
    return {"ok": True, "ticket": ticket, "volume": filled}


def _handle_exit(ev: dict) -> dict:
    tid, reason = ev["trade_id"], ev.get("reason")
    pos = _positions()
    p = pos["open"].get(str(tid))
    if not p:
        return {"ok": True, "reason": "NO_MT5_POSITION"}                    # the paper trade never traded on MT5 (switch off, unmapped, skipped ...)
    sym, bsym, ticket = p["symbol"], p["broker_symbol"], p["ticket"]
    # did the broker close it already (server-side SL / TP / stop-out)?
    deals = B.position_exit_deals(int(ticket), p.get("opened_epoch") or 0) if B.available() else {"ok": False}
    exits = (deals.get("deals") or []) if deals.get("ok") else []
    still = B.positions(bsym).get("positions") or [] if B.available() else []
    still_open = any(int(x.get("ticket") or 0) == int(ticket) for x in still)
    if exits and not still_open:
        why = exits[-1].get("reason")
        audit("CLOSED_BY_BROKER", symbol=sym, broker_symbol=bsym, action=("SELL" if p["side"] in ("BUY", "LONG") else "BUY"), quantity=p.get("volume"), trade_id=tid, strategy=p.get("strategy"),
              ticket=ticket, price=exits[-1].get("price"), result=why, reason="%s hit on MT5 (paper exit: %s)" % (why, reason), detail={"deals": exits})
        pos["open"].pop(str(tid), None)
        _save_positions(pos)
        return {"ok": True, "closed_by": why}
    last = None
    for attempt in range(1, MAX_CLOSE_ATTEMPTS + 1):
        res = B.close_position(int(ticket), deviation_points=DEVIATION_POINTS * attempt, magic=MAGIC, comment="TRDGO %s #%s" % ((reason or "EXIT")[:10], tid))
        last = res
        if res.get("ok"):
            audit("CLOSED", symbol=sym, broker_symbol=bsym, action=("SELL" if p["side"] in ("BUY", "LONG") else "BUY"), quantity=res.get("volume") or p.get("volume"), trade_id=tid,
                  strategy=p.get("strategy"), ticket=ticket, price=res.get("price"), result=res.get("retcode_text") or ("ALREADY_CLOSED" if res.get("already_closed") else "DONE"),
                  reason="paper exit %s (attempt %d)" % (reason, attempt))
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


def _safe_request(req: Optional[dict]) -> Optional[dict]:
    return {k: v for k, v in (req or {}).items()} if req else None


def reconcile() -> dict:
    """Operator button: compare our ledger with the broker's open positions (our magic); report positions closed by the broker
    while we were not looking and tickets we no longer see."""
    if not _conn["connected"]:
        return {"ok": False, "reason": "NOT_CONNECTED"}
    broker = B.positions(magic=MAGIC)
    if not broker.get("ok"):
        return broker
    live = {int(p["ticket"]): p for p in broker["positions"]}
    pos = _positions()
    gone = []
    for tid, p in list(pos["open"].items()):
        if int(p["ticket"]) not in live:
            deals = B.position_exit_deals(int(p["ticket"]), p.get("opened_epoch") or 0)
            why = ((deals.get("deals") or [{}])[-1].get("reason") if deals.get("ok") and deals.get("deals") else "UNKNOWN")
            audit("CLOSED_BY_BROKER", symbol=p["symbol"], broker_symbol=p["broker_symbol"], trade_id=p["trade_id"], ticket=p["ticket"], result=why, reason="found closed during reconcile")
            gone.append({"trade_id": p["trade_id"], "ticket": p["ticket"], "reason": why})
            pos["open"].pop(tid, None)
    _save_positions(pos)
    ours = {int(p["ticket"]) for p in pos["open"].values()}
    unknown = [p for t, p in live.items() if t not in ours]
    audit("RECONCILE", detail={"ledger_open": len(pos["open"]), "broker_open": len(live), "closed_meanwhile": len(gone), "unknown_tickets": [p["ticket"] for p in unknown]})
    return {"ok": True, "ledger_open": list(pos["open"].values()), "broker_open": broker["positions"], "closed_meanwhile": gone, "unknown_on_broker": unknown}


def startup() -> None:
    """At service start: reconnect from saved credentials when the switch is on (never sends an order by itself)."""
    if not auto_trade_enabled():
        _log.info("MT5 auto-trade: switch AUTO_TRADE is off")
        return
    res = reconnect_from_saved()
    audit("STARTUP", result="CONNECTED" if res.get("ok") else "NOT_CONNECTED", reason=None if res.get("ok") else "%s: %s" % (res.get("reason"), res.get("detail")))
