"""Can the NEW live engine safely recover and manage the signals that are open at a forward cut-over?

A cut-over used to require ZERO open live signals. The old engine, however, stops scanning at about 15:30 ET and so
leaves late signals open every day, which blocked every window. The new engine manages every open signal inside the carry
window (tab_settings.INTRADAY_LIFECYCLE) by its own rules, so a forward cut-over may proceed with them -- but only if EACH
open signal passes the checks below. A single problem blocks the cut-over (fail closed). Open PAPER POSITIONS
(demo_trades) are a separate rule and still must be zero.

Nothing here writes. Standard library only (the checks also run, as an embedded copy, on a server that still runs the OLD
build and has none of this application's newer modules).

What is checked, per open signal inside the carry window:
  * the table has every column the engine reads;
  * state and side agree (BUY / STRONG BUY with side BUY; SELL / STRONG SELL with side SELL) and the row is open;
  * the symbol is a US stock / ETF the engine can price (not an index, not a future, not malformed);
  * timestamps: session_date is a real date inside the carry window and not in the future; signal_ts exists, is not in
    the future and falls on session_date (Eastern); status_ts is not before signal_ts;
  * prices: entry and best_price are positive numbers; tp / sl are positive and on the correct side of the entry (BUY:
    sl < entry < tp, SELL: tp < entry < sl) -- both may be empty only when there is no ATR (the engine opens such rows);
  * no symbol has more than one open signal (a duplicate, and above all an opposite-side pair, is a conflict).
Rows OLDER than the carry window are STALE: the engine never manages or closes them, they cannot be recovered, and they
are reported but do not block (see intraday_signal_v2.expire_stale for the operator-only way to retire them).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = ("id", "symbol", "session_date", "state", "side", "is_open", "entry", "tp", "sl", "best_price", "atr",
                    "be_moved", "score_in", "signal_ts", "status_ts")
_BUY_STATES, _SELL_STATES = ("BUY", "STRONG BUY"), ("SELL", "STRONG SELL")
_INDEX_ROOTS = frozenset({"SPX", "SPXW", "NDX", "NDXP", "RUT", "RUTW", "INDU", "DJX", "VIX", "VIXW", "COMP", "OEX", "XSP"})
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_FUTURE_SKEW = timedelta(minutes=5)


def instrument_kind(symbol: Any) -> str:
    """'equity' | 'index' | 'future' | 'unknown' -- the same rule as market_calendar.instrument_kind (a test pins them together)."""
    s = (str(symbol) if symbol is not None else "").strip().upper()
    if not s:
        return "unknown"
    if s.startswith("/") or s.endswith("=F"):
        return "future"
    if s.startswith("^") or s in _INDEX_ROOTS:
        return "index"
    return "equity" if s.replace(".", "").replace("-", "").isalnum() else "unknown"


def _ts(v: Any) -> Optional[datetime]:
    """A timestamp from a database driver (datetime) or a text column (ISO). Naive values are taken as UTC."""
    if v is None:
        return None
    try:
        if isinstance(v, datetime):
            d = v
        elif isinstance(v, str):
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        else:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _day(v: Any) -> Optional[date]:
    try:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            return date.fromisoformat(v.strip()[:10])
    except (ValueError, TypeError):
        pass
    return None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


def validate(rows: Iterable[dict], *, columns: Optional[Iterable[str]] = None, now: Optional[datetime] = None,
             cutoff: Any = None) -> dict:
    """Judge the OPEN signal rows. ``cutoff``: sessions older than this ISO date are stale. Returns
    {"ok", "fresh", "stale", "problems": [...], "fresh_rows": [{id, symbol, session, side}]}. Never raises."""
    problems: list[str] = []
    fresh_rows: list[dict] = []
    stale = 0
    try:
        now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
        cut = _day(cutoff)
        rows = list(rows)
        if cut is None:
            problems.append("the carry-window cutoff is unknown")
        by_symbol: dict[str, list[dict]] = {}
        for r in rows:
            rid = r.get("id")
            tag = f"signal #{rid}"
            sd = _day(r.get("session_date"))
            if cut is not None and sd is not None and sd < cut:
                stale += 1                                     # outside the carry window: reported, never validated
                continue
            tag += f" {r.get('symbol')}"
            fresh = {"id": rid, "symbol": r.get("symbol"), "session": str(sd) if sd else None, "side": r.get("side")}
            fresh_rows.append(fresh)
            bad: list[str] = []
            if rid is None:
                bad.append("no id")
            if r.get("is_open") not in (True, 1):
                bad.append("is_open is not true")
            sym = r.get("symbol")
            if not isinstance(sym, str) or not _SYMBOL.match(sym) or instrument_kind(sym) != "equity":
                bad.append(f"symbol {sym!r} is not a US stock / ETF the engine can price")
            side, state = r.get("side"), r.get("state")
            if side not in ("BUY", "SELL"):
                bad.append(f"side {side!r} is neither BUY nor SELL")
            elif state not in (_BUY_STATES if side == "BUY" else _SELL_STATES):
                bad.append(f"state {state!r} does not agree with side {side}")
            if sd is None:
                bad.append("session_date is not a date")
            elif sd > now_et.date():
                bad.append(f"session_date {sd} is in the future")
            st, sts = _ts(r.get("signal_ts")), _ts(r.get("status_ts"))
            if st is None:
                bad.append("signal_ts is missing or unreadable")
            else:
                if st > now_et + _FUTURE_SKEW:
                    bad.append("signal_ts is in the future")
                if sd is not None and st.astimezone(ET).date() != sd:
                    bad.append(f"signal_ts falls on {st.astimezone(ET).date()}, not on session {sd}")
            if sts is None:
                bad.append("status_ts is missing or unreadable")
            elif st is not None and sts < st - timedelta(seconds=1):
                bad.append("status_ts is before signal_ts")
            entry, best, tp, sl, atr = (_num(r.get(k)) for k in ("entry", "best_price", "tp", "sl", "atr"))
            if entry is None or entry <= 0:
                bad.append("entry is not a positive number")
            if best is None or best <= 0:
                bad.append("best_price is not a positive number")
            if atr is not None and atr < 0:
                bad.append("atr is negative")
            if (tp is None) != (sl is None):
                bad.append("only one of tp / sl is set")
            elif tp is None:
                if atr:                                            # the engine only leaves levels empty when there is no ATR
                    bad.append("tp / sl are empty although an ATR is recorded")
            elif entry and entry > 0:
                if tp <= 0 or sl <= 0:
                    bad.append("tp / sl are not positive")
                elif side == "BUY" and not (sl < entry < tp):
                    bad.append(f"BUY levels are inconsistent (sl {sl} / entry {entry} / tp {tp})")
                elif side == "SELL" and not (tp < entry < sl):
                    bad.append(f"SELL levels are inconsistent (tp {tp} / entry {entry} / sl {sl})")
            be = r.get("be_moved")
            if be is not None and be not in (True, False, 0, 1):
                bad.append("be_moved is not true/false")
            for b in bad:
                problems.append(f"{tag}: {b}")
            if isinstance(sym, str):
                by_symbol.setdefault(sym, []).append(r)
        if fresh_rows and columns is not None:                  # a table without these columns matters only if something is open
            missing = [c for c in REQUIRED_COLUMNS if c not in set(columns)]
            if missing:
                problems.append("the signal table lacks columns the engine reads: " + ", ".join(missing))
        for sym, group in sorted(by_symbol.items()):
            if len(group) > 1:
                sides = sorted({str(g.get("side")) for g in group})
                kind = "opposite sides" if len(sides) > 1 else "the same side"
                problems.append(f"{sym} has {len(group)} open signals ({kind}): ids " +
                                ", ".join(str(g.get("id")) for g in group))
    except Exception as exc:  # noqa: BLE001 -- anything unexpected is a refusal
        problems.append(f"the open signals could not be validated ({type(exc).__name__})")
    return {"ok": not problems, "fresh": len(fresh_rows), "stale": stale, "problems": problems, "fresh_rows": fresh_rows}


def cutoff_iso(now: Optional[datetime] = None, trading_days: int = 1, is_trading_day=None) -> str:
    """The ISO date before which an open signal is stale: ``trading_days`` trading days back from today (Eastern).
    Weekends are skipped; ``is_trading_day`` (optional) adds exchange holidays."""
    d = (now or datetime.now(timezone.utc)).astimezone(ET).date()
    n = 0
    while n < trading_days:
        d -= timedelta(days=1)
        if d.weekday() < 5 and (is_trading_day is None or is_trading_day(d)):
            n += 1
    return d.isoformat()


def assess(engine=None, *, now: Optional[datetime] = None) -> dict:
    """Read the open signals from the database (read-only) and validate them. Fails closed on any read problem."""
    out = {"ok": False, "table": False, "fresh": 0, "stale": 0, "problems": [], "fresh_rows": []}
    try:
        from sqlalchemy import inspect, text
        import entry_lock
        eng = entry_lock._engine(engine)
        insp = inspect(eng)
        if "intraday_sig_v2" not in insp.get_table_names():
            return {**out, "ok": True, "problems": []}                        # no table = nothing open
        out["table"] = True
        cols = [c["name"] for c in insp.get_columns("intraday_sig_v2")]
        missing = [c for c in ("is_open", "session_date") if c not in cols]
        if missing:
            return {**out, "problems": ["the signal table lacks " + ", ".join(missing) + ": cannot tell what is open"]}
        cut = entry_lock._cutoff(now)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
            pick = [c for c in REQUIRED_COLUMNS if c in cols]
            rows = conn.execute(text("SELECT " + ", ".join(pick) + " FROM intraday_sig_v2 WHERE is_open = :t ORDER BY id"),
                                {"t": True}).mappings().all()
        res = validate([dict(r) for r in rows], columns=cols, now=now, cutoff=cut)
        return {**res, "table": True}
    except Exception as exc:  # noqa: BLE001
        return {**out, "problems": [f"could not read the open signals ({type(exc).__name__}): refusing to proceed blind"]}
