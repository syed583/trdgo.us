"""Demo (paper) trade tracker for the intraday model signals -- PURE INTRADAY.

One shared blotter across the four intraday models (Grok, Meta, Kimi, Gemini). When a model's board shows a BUY (long)
or SELL (short), this opens ONE paper trade per (strategy, symbol, trading day) and tracks it live.

The rules (tab_settings.PAPER_EXECUTION):
  * entries only 10:00-15:00 ET on a regular trading day (never later than close - 60 min on an early-close day); never
    pre-market, never after hours, never on a weekend / holiday (market_calendar);
  * an entry is priced at a FRESH live quote (the provider's own quote time at most ``quote_max_age_s`` old) -- a missing
    or old quote, or a stale board input (the freshness gate), means no entry;
  * size = ``notional_usd`` / entry price, floored to whole shares (no fixed 100 shares);
  * stop / target levels as the board computed them (0.5 / 1.0 ATR), re-based on the real entry price;
  * breakeven trail: once profit reaches ``breakeven_at`` of the target distance the stop moves to the entry;
  * exits: STOP, TARGET, SCORE_EXIT (score falls 15 points against the entry), and EOD_CLOSE: EVERY open position is
    closed ``eod_minutes_before_close`` before the official close (15:50 ET; 12:50 on an early-close day);
  * fills are the observed crossing price (``paper_fills`` ``observed``), never the level itself;
  * a position still open from an earlier day (left by an older build) is closed at the current price as CLEANUP;
  * every entry stores its evidence: all parameter readings, coverage %, score, quote age, bid, ask, spread, sizing.

Paper only -- no real orders; never touches the Earnings AI data (its own additive table). Everything the tracker does is
also written to logs/tracker.log (rotating), not only to stdout.
"""
from __future__ import annotations

import json
import math
import threading
import time as _time
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SOURCE = "Strategy Trades — $10,000-notional paper trades from the intraday signals (pure intraday)"
SCORE_DROP_EXIT = 15.0          # exit when score falls 15 pts from entry
_MARKET_OPEN, _MARKET_CLOSE = dtime(9, 30), dtime(16, 0)

# strategy key -> (label, "module:function")  -- the strategies that may OPEN a paper trade
MODELS: dict[str, tuple[str, str]] = {
    "grok": ("Grok Intraday", "intraday_grok_service:board"),
    "meta": ("Meta Intraday", "meta_intraday_service:board"),
    "kimi": ("Kimi Intraday", "kimi_intraday_service:board"),
    "gemini": ("Gemini Intraday", "gemini_intraday_service:board"),
}
# Strategies that no longer trade (their boards were removed) but whose past trades stay readable in the blotter.
RETIRED: dict[str, str] = {
    "chatgpt": "ChatGPT Intraday (retired)",
    "claude_pro": "Claude Pro Intraday (retired)",
}

EXIT_LABELS = {
    "OPEN": "Open", "STOP": "Stopped", "TARGET": "Target", "SCORE_EXIT": "Score exit",
    "EOD_CLOSE": "Closed before the bell", "CLEANUP": "Cleanup (prior-day leftover)", "TIMED_EXIT": "Timed exit",
    "MARKET_CLOSE": "Closed at the bell", "NO_PRICE": "No price (closed unpriced)", "PENDING_RECON": "Pending reconciliation",
}

_schema_ready = False
_last_tick = 0.0
_tick_lock = threading.Lock()

try:
    import app_logging as _al
    _log = _al.file_logger("tracker")
except Exception:  # noqa: BLE001
    import logging as _logging
    _log = _logging.getLogger("tracker")


def strategy_label(key: str) -> str:
    return MODELS.get(key, (RETIRED.get(key, key),))[0]


def _cfg() -> dict:
    import tab_settings
    return tab_settings.PAPER_EXECUTION


def _hhmm(s: str) -> dtime:
    h, m = str(s).split(":")
    return dtime(int(h), int(m))


def _et_now() -> datetime:
    return datetime.now(ET)


def _is_rth(now: Optional[datetime] = None) -> bool:
    n = (now or _et_now())
    return n.weekday() < 5 and _MARKET_OPEN <= n.time() < _MARKET_CLOSE


def _after_close(now: Optional[datetime] = None) -> bool:
    n = (now or _et_now())
    return n.weekday() < 5 and n.time() >= _MARKET_CLOSE


# ------------------------------------------------------------------------------------------------- the clock rules ---
def entry_window(d) -> Optional[tuple[datetime, datetime]]:
    """[start, end) in which a NEW entry may open on trading day ``d``: 10:00 ET to 15:00 ET, and never later than
    60 minutes before the official close (an early-close day ends entries at 12:00). None on a non-trading day."""
    import market_calendar as mc
    if not mc.is_trading_day(d):
        return None
    c = _cfg()
    start = datetime.combine(d, _hhmm(c.get("entry_start", "10:00")), tzinfo=ET)
    end = datetime.combine(d, _hhmm(c.get("entry_end", "15:00")), tzinfo=ET)
    end = min(end, mc.close_datetime(d) - timedelta(minutes=60))
    return start, end


def _can_open(now: Optional[datetime] = None) -> bool:
    """May a NEW entry open right now? Regular trading day, inside the entry window only."""
    n = (now or _et_now()).astimezone(ET)
    w = entry_window(n.date())
    return bool(w) and w[0] <= n < w[1]


def eod_cutoff(d) -> Optional[datetime]:
    """When every open position of trading day ``d`` is closed: ``eod_minutes_before_close`` before the official close."""
    import market_calendar as mc
    if not mc.is_trading_day(d):
        return None
    return mc.close_datetime(d) - timedelta(minutes=int(_cfg().get("eod_minutes_before_close", 10)))


def _eod_due(t, now: Optional[datetime] = None) -> bool:
    """Is it time to close ``t`` for the day? True from the EOD cutoff of its own session date; a position of an EARLIER
    date is always due (it is cleaned up as CLEANUP, see tick)."""
    n = (now or _et_now()).astimezone(ET)
    if t.session_date is None:
        return False
    if t.session_date < n.date():
        return True
    if t.session_date > n.date():
        return False
    cut = eod_cutoff(t.session_date)
    return cut is not None and n >= cut


def has_open_trades() -> bool:
    """True if any demo trade is still OPEN (or the database cannot be asked). A tick with nothing open and no entries
    allowed would rebuild the intraday boards for nothing."""
    try:
        ensure_schema()
        import database
        from models_demo_trades import DemoTrade
        s = database.SessionLocal()
        try:
            if s.query(DemoTrade.id).filter_by(status="OPEN").first() is not None:
                return True
            import intraday_workflow as wf
            if wf.shadow():                                   # the virtual book must also be closed / reconciled at the bell
                from models_demo_trades import ShadowTrade
                if s.query(ShadowTrade.id).filter(ShadowTrade.status.in_(("OPEN", "PENDING_RECON"))).first() is not None:
                    return True
            return wf.active() and s.query(DemoTrade.id).filter_by(status="PENDING_RECON").first() is not None
        finally:
            s.close()
    except Exception:  # noqa: BLE001 - fail safe: keep ticking
        return True


def should_tick(now: Optional[datetime] = None) -> bool:
    """Tick through the entry window and until the EOD close of a trading day; outside that only while something is
    still open (a leftover to clean up, or a position the EOD cutoff must still close)."""
    n = (now or _et_now()).astimezone(ET)
    import intraday_workflow as wf
    if wf.active():
        return wf.should_tick(n, has_open_trades)
    w = entry_window(n.date())
    if w and (w[0] - timedelta(minutes=5)) <= n < (eod_cutoff(n.date()) + timedelta(minutes=5)):
        return True
    return has_open_trades()


def sleep_seconds(default: float = 60.0) -> float:
    """How long the tracker loop sleeps. 60 s, except that it wakes at the official close when that is sooner (workflow v2)."""
    try:
        import intraday_workflow as wf
        return wf.sleep_seconds(_et_now(), default) if wf.active() else default
    except Exception:  # noqa: BLE001
        return default


def _exit_timed(t, now: Optional[datetime] = None) -> bool:
    """Kept for the session workflow and older callers: the pure-intraday rule is the EOD cutoff (``_eod_due``)."""
    return _eod_due(t, now)


def _unpriced_expired(t, now: datetime) -> bool:
    """A close that has been DUE for ``max_unpriced_trading_days`` trading days without ever finding a reliable price."""
    import market_calendar
    import tab_settings
    if not t.session_date:
        return False
    d, n = t.session_date, 0
    while d < now.date():
        d = market_calendar.next_trading_day(d)
        n += 1
    return n >= int(tab_settings.PAPER_EXECUTION["max_unpriced_trading_days"])


def _priced(t, info: dict, boards: dict):
    """The best price for ``t`` and whether it is an executable (reliable) one. Never invents a price."""
    import paper_fills as pf
    import tab_settings
    cands = []
    for rows in ([boards.get(t.strategy, {})] + [r for k, r in boards.items() if k != t.strategy]):
        r = rows.get(t.symbol)
        if r and r.get("now") is not None:
            cands.append(r)
    # the freshest non-WAIT board price; a WAIT row's price is kept only as a fallback description
    cands.sort(key=lambda r: (bool(r.get("wait")), r.get("price_age_s") if r.get("price_age_s") is not None else 1e9))
    best = cands[0] if cands else {}
    lim = float(tab_settings.PAPER_EXECUTION["max_price_age_s"])
    board_ok = best.get("now") is not None and not best.get("wait") and best.get("price_age_s") is not None \
        and best["price_age_s"] <= lim
    quote = None if board_ok else _quote_price(t.symbol)
    return pf.classify_price(best.get("now"), best.get("price_age_s"), bool(best.get("wait")), quote,
                             t.last_price, t.entry_price)


def _aware(dt):
    from datetime import timezone as _tz
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=_tz.utc))


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        import models_demo_trades  # noqa: F401 - registers the table
        from database import Base, engine
        from models_demo_trades import DemoTrade, ensure_tracker_columns
        DemoTrade.__table__.create(bind=engine, checkfirst=True)
        try:
            added = ensure_tracker_columns(engine)        # nullable evidence columns on an existing table
            if added:
                _log.info("demo_trades: added columns %s", ", ".join(added))
        except Exception:  # noqa: BLE001
            _log.exception("demo_trades: the tracker's evidence columns could not be added")
        import intraday_workflow as wf
        if wf.active():
            wf.migrate(engine)                # V2 activation only: evidence columns + unique indexes (separate approval)
        _schema_ready = True
    except Exception:  # noqa: BLE001 - never hard-fail a page on a DB blip
        return
    _ensure_extra_tables()


_extra_tables_done = False


def _ensure_extra_tables() -> None:
    """Create ONLY the new tables the enabled optional features need (audit and/or shadow). Separate from the Legacy schema step
    above: a failure here (e.g. no DDL right) is logged once and affects nothing else; Legacy never depends on these tables."""
    global _extra_tables_done
    if _extra_tables_done:
        return
    try:
        import signal_verification as sv
        import intraday_workflow as wf
        need = []
        if sv.enabled():
            need.append("intraday_signal_audit")
        if wf.shadow():
            need += ["intraday_shadow_trades", "intraday_shadow_log", "intraday_signal_events", "intraday_snapshots",
                     "intraday_data_exceptions"]
        if need:
            from database import engine
            wf.ensure_tables(engine, need)
            if "intraday_signal_audit" in need:
                sv.secure_audit_table(engine)          # keep the audit data out of the Data API
        _extra_tables_done = True
    except Exception:  # noqa: BLE001
        _log.exception("optional intraday tables could not be created; Legacy trading is unaffected")
        _extra_tables_done = True               # do not retry on every tick


def _board(spec: str) -> Optional[dict]:
    mod, fn = spec.split(":")
    try:
        m = __import__(mod)
        return getattr(m, fn)()
    except Exception:  # noqa: BLE001
        _log.exception("board %s failed", spec)
        return None


def _row_map(board: dict) -> dict[str, dict]:
    """symbol -> {signal, score, entry, now, sl, tp, ...} across buyers/sellers/neutral/wait."""
    import intraday_safety
    out: dict[str, dict] = {}
    # "wait" rows (stale / incomplete data) stay in the map so OPEN trades keep being managed
    # exactly as before (price, stop, target, score exit from the raw score); they just can
    # never open a NEW trade (see `tradable`).
    for bucket in ("buyers", "sellers", "neutral", "wait"):
        for r in (board.get(bucket) or []):
            s = (r.get("symbol") or "").upper()
            if s:
                score = r.get("score") if r.get("score") is not None else r.get("score_raw")
                out[s] = {"signal": r.get("signal"), "score": score,
                          "entry": r.get("entry"), "now": r.get("now"),
                          "price_age_s": (r.get("data") or {}).get("price_age_s"), "wait": bool(r.get("wait")),
                          "sl": r.get("sl"), "tp": r.get("tp"),
                          "tradable": intraday_safety.tradable(r), "data": r.get("data") or {}, "signal_raw": r.get("signal_raw"),
                          "score_raw": r.get("score_raw"), "signal_meta": r.get("signal_meta"),
                          "params": r.get("params"), "raw_levels": r.get("raw_levels"), "gate_ts": r.get("gate_ts"),
                          "model_status": r.get("model_status"), "coverage": r.get("coverage"),
                          "agreement": r.get("agreement"), "health": r.get("health")}
    return out


def _pnl(side: str, entry: float, last: float, shares: int):
    diff = (last - entry) if side == "LONG" else (entry - last)
    profit = round(diff * shares, 2)
    pct = round((diff / entry) * 100, 2) if entry else 0.0
    return profit, pct


class _Level:
    """The trade as the exit rules see it, with the stop / target actually in force (backfilled when missing)."""

    def __init__(self, t, stop, target):
        self.side, self.score_at_entry, self.stop, self.target = t.side, t.score_at_entry, stop, target


def _exit_reason(t, last: float, score: Optional[float], after_close: bool) -> Optional[str]:
    long = t.side == "LONG"
    if t.stop is not None and ((long and last <= t.stop) or (not long and last >= t.stop)):
        return "STOP"
    if t.target is not None and ((long and last >= t.target) or (not long and last <= t.target)):
        return "TARGET"
    if score is not None and t.score_at_entry is not None:
        drop = (t.score_at_entry - score) if long else (score - t.score_at_entry)
        if drop >= SCORE_DROP_EXIT:
            return "SCORE_EXIT"
    if after_close:
        return "EOD_CLOSE"
    return None


def breakeven_stop(side: str, entry: float, stop: Optional[float], target: Optional[float], last: float,
                   trigger: Optional[float] = None) -> Optional[float]:
    """The new stop when the breakeven trail fires, else None. Pure: fires once profit has covered ``trigger`` (default
    PAPER_EXECUTION breakeven_at = 50 %) of the distance from the entry to the target, and only moves the stop in the
    trade's favour (a stop already at or beyond the entry is left alone)."""
    if target is None or entry is None or last is None:
        return None
    trig = float(_cfg().get("breakeven_at", 0.5)) if trigger is None else float(trigger)
    dist = abs(target - entry)
    if dist <= 0:
        return None
    gain = (last - entry) if side == "LONG" else (entry - last)
    if gain < trig * dist:
        return None
    if stop is not None and ((side == "LONG" and stop >= entry) or (side == "SHORT" and stop <= entry)):
        return None
    return round(float(entry), 4)


def _quote(symbol: str) -> Optional[dict]:
    """The live quote the tracker prices with (price, bid, ask, spread, the provider's quote age), cached 15 s."""
    import swr
    import unusualwhales_service as uw

    def _q():
        q = uw.get_quote(symbol) or {}
        v = q.get("price") or q.get("close") or q.get("last")
        try:
            price = float(v)
        except (TypeError, ValueError):
            return {}
        return {"price": price, "bid": q.get("bid"), "ask": q.get("ask"), "spread": q.get("spread"),
                "spread_pct": q.get("spread_pct"), "quote_epoch": q.get("quote_epoch"),
                "quote_age_s": q.get("quote_age_s"), "fetched_at": _time.time()}

    v = swr.serve(f"demo:q:{symbol}", _q, 15.0)
    return v if isinstance(v, dict) and v.get("price") else None


def _quote_price(symbol: str) -> Optional[float]:
    """Live last price for a symbol (exits / cleanup), from the same short cache as ``_quote``."""
    q = _quote(symbol)
    return q.get("price") if q else None


def _fresh_quote(symbol: str, now: Optional[datetime] = None) -> tuple[Optional[dict], str]:
    """A quote young enough to price an ENTRY at, else (None, why). The age is the provider's own quote time PLUS the time
    the cached copy has sat here; a quote without a provider time cannot be verified and is refused."""
    q = _quote(symbol)
    if not q:
        return None, "no live quote"
    lim = float(_cfg().get("quote_max_age_s", 60.0))
    age = q.get("quote_age_s")
    if age is None:
        return None, "quote carries no timestamp (age unverifiable)"
    held = max(0.0, _time.time() - float(q.get("fetched_at") or _time.time()))
    total = float(age) + held
    if total > lim:
        return None, f"quote is {total:.0f}s old (limit {lim:.0f}s)"
    return dict(q, quote_age_s=round(total, 1)), ""


def _live_price(symbol: str, boards: dict, fallback: Optional[float] = None) -> Optional[float]:
    """Best current price: any board's `now`, else a live quote, else fallback."""
    for rows in boards.values():
        r = rows.get(symbol)
        if r and r.get("now") is not None:
            return r["now"]
    return _quote_price(symbol) or fallback


def _with_levels(side: str, entry: float, sl, tp):
    """Fill a default 1%/2% (2:1) stop and target when the board gives none, so
    every trade can still exit on stop/target."""
    if sl is None:
        sl = round(entry * (0.99 if side == "LONG" else 1.01), 2)
    if tp is None:
        tp = round(entry * (1.02 if side == "LONG" else 0.98), 2)
    return sl, tp


def rebase_levels(side: str, board_entry, sl, tp, entry: float):
    """The board's stop / target distances (computed from ITS price) applied to the real entry price."""
    if board_entry and sl is not None and tp is not None:
        sgn = 1 if side == "LONG" else -1
        sl_d, tp_d = abs(float(board_entry) - float(sl)), abs(float(tp) - float(board_entry))
        return round(entry - sgn * sl_d, 2), round(entry + sgn * tp_d, 2)
    return _with_levels(side, entry, None, None)


def shares_for(entry: float, notional: Optional[float] = None) -> int:
    """Whole shares for ``notional`` dollars at ``entry`` (0 when the price is above the notional)."""
    n = float(_cfg().get("notional_usd", 10_000.0)) if notional is None else float(notional)
    if not entry or entry <= 0:
        return 0
    return int(math.floor(n / float(entry)))


def _is_equity(sym: str) -> bool:
    """Only US stocks / ETFs: their session is the one modelled. Indexes cannot be traded; futures follow a different
    (Globex) calendar that is not modelled -- they never open here."""
    try:
        import market_calendar
        return market_calendar.instrument_kind(sym) == "equity"
    except Exception:  # noqa: BLE001
        return False


def _entry_allowed(sym: str, sig: str, et) -> bool:
    """The last gate, at the moment of opening: a CONFIRMED 15-minute reading and an open entry lock, read fresh.
    ANY failure (even a missing module) means no entry -- and must not raise, because the tick that opens trades
    also commits the exits of the trades already open."""
    try:
        import intraday_15m
        import intraday_indicators as _ind
        return bool(intraday_15m.entry_check(sym, sig, _ind.market_phase(et), execution=True)["allowed"])
    except Exception:  # noqa: BLE001
        return False


def _evidence(info: dict, q: dict, shares: int, entry: float, reason_ok: str = "") -> str:
    """What the strategy saw at the moment of entry, as JSON: every parameter reading, coverage, score, quote, sizing."""
    d = info.get("data") or {}
    ev = {"params": info.get("params"), "coverage_pct": info.get("coverage"), "agreement_pct": info.get("agreement"),
          "score": info.get("score"), "signal": info.get("signal"), "board_entry": info.get("entry"),
          "board_levels": {"sl": info.get("sl"), "tp": info.get("tp")},
          "inputs": {"signals_age_s": d.get("signals_age_s"), "price_age_s": d.get("price_age_s"), "inputs": d.get("inputs"),
                     "phase": d.get("phase"), "dropped_stale": d.get("dropped_stale")},
          "health": info.get("health"), "quote": {k: q.get(k) for k in ("price", "bid", "ask", "spread", "spread_pct",
                                                                           "quote_age_s", "quote_epoch")},
          "sizing": {"notional_usd": _cfg().get("notional_usd"), "shares": shares, "entry_price": entry}}
    try:
        return json.dumps(ev, default=str)
    except Exception:  # noqa: BLE001
        return json.dumps({"error": "evidence not serialisable"})


def tick(force: bool = False) -> None:
    """Refresh open trades from the live boards and open new ones. Throttled."""
    global _last_tick
    now = _time.time()
    if not force and (now - _last_tick) < 15:
        return
    if not _tick_lock.acquire(blocking=False):
        return
    try:
        _last_tick = now
        ensure_schema()
        import intraday_workflow as wf
        if wf.active():                       # the session workflow (see intraday_workflow.py); legacy below is unchanged
            wf.run(_et_now(), MODELS)
            return
        import database
        from models_demo_trades import DemoTrade
        et = _et_now()
        today = et.date()

        boards = {k: _row_map(b) for k, (lbl, spec) in MODELS.items()
                  if (b := _board(spec))}

        s = database.SessionLocal()
        try:
            from sqlalchemy import update
            # Every OPEN position: today's are managed (trail, stop, target, score exit, EOD close); one left over from an
            # earlier date -- an older build carried positions -- is closed at the current price as CLEANUP.
            open_trades = s.query(DemoTrade).filter_by(status="OPEN").order_by(DemoTrade.id.asc()).all()
            import paper_fills as pf
            for t in open_trades:
                info = boards.get(t.strategy, {}).get(t.symbol) or {}
                stop, target = t.stop, t.target
                if stop is None or target is None:       # backfill levels on older rows
                    stop, target = _with_levels(t.side, t.entry_price, stop, target)
                price = _priced(t, info, boards)
                leftover = bool(t.session_date and t.session_date < today)
                due = _eod_due(t, et)
                if not price.reliable:
                    # No executable price: nothing is closed, nothing is refreshed, no fill is invented. A close that
                    # stays due and unpriced for days is closed as NO_PRICE (no exit price, no P&L).
                    if due and _unpriced_expired(t, et):
                        s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == "OPEN").values(
                            status="NO_PRICE", exit_time=et.astimezone(timezone.utc), exit_price=None, profit=None, profit_pct=None))
                        _log.warning("NO_PRICE %s %s #%s: due since %s, no reliable price", t.strategy, t.symbol, t.id, t.session_date)
                    continue
                last = price.value
                be_moved = bool(t.be_moved)
                if not leftover:
                    nb = breakeven_stop(t.side, t.entry_price, stop, target, last)
                    if nb is not None:
                        stop, be_moved = nb, True
                        _log.info("BREAKEVEN %s %s #%s: stop -> %.4f at %.4f", t.strategy, t.symbol, t.id, stop, last)
                profit, pct = _pnl(t.side, t.entry_price, last, t.shares)
                if leftover:
                    reason = "CLEANUP"
                else:
                    reason = _exit_reason(_Level(t, stop, target), last, info.get("score"), due)
                values = {"stop": stop, "target": target, "last_price": last, "profit": profit, "profit_pct": pct,
                          "be_moved": be_moved}
                if reason:
                    gap = pf.observation_gap(_aware(t.updated_at) or _aware(t.entry_time), et) \
                        if reason in ("STOP", "TARGET") else False
                    exit_px = pf.fill_price(t.side, reason, stop, target, last, gap)
                    profit, pct = _pnl(t.side, t.entry_price, exit_px, t.shares)
                    values.update(exit_price=exit_px, last_price=exit_px, profit=profit, profit_pct=pct,
                                  status=reason, exit_time=et.astimezone(timezone.utc))
                    _log.info("EXIT %s %s %s #%s: %s at %.4f (level stop %.4f target %.4f, source %s) P&L %.2f",
                              reason, t.strategy, t.symbol, t.id, t.side, exit_px, stop or 0.0, target or 0.0, price.source, profit)
                # Guarded by "still OPEN": if another process / tick already closed this trade, nothing is written
                # (no second close, no overwritten P&L, no refresh of a closed row).
                s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == "OPEN").values(**values))

            have, have0 = set(), set()
            # open new trades for fresh BUY/SELL (one per strategy+symbol+day), inside the entry window only
            if _can_open(et):
                have = {(x.strategy, x.symbol) for x in
                        s.query(DemoTrade.strategy, DemoTrade.symbol).filter_by(session_date=today).all()}
                have |= {(x.strategy, x.symbol) for x in
                         s.query(DemoTrade.strategy, DemoTrade.symbol).filter_by(status="OPEN").all()}
                have0 = set(have)                  # (read only; used by the shadow-mode decision log)
                for strat, rows in boards.items():
                    for sym, info in rows.items():
                        sig = info.get("signal")
                        # New entries only from rows whose data passed the freshness gate.
                        if not (sig in ("BUY", "SELL") and info.get("entry") and info.get("tradable", False)
                                and (strat, sym) not in have and _is_equity(sym)):
                            continue
                        # The last gate, at the moment of opening: a CONFIRMED 15-minute reading and an open
                        # entry lock, read fresh. Exits above never depend on this.
                        if not _entry_allowed(sym, sig, et):
                            continue
                        q, why = _fresh_quote(sym, et)
                        if q is None:
                            _log.info("NO ENTRY %s %s: %s", strat, sym, why)
                            continue
                        entry = round(float(q["price"]), 4)
                        shares = shares_for(entry)
                        if shares < 1:
                            _log.info("NO ENTRY %s %s: price %.2f above the notional", strat, sym, entry)
                            continue
                        side = "LONG" if sig == "BUY" else "SHORT"
                        sl, tp = rebase_levels(side, info.get("entry"), info.get("sl"), info.get("tp"), entry)
                        profit0, pct0 = _pnl(side, entry, entry, shares)
                        s.add(DemoTrade(strategy=strat, symbol=sym, side=side, shares=shares,
                                        session_date=today, entry_time=et.astimezone(timezone.utc), entry_price=entry,
                                        stop=sl, target=tp,
                                        score_at_entry=info.get("score"), last_price=entry,
                                        profit=profit0, profit_pct=pct0, status="OPEN",
                                        entry_bid=q.get("bid"), entry_ask=q.get("ask"), entry_spread_pct=q.get("spread_pct"),
                                        entry_quote_age_s=q.get("quote_age_s"), be_moved=False,
                                        entry_evidence=_evidence(info, q, shares, entry)))
                        have.add((strat, sym))
                        _log.info("ENTRY %s %s %s: %d sh at %.4f (quote age %.1fs, spread %s%%) stop %.4f target %.4f score %s coverage %s",
                                  strat, sym, side, shares, entry, q.get("quote_age_s") or -1, q.get("spread_pct"), sl, tp,
                                  info.get("score"), info.get("coverage"))
            s.commit()
        finally:
            s.close()
        try:
            import signal_verification as sv
            if sv.enabled():                  # audit only, AFTER Legacy has committed: records, never blocks or changes a trade
                _audit_signals(et, boards, have0, have)
        except Exception:  # noqa: BLE001
            _log.exception("signal verification failed; trading is unaffected")
        if wf.shadow():                       # AFTER Legacy has committed, and unable to affect it: log Legacy, then V2 judges the same boards
            _shadow_log_legacy(et, boards, have0, have)
            try:
                wf.run(et, MODELS, shadow=True)
            except Exception:  # noqa: BLE001
                _log.exception("shadow V2 tick failed; Legacy is unaffected")
    except Exception:  # noqa: BLE001 - a failed tick is retried by the next one (nothing is half-written)
        _log.exception("paper-trade tick failed; the next tick retries")
    finally:
        _tick_lock.release()


def _shadow_log_legacy(et, boards: dict, have0: set, have: set) -> None:
    """Shadow mode only. Runs AFTER Legacy's transaction is committed and closed, in its own session: it records what Legacy did (or
    why it did not) with every BUY / SELL board row, for the comparison. It cannot touch a Legacy trade, and any failure here is
    swallowed."""
    import database
    s = database.SessionLocal()
    try:
        import intraday_shadow as sh
        from models_demo_trades import DemoTrade
        today = et.date()
        decisions = []                     # phase 1: decide (may read the shared caches); phase 2: write -- nothing pending while reading
        for strat, rows in boards.items():
            for sym, info in rows.items():
                raw = info.get("signal_raw") or info.get("signal")
                if raw not in ("BUY", "SELL"):
                    continue
                opened_id, held, why = None, False, ""
                if (strat, sym) in have and (strat, sym) not in have0:
                    t = s.query(DemoTrade.id).filter_by(strategy=strat, symbol=sym, session_date=today).order_by(DemoTrade.id.desc()).first()
                    opened_id = t[0] if t else None
                elif (strat, sym) in have0:
                    held, why = True, "already held / traded today"
                elif not _can_open(et):
                    why = "entries closed (outside the 10:00-15:00 ET entry window)"
                elif not _is_equity(sym):
                    why = "unsupported instrument (index / future)"
                elif info.get("wait") or info.get("signal") not in ("BUY", "SELL"):
                    why = "freshness / coverage / 15-minute gate (WAIT row, no entry levels)"
                elif not info.get("entry"):
                    why = "no entry price"
                elif not info.get("tradable", False):
                    try:
                        import intraday_15m
                        why = "15-minute entry gate: " + str(intraday_15m.entry_check(sym, raw, (info.get("data") or {}).get("phase"))["reason"])
                    except Exception:  # noqa: BLE001
                        why = "entry gate refused"
                else:
                    why = "entry gate refused at the moment of opening (15-minute confirmation / lock / stale quote)"
                decisions.append((strat, sym, info, opened_id, held, why))
        for strat, sym, info, opened_id, held, why in decisions:
            sh.legacy_decision(s, et, strat, sym, info, opened_id=opened_id, held=held, reason=why)
        s.commit()
    except Exception:  # noqa: BLE001
        s.rollback()
        _log.exception("shadow decision log failed; Legacy is unaffected")
    finally:
        s.close()


def _audit_signals(et, boards: dict, have0: set, have: set) -> None:
    """Signal-verification audit of every BUY / SELL board row, in its own session after Legacy's commit. Never raises. A fault in
    one signal's verification is counted and skipped; a database fault is counted and the whole audit write is dropped; a hard time
    budget stops the pass. Nothing here can touch a paper trade."""
    import time as _t
    import database
    import signal_verification as sv
    from models_demo_trades import DemoTrade
    t0 = _t.monotonic()
    budget = float((getattr(__import__("tab_settings"), "INTRADAY_VERIFICATION", None) or {}).get("max_seconds", 5.0))
    sv.STATS["cycles"] += 1
    s = (sv.audit_sessionmaker() or database.SessionLocal)()      # its own least-privileged role when AUDIT_DATABASE_URL is set
    try:
        if s.get_bind().dialect.name == "postgresql":      # the audit can never hold a lock or a statement open against live trading
            from sqlalchemy import text as _text
            s.execute(_text("SET LOCAL lock_timeout = '2s'"))
            s.execute(_text("SET LOCAL statement_timeout = '5s'"))
        today = et.date()
        results = []
        for strat, rows in boards.items():
            for sym, info in rows.items():
                if _t.monotonic() - t0 > budget:             # a hard time budget: whatever is left is simply not audited this tick
                    sv.STATS["budget_skips"] += 1
                    continue
                raw = info.get("signal_raw") or info.get("signal")
                if raw not in ("BUY", "SELL"):
                    continue
                sv.STATS["signals_seen"] += 1
                try:
                    v = sv.verify(strat, dict(info, symbol=sym), now=et)
                    sv.STATS["verified"] += 1
                except Exception:  # noqa: BLE001 - one signal's audit fault is counted and skipped
                    sv.STATS["verify_errors"] += 1
                    continue
                opened_id, held, attr = None, (strat, sym) in have0, None
                if (strat, sym) in have and not held:
                    t = s.query(DemoTrade).filter_by(strategy=strat, symbol=sym, session_date=today).order_by(DemoTrade.id.desc()).first()
                    opened_id = t.id if t else None
                    attr = sv.attribution(strat, sym, raw, info.get("entry"), v["score"], t)
                    n_today = s.query(DemoTrade.id).filter_by(strategy=strat, symbol=sym, session_date=today).count()
                    if n_today > 1:                       # the schema forbids it; if it ever happened it must be visible
                        attr = {"ok": False, "detail": f"{n_today} trades for this strategy/symbol/day"}
                results.append((v, sym, opened_id, held, attr))
        for v, sym, opened_id, held, attr in results:
            if sv.record(s, v, now=et, symbol=sym, opened_id=opened_id, held=held, attr=attr):
                sv.STATS["recorded"] += 1
        s.commit()
    except Exception:  # noqa: BLE001
        s.rollback()
        sv._last.clear()                                     # nothing was stored: let the next pass record again
        sv.STATS["write_errors"] += 1
        _log.exception("signal verification audit failed; trading is unaffected")
    finally:
        s.close()
        sv.log_stats()


def _quote_fields(r, live: bool) -> dict:
    """Bid / ask / spread for a blotter row: the live quote (from the tracker's short cache, no new request) for an OPEN
    position, the entry quote for a closed one. Missing values stay None (the page shows a dash)."""
    if live:
        import swr
        q = swr.peek(f"demo:q:{r.symbol}") or {}
        if q.get("price"):
            return {"bid": q.get("bid"), "ask": q.get("ask"), "spread": q.get("spread"), "spread_pct": q.get("spread_pct"),
                    "quote_age_s": q.get("quote_age_s")}
    bid, ask = r.entry_bid, r.entry_ask
    spread = round(ask - bid, 4) if (bid is not None and ask is not None) else None
    return {"bid": bid, "ask": ask, "spread": spread, "spread_pct": r.entry_spread_pct, "quote_age_s": r.entry_quote_age_s}


def blotter(strategy: str = "", date: str = "") -> dict:
    ensure_schema()
    # Refresh in the background so the page loads instantly from the DB rather
    # than blocking on the board tick (slow on a cold cache). The next refresh
    # picks up the updated P&L. (The background tracker also ticks every 60s.)
    threading.Thread(target=lambda: tick(), daemon=True).start()
    import database
    from models_demo_trades import DemoTrade
    et = _et_now()
    try:
        from datetime import date as _d
        day = _d.fromisoformat(date) if date else et.date()
    except Exception:  # noqa: BLE001
        day = et.date()

    s = database.SessionLocal()
    try:
        q = s.query(DemoTrade).filter(DemoTrade.session_date == day)
        if strategy and strategy != "all":
            q = q.filter(DemoTrade.strategy == strategy)
        rows = q.order_by(DemoTrade.entry_time.asc()).all()
        carried = []
        if day == et.date():                              # positions still open from EARLIER dates (to be cleaned up)
            cq = s.query(DemoTrade).filter(DemoTrade.status == "OPEN", DemoTrade.session_date < day)
            if strategy and strategy != "all":
                cq = cq.filter(DemoTrade.strategy == strategy)
            carried = [{
                "id": r.id, "strategy": r.strategy, "strategy_label": strategy_label(r.strategy),
                "symbol": r.symbol, "side": r.side, "shares": r.shares, "opened_on": str(r.session_date),
                "entry_price": r.entry_price, "last_price": r.last_price, "stop": r.stop, "target": r.target,
                "profit": r.profit, "profit_pct": r.profit_pct, "status": r.status,
            } for r in cq.order_by(DemoTrade.session_date.asc(), DemoTrade.id.asc()).all()]
        import paper_fills as pf

        def _basis(r):
            if r.status in ("STOP", "TARGET") and r.exit_price is not None:
                eff = pf.gap_effect(r.side, r.status, r.stop, r.target, r.exit_price, r.shares)
                return ("GAP" if eff is not None else "LEVEL"), eff
            return (None, None)
        out = [{
            "exit_basis": _basis(r)[0], "gap_effect": _basis(r)[1],
            "exit_date": r.exit_time.astimezone(ET).date().isoformat() if r.exit_time else None,
            "id": r.id, "strategy": r.strategy, "strategy_label": strategy_label(r.strategy),
            "symbol": r.symbol, "side": r.side, "shares": r.shares,
            "notional": round(r.entry_price * r.shares, 2) if (r.entry_price and r.shares) else None,
            "entry_time": r.entry_time.astimezone(ET).strftime("%I:%M:%S %p") if r.entry_time else None,
            "entry_price": r.entry_price, "last_price": r.last_price,
            "chg": round((r.last_price - r.entry_price), 2) if (r.last_price and r.entry_price) else None,
            "stop": r.stop, "target": r.target, "be_moved": bool(r.be_moved),
            "profit": r.profit, "profit_pct": r.profit_pct,
            "status": r.status, "exit_reason_label": EXIT_LABELS.get(r.status, r.status), "score_at_entry": r.score_at_entry,
            "exit_price": r.exit_price, **_quote_fields(r, r.status == "OPEN"),
        } for r in rows]
        # Winners at the top, losers at the bottom (open/flat in the middle).
        out.sort(key=lambda x: (x["profit"] if x["profit"] is not None else 0.0), reverse=True)
        total = round(sum((x["profit"] or 0) for x in out), 2)
        notional = round(sum((x["notional"] or 0) for x in out), 2)
        gap_total = round(sum((x["gap_effect"] or 0) for x in out), 2)
        # P&L REALIZED on this date (positions closed today, whatever date they were opened), kept apart from the
        # entry-date attribution above so a cleaned-up leftover never distorts either view
        rq = s.query(DemoTrade).filter(DemoTrade.status != "OPEN", DemoTrade.profit.isnot(None))
        if strategy and strategy != "all":
            rq = rq.filter(DemoTrade.strategy == strategy)
        closed_today = [r for r in rq.all() if r.exit_time and r.exit_time.astimezone(ET).date() == day]
        realized = round(sum(r.profit for r in closed_today), 2)
        wins = sum(1 for x in out if (x["profit"] or 0) > 0)
        closed = sum(1 for x in out if x["status"] != "OPEN")
        w = entry_window(et.date())
        return {"status": "OK", "source": SOURCE, "date": str(day),
                "strategies": [{"key": k, "label": v[0]} for k, v in MODELS.items()] +
                              [{"key": k, "label": v} for k, v in RETIRED.items()],
                "rows": out, "total_profit": total, "total_notional": notional,
                "total_pct": round(total / notional * 100, 2) if notional else None, "count": len(out),
                # still-open positions from earlier dates: listed here, NOT in rows / totals (their P&L stays
                # attributed to the date they were opened); the next tick closes them as CLEANUP
                "carried_open": carried, "carried_open_count": len(carried),
                "gap_effect_total": gap_total, "realized_on_date": realized, "closed_on_date": len(closed_today),
                "realized_note": "realized_on_date = P&L of positions CLOSED on this date; total_profit = trades OPENED on it",
                "open": len(out) - closed, "closed": closed, "wins": wins,
                "rules": {"notional_usd": _cfg().get("notional_usd"), "entry_window_et": [w[0].strftime("%H:%M"), w[1].strftime("%H:%M")] if w else None,
                          "eod_close_et": eod_cutoff(et.date()).strftime("%H:%M") if eod_cutoff(et.date()) else None,
                          "quote_max_age_s": _cfg().get("quote_max_age_s"), "breakeven_at": _cfg().get("breakeven_at"),
                          "fill_model": _cfg().get("fill_model")},
                "rth": _is_rth(et), "after_close": _after_close(et), "entries_open": _can_open(et)}
    finally:
        s.close()
