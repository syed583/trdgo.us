"""15-minute candle analysis and confirmation.

The live engine reads 5-minute bars. This module adds a SECOND, slower timeframe built from real
15-minute OHLC candles (the provider's native `/ohlc/15m`, not 5-minute bars stitched together) and
asks one question of a BUY / SELL that every other gate has already passed:

    do the closed 15-minute candles agree with it?

It never changes a score. It only decides whether a signal may be shown as actionable:

    CONFIRMED  the 15-minute picture agrees with the side         -> the signal stands
    CONFLICT   the 15-minute picture points the other way         -> WAIT
    MIXED      no clear 15-minute direction                       -> WAIT
    STALE      the newest closed candle is older than it may be   -> WAIT
    MISSING    never fetched / not available                      -> WAIT
    INVALID    the candles failed validation (holes, bad OHLC)    -> WAIT
    WARMUP     the first 15-minute candle of the session has not closed (09:45 ET) -> WAIT

Only CLOSED candles are used (the forming candle changes every second and confirms nothing). Every
candle is validated before it is trusted: finite positive prices, high >= open/close/low, low <=
open/close/high, a volume, a 15-minute-aligned start inside regular hours, no duplicates, and no hole
in the newest few candles of today. Bad candles are dropped and counted, never repaired.

Direction is a three-vote reading of the closed candles (each vote +1 / 0 / -1):
    price vs session VWAP (typical price x volume, today's closed candles)
    EMA(9) vs EMA(21) of the closes
    RSI(14) of the closes (>= rsi_bull bullish, <= rsi_bear bearish)
Two or more agreeing votes with none against decide a direction; anything else is MIXED.

Cost: one request per symbol per CLOSED candle (a candle changes only when it closes), i.e. at most
26 per symbol per session -- and the refresh waits for the boundary instead of polling.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import tab_settings as cfg
import uw_budget
import uw_priority as pri

KEY = "intra15:{}"
UNSAFE_BUILD: Optional[str] = None       # set at boot if this build fails its own release check: no entries at all
MODULE = "candles_15m"
BAR_S = 15 * 60


def _p() -> dict:
    return cfg.INTRADAY_15M


# --------------------------------------------------------------- validation ----
def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate_bars(rows: list, now: Optional[datetime] = None) -> dict:
    """Closed, valid, regular-hours 15-minute candles, oldest first.

    Returns {"bars": [...], "rejected": {reason: n}, "forming": bool}. Pure -- no I/O."""
    import intraday_indicators as ind
    now = now or ind.now_et()
    seen: dict = {}
    rejected: dict = {}
    forming = False

    def bad(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for r in rows or []:
        if not isinstance(r, dict):
            bad("not a candle")
            continue
        dt = ind._et(r.get("start_time") or r.get("t") or r.get("date"))
        o, h, l, c = (_num(r.get(k)) for k in ("open", "high", "low", "close"))
        v = _num(r.get("volume"))
        if dt is None:
            bad("no start time")
            continue
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            bad("missing or non-positive price")
            continue
        eps = 1e-9 * max(h, 1.0)
        if h + eps < max(o, c, l) or l - eps > min(o, c, h):
            bad("inconsistent OHLC")
            continue
        if v is None or v < 0:
            bad("missing volume")
            continue
        if dt.second or dt.minute % 15:
            bad("not aligned to 15 minutes")
            continue
        if dt.weekday() >= 5 or not ind._is_rth(dt):
            bad("outside regular hours")
            continue
        if dt in seen:
            bad("duplicate")
            continue
        if dt + timedelta(seconds=BAR_S) > now:
            forming = True                       # still forming: never confirms anything
            continue
        seen[dt] = {"dt": dt, "o": o, "h": h, "l": l, "c": c, "v": v}
    bars = [seen[k] for k in sorted(seen)]
    return {"bars": bars, "rejected": rejected, "forming": forming}


def _contiguous_tail(bars: list, n: int, today) -> bool:
    """The newest ``n`` closed candles of today follow each other with no hole."""
    tod = [b for b in bars if b["dt"].date() == today]
    tail = tod[-n:]
    return all((tail[i]["dt"] - tail[i - 1]["dt"]).total_seconds() == BAR_S for i in range(1, len(tail)))


# ----------------------------------------------------------------- analysis ----
def _ema(vals: list, span: int) -> Optional[float]:
    if not vals:
        return None
    k = 2.0 / (span + 1)
    e = vals[0]
    for x in vals[1:]:
        e = x * k + e * (1 - k)
    return e


def _rsi(closes: list, period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def analyse(rows: list, now: Optional[datetime] = None) -> dict:
    """The 15-minute reading of the raw candle rows. Pure. status OK | WARMUP | INVALID."""
    import intraday_indicators as ind
    p = _p()
    now = now or ind.now_et()
    v = validate_bars(rows, now)
    bars = v["bars"]
    base = {"rejected": v["rejected"], "bars": len(bars), "timeframe": "15m"}
    if not bars:
        return dict(base, status="INVALID", detail="no valid closed 15-minute candle")
    today = now.date()
    sess = [b for b in bars if b["dt"].date() == today]
    last = bars[-1]
    base.update(last_bar_start=last["dt"].isoformat(),
                last_bar_end_epoch=(last["dt"] + timedelta(seconds=BAR_S)).timestamp(),
                session_bars=len(sess))
    if len(sess) < p["min_session_bars"]:
        return dict(base, status="WARMUP",
                    detail=f"{len(sess)} closed 15-minute candle(s) today (needs {p['min_session_bars']}; "
                           f"the first closes 09:45 ET)")
    if len(bars) < p["min_bars"]:
        return dict(base, status="INVALID", detail=f"only {len(bars)} valid candles (needs {p['min_bars']})")
    if not _contiguous_tail(bars, p["contiguous_bars"], today):
        return dict(base, status="INVALID", detail=f"a candle is missing from today's last {p['contiguous_bars']}")
    closes = [b["c"] for b in bars]
    num = den = 0.0
    for b in sess:
        num += (b["h"] + b["l"] + b["c"]) / 3.0 * b["v"]
        den += b["v"]
    vwap = (num / den) if den > 0 else None
    e_fast, e_slow = _ema(closes[-60:], p["ema_fast"]), _ema(closes[-100:], p["ema_slow"])
    rsi = _rsi(closes[-100:], p["rsi_period"])
    if vwap is None or e_fast is None or e_slow is None or rsi is None:
        return dict(base, status="INVALID", detail="VWAP / EMA / RSI could not be computed")
    px = last["c"]
    v_vwap = 1 if px > vwap else -1 if px < vwap else 0
    v_ema = 1 if e_fast > e_slow else -1 if e_fast < e_slow else 0
    v_rsi = 1 if rsi >= p["rsi_bull"] else -1 if rsi <= p["rsi_bear"] else 0
    votes = {"vwap": v_vwap, "ema": v_ema, "rsi": v_rsi}
    total = v_vwap + v_ema + v_rsi
    bias = 1 if total >= 2 else -1 if total <= -2 else 0
    word = {1: "bullish", -1: "bearish", 0: "mixed"}[bias]
    detail = (f"15m {word}: close {px:.2f} {'>' if v_vwap > 0 else '<' if v_vwap < 0 else '='} VWAP {vwap:.2f}, "
              f"EMA{p['ema_fast']} {e_fast:.2f} {'>' if v_ema > 0 else '<' if v_ema < 0 else '='} "
              f"EMA{p['ema_slow']} {e_slow:.2f}, RSI {rsi:.0f}")
    return dict(base, status="OK", bias=bias, votes=votes, close=px, vwap=round(vwap, 4),
                ema_fast=round(e_fast, 4), ema_slow=round(e_slow, 4), rsi=round(rsi, 1), detail=detail)


# ------------------------------------------------------------------- store -----
def compute(symbol: str) -> Optional[dict]:
    """One fetch + analysis for one symbol (this DOES call the API: one request)."""
    import unusualwhales_service as uw
    symbol = symbol.upper()
    with pri.use(pri.ESSENTIAL, MODULE):
        out = uw.candles(symbol, "15m", limit=int(_p()["history_bars"]))
    if (out or {}).get("status") != "OK":
        return None
    a = analyse(uw._rows(out))
    return dict(a, symbol=symbol, ts=time.time())


def store(symbol: str, value: dict) -> None:
    import swr
    swr._run(KEY.format(symbol.upper()), lambda: value)


def read(symbol: str) -> Optional[dict]:
    """The last stored 15-minute reading (cache read only, never an API call)."""
    import swr
    v = swr.peek(KEY.format(symbol.upper()))
    return v if isinstance(v, dict) and v.get("ts") else None


def due(v: Optional[dict], now: float) -> bool:
    """Does this reading need a refetch? Only when a newer candle should have closed since it."""
    if not v:
        return True
    if now - v["ts"] < 60.0:                               # never hammer a symbol
        return False
    end = v.get("last_bar_end_epoch")
    if end is None:                                        # nothing usable last time: try again
        return True
    return now >= end + BAR_S + float(_p()["recheck_after_close_s"])


def assert_enforced() -> bool:
    """Boot check. The confirmation has NO switch: there is no setting, no environment variable and no argument
    that turns it off -- it is part of ``entry_check``, which every code path that opens a trade calls. This only
    reports (loudly) if someone tries to configure around it."""
    import logging
    import os
    if os.environ.get("ALLOW_UNCONFIRMED_ENTRIES") or "require_confirmation" in _p():
        logging.critical("15-minute confirmation: an attempt to switch it off was found (ALLOW_UNCONFIRMED_ENTRIES / "
                         "require_confirmation). There is no such override; it is ignored.")
    return True


def boot_self_check() -> Optional[str]:
    """At boot, run the release checker over THIS build's files. A partial deploy (a missing or altered safety file)
    must not trade: if it fails, every entry is refused until the build is fixed. Returns the failure text or None."""
    global UNSAFE_BUILD
    import logging
    try:
        import release_check
        result = release_check.check(release_check.read_tree())
    except Exception as exc:  # noqa: BLE001
        UNSAFE_BUILD = f"the release check could not run ({type(exc).__name__})"
    else:
        bad = [c["check"] for c in result["checks"] if not c["ok"]]
        UNSAFE_BUILD = ("; ".join(bad[:4]) + (" ..." if len(bad) > 4 else "")) if bad else None
    if UNSAFE_BUILD:
        logging.critical("BUILD FAILED ITS SAFETY CHECK -- all new entries are refused: %s", UNSAFE_BUILD)
    return UNSAFE_BUILD


def database_info() -> dict:
    """Which database engine this process uses (engine name and whether it is behind a pooler; never a credential)."""
    try:
        import database
        url = str(database.engine.url)
        return {"dialect": database.engine.dialect.name, "driver": database.engine.dialect.driver,
                "pooler": ":6543" in url or "pooler." in url, "production_db": database.is_production()}
    except Exception as exc:  # noqa: BLE001
        return {"dialect": None, "error": type(exc).__name__}


def build_info() -> dict:
    """Which build is running (git commit), for the deploy / rollback checks. None when git is unavailable."""
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
        return {"commit": sha.stdout.strip() or None, "uncommitted_changes": bool(dirty.stdout.strip())}
    except Exception:  # noqa: BLE001
        return {"commit": None, "uncommitted_changes": None}


_BOOT_BUILD: Optional[dict] = None


def status() -> dict:
    """What a deploy / rollback check reads from a RUNNING build. ``build`` is the commit this PROCESS started
    with; ``tree_commit_now`` is what is on disk now -- they differ if files were replaced under a running service."""
    import os
    global _BOOT_BUILD
    if _BOOT_BUILD is None:
        _BOOT_BUILD = build_info()
    now_tree = build_info()
    try:
        import entry_lock
        lock = {"locked": entry_lock.is_locked(fresh=True)}
    except Exception:  # noqa: BLE001
        lock = {"locked": None}
    return {"confirm15_required": True, "can_be_disabled": False, "bar_minutes": _p().get("bar_minutes"),
            "environment": (os.environ.get("APP_ENV") or "").strip().lower() or None,
            "entry_lock": lock, "build": _BOOT_BUILD, "tree_commit_now": now_tree.get("commit"),
            "running_matches_tree": bool(_BOOT_BUILD.get("commit")) and _BOOT_BUILD.get("commit") == now_tree.get("commit"),
            "unsafe_build": UNSAFE_BUILD, "database": database_info()}


# ------------------------------------------------------------ confirmation -----
def confirmation(symbol: str, side: Optional[str], phase: Optional[str] = "rth",
                 now: Optional[float] = None) -> dict:
    """Does the 15-minute picture confirm ``side`` (BUY / SELL)? Cache read only.

    {"applies", "status", "confirmed", ...}. ``confirmed`` is None when it does not apply (outside the
    regular session) and otherwise True only for a fresh, valid, agreeing reading."""
    from intraday_safety import dubai
    now = time.time() if now is None else now
    if phase != "rth":
        return {"applies": False, "status": "N/A", "confirmed": None, "side": side, "timeframe": "15m",
                "detail": "15-minute confirmation applies in the regular session only"}
    v = read(symbol)
    out = {"applies": True, "side": side, "timeframe": "15m", "confirmed": False}
    if not v:
        return dict(out, status="MISSING", detail="no 15-minute candles fetched yet")
    out.update(bars=v.get("bars"), rejected=v.get("rejected") or {}, fetched_dubai=dubai(v["ts"]))
    st = v.get("status")
    if st != "OK":
        return dict(out, status=st or "INVALID", detail=v.get("detail") or "no usable 15-minute reading")
    end = float(v["last_bar_end_epoch"])
    age = max(0.0, now - end)
    out.update(last_bar_end_dubai=dubai(end), age_s=round(age), bias=v["bias"], votes=v["votes"])
    if age > float(_p()["max_close_lag_s"]):
        return dict(out, status="STALE",
                    detail=f"newest closed 15-minute candle ended {age / 60:.0f} min ago "
                           f"(max {float(_p()['max_close_lag_s']) / 60:.0f})")
    want = 1 if side == "BUY" else -1 if side == "SELL" else 0
    if v["bias"] == 0:
        return dict(out, status="MIXED", detail=v["detail"])
    if v["bias"] != want:
        return dict(out, status="CONFLICT", detail=v["detail"])
    return dict(out, status="CONFIRMED", confirmed=True, detail=v["detail"])


def entry_check(symbol: str, side: Optional[str], phase: Optional[str] = None, *, execution: bool = False,
                now: Optional[float] = None) -> dict:
    """May a NEW BUY / SELL entry be opened for ``symbol`` right now? Cache reads only. FAIL CLOSED: any error
    inside the check (a missing module, a broken cache, an unreadable lock) means NO entry -- and never an
    exception that could abort the caller's transaction, which also carries its exits.

    (Details: see ``_entry_check`` below.)"""
    if UNSAFE_BUILD:
        return {"allowed": False, "status": "BUILD_FAILED_SAFETY_CHECK", "confirm15": None,
                "reason": f"this build failed its own safety check at boot: {UNSAFE_BUILD}"}
    try:
        return _entry_check(symbol, side, phase, execution=execution, now=now)
    except Exception as exc:  # noqa: BLE001
        return {"allowed": False, "status": "GATE_ERROR", "confirm15": None,
                "reason": f"the entry gate failed ({type(exc).__name__}); refusing a new entry"}


def _entry_check(symbol: str, side: Optional[str], phase: Optional[str] = None, *, execution: bool = False,
                 now: Optional[float] = None) -> dict:
    """The implementation of ``entry_check``.

    This is the one gate every path that opens a trade (and every board that would show a tradable BUY / SELL)
    goes through. There is no way to configure it away. Allowed only when ALL of these hold:

      * the side is BUY or SELL and we are in the regular session (a 15-minute reading exists only there);
      * the newest CLOSED 15-minute candle is fresh, valid and AGREES with the side (status CONFIRMED);
      * (``execution=True``, i.e. the moment of opening) the operator's entry lock is off.

    Missing, stale, invalid, warming-up, mixed, conflicting or unavailable readings, an unreadable lock state
    and any time outside the regular session all return allowed=False. Exits and other risk-reducing actions
    never call this."""
    if side not in ("BUY", "SELL"):
        return {"allowed": False, "status": "NO_SIDE", "reason": "not a BUY or SELL", "confirm15": None}
    import market_calendar
    kind = market_calendar.instrument_kind(symbol)
    if kind != "equity":                    # indexes cannot be traded; futures follow a calendar that is not modelled
        return {"allowed": False, "status": "UNSUPPORTED_INSTRUMENT", "confirm15": None,
                "reason": f"{symbol} is a {kind}: only US stocks / ETFs (the modelled session) may open"}
    if phase is None:
        try:
            import intraday_indicators as ind
            phase = ind.market_phase()
        except Exception:  # noqa: BLE001
            phase = None
    c = confirmation(symbol, side, "rth" if phase == "rth" else (phase or "unknown"), now)
    if phase != "rth":
        return {"allowed": False, "status": "OUTSIDE_SESSION", "confirm15": c,
                "reason": "new entries need a confirmed 15-minute reading, which exists only in the regular session"}
    if not c.get("confirmed"):
        return {"allowed": False, "status": c.get("status") or "UNAVAILABLE", "confirm15": c,
                "reason": f"15-minute candles do not confirm the {side} ({c.get('status')}): {c.get('detail') or ''}".strip()}
    if execution:
        try:
            import entry_lock
            if entry_lock.is_locked(fresh=True):
                return {"allowed": False, "status": "ENTRIES_LOCKED", "confirm15": c,
                        "reason": "new entries are locked by the operator (entry_lock)"}
        except Exception:  # noqa: BLE001
            return {"allowed": False, "status": "ENTRIES_LOCKED", "confirm15": c,
                    "reason": "the entry lock could not be read: refusing a new entry"}
    return {"allowed": True, "status": "CONFIRMED", "confirm15": c, "reason": ""}


# ----------------------------------------------------------------- refresh -----
def cycle(symbols: list, phase: Optional[str], max_symbols: int = 20, now: Optional[float] = None,
          fetch: Callable = compute) -> dict:
    """Refetch the symbols whose newest candle has closed since the last look, interesting names first,
    inside the shared budget and this module's hourly cap."""
    import intraday_feed
    out = {"refreshed": [], "failed": [], "skipped": [], "due": 0}
    if phase != "rth":
        return out
    now = time.time() if now is None else now
    ctl = uw_budget.get()
    cap = float(cfg.UW_BUDGET.get("candles15_hourly_cap", 160))
    hot = intraday_feed.interest()
    todo = [(hot.get(s, 0.0), s) for s in symbols if due(read(s), now)]
    todo.sort(reverse=True)
    out["due"] = len(todo)
    for _i, s in todo[:max_symbols]:
        if ctl.module_hour_calls(MODULE) >= cap:
            out["skipped"].append((s, "CANDLES15_HOURLY_CAP"))
            break
        d = ctl.peek(pri.ESSENTIAL)
        if not d.ok:
            out["skipped"].append((s, d.code))
            break
        try:
            val = fetch(s)
        except Exception:  # noqa: BLE001
            val = None
        if val:
            store(s, val)
            out["refreshed"].append(s)
        else:
            out["failed"].append(s)
    return out
