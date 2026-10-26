"""Demo (paper) trade tracker for the intraday model signals.

One shared blotter across every intraday model. When a model's board shows a
BUY (long) or SELL (short), this opens ONE 100-share demo trade per
(strategy, symbol, trading day) at the model's entry, with its stop/target, and
tracks it live until it hits the stop, the target, a 15-point score drop, or the
market close (timed exit). Paper only -- no real orders; never touches the
Earnings AI data (its own additive table).
"""
from __future__ import annotations

import threading
import time as _time
from datetime import datetime, time as dtime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SOURCE = "Strategy Trades — demo 100-share paper trades from the intraday signals"
SCORE_DROP_EXIT = 15.0          # exit when score falls 15 pts from entry
_MARKET_OPEN, _MARKET_CLOSE = dtime(9, 30), dtime(16, 0)

# strategy key -> (label, "module:function")
MODELS: dict[str, tuple[str, str]] = {
    "grok": ("Grok Intraday", "intraday_grok_service:board"),
    "meta": ("Meta Intraday", "meta_intraday_service:board"),
    "kimi": ("Kimi Intraday", "kimi_intraday_service:board"),
    "chatgpt": ("ChatGPT Intraday", "chatgpt_intraday_service:board"),
    "gemini": ("Gemini Intraday", "gemini_intraday_service:board"),
    "claude_pro": ("Claude Pro Intraday", "claude_pro_intraday_service:board"),
}

_schema_ready = False
_last_tick = 0.0
_tick_lock = threading.Lock()


def _et_now() -> datetime:
    return datetime.now(ET)


def _is_rth(now: Optional[datetime] = None) -> bool:
    n = (now or _et_now())
    return n.weekday() < 5 and _MARKET_OPEN <= n.time() < _MARKET_CLOSE


def _after_close(now: Optional[datetime] = None) -> bool:
    n = (now or _et_now())
    return n.weekday() < 5 and n.time() >= _MARKET_CLOSE


# Trades may OPEN across the extended session (pre-market + RTH + after-hours),
# so the blotter is useful outside 9:30-4:00 too. Entries use the current price.
from datetime import time as _dtime
_EXT_OPEN, _EXT_CLOSE = _dtime(4, 0), _dtime(20, 0)


def _can_open(now: Optional[datetime] = None) -> bool:
    n = (now or _et_now())
    return n.weekday() < 5 and _EXT_OPEN <= n.time() < _EXT_CLOSE


def has_open_trades() -> bool:
    """True if any demo trade is still OPEN (or the database cannot be asked). A tick with
    nothing open and no entries allowed would rebuild all six intraday boards for nothing."""
    try:
        ensure_schema()
        import database
        from models_demo_trades import DemoTrade
        s = database.SessionLocal()
        try:
            return s.query(DemoTrade.id).filter_by(status="OPEN").first() is not None
        finally:
            s.close()
    except Exception:  # noqa: BLE001 - fail safe: keep ticking
        return True


def should_tick(now: Optional[datetime] = None) -> bool:
    """Tick while entries are allowed (04:00-20:00 ET weekdays); after that only to close
    out trades that are still open."""
    n = now or _et_now()
    if _can_open(n):
        return True
    return _after_close(n) and has_open_trades()


def _exit_timed(t, now: Optional[datetime] = None) -> bool:
    """The timed exit. It fires at the first REGULAR-SESSION CLOSE of a trading day that comes after the entry (16:00 ET,
    13:00 ET on an early-close day), and only when the clock is at or after that day's close:

      * a trade opened before the close exits at that day's close (the original rule);
      * a trade opened after the close (after-hours) exits at the NEXT trading day's close;
      * a trade whose close passed while the service was down is NOT closed just because the date changed: it keeps
        being monitored (stop, target, score) and exits at the next close.

    A weekend or an exchange holiday never closes anything (no close happens, and prices do not move)."""
    import market_calendar
    n = (now or _et_now())
    if not market_calendar.is_trading_day(n.date()) or n < market_calendar.close_datetime(n.date()):
        return False
    if not t.entry_time:
        return False
    return n >= market_calendar.first_close_after(t.entry_time)


def _unpriced_expired(t, now: datetime) -> bool:
    """A close that has been DUE for ``max_unpriced_trading_days`` trading days without ever finding a reliable price."""
    import market_calendar
    import tab_settings
    if not t.entry_time:
        return False
    due = market_calendar.first_close_after(t.entry_time).date()
    d, n = due, 0
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
        from models_demo_trades import DemoTrade
        DemoTrade.__table__.create(bind=engine, checkfirst=True)
        _schema_ready = True
    except Exception:  # noqa: BLE001 - never hard-fail a page on a DB blip
        pass


def _board(spec: str) -> Optional[dict]:
    mod, fn = spec.split(":")
    try:
        m = __import__(mod)
        return getattr(m, fn)()
    except Exception:  # noqa: BLE001
        return None


def _row_map(board: dict) -> dict[str, dict]:
    """symbol -> {signal, score, entry, now, sl, tp} across buyers/sellers/neutral."""
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
                          "tradable": intraday_safety.tradable(r)}
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
        return "TIMED_EXIT"
    return None


def _quote_price(symbol: str) -> Optional[float]:
    """Live last price for a symbol, cached ~2 min. Used when a board gives no
    current price (e.g. the pre-market fallback board used by ChatGPT/Claude Pro)."""
    import swr
    import unusualwhales_service as uw

    def _q() -> Optional[float]:
        q = uw.get_quote(symbol) or {}
        v = q.get("price") or q.get("close") or q.get("last")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    v = swr.serve(f"demo:px:{symbol}", _q, 120.0)
    return v if isinstance(v, (int, float)) else None


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
        import database
        from models_demo_trades import DemoTrade
        et = _et_now()
        today = et.date()

        boards = {k: _row_map(b) for k, (lbl, spec) in MODELS.items()
                  if (b := _board(spec))}

        s = database.SessionLocal()
        try:
            # EVERY open position, whatever date it was opened: positions are carried across midnight, weekends
            # and holidays until a rule closes them (stop, target, trailing/score, timed exit). Nothing is closed
            # merely because the date changed.
            from sqlalchemy import update
            from models_demo_trades import _utcnow
            open_trades = s.query(DemoTrade).filter_by(status="OPEN").order_by(DemoTrade.id.asc()).all()
            import paper_fills as pf
            for t in open_trades:
                info = boards.get(t.strategy, {}).get(t.symbol) or {}
                stop, target = t.stop, t.target
                if stop is None or target is None:       # backfill levels on older rows
                    stop, target = _with_levels(t.side, t.entry_price, stop, target)
                price = _priced(t, info, boards)
                timed = _exit_timed(t, et)
                if not price.reliable:
                    # No executable price: nothing is closed, nothing is refreshed, no fill is invented. A close that
                    # stays due and unpriced for days is closed as NO_PRICE (no exit price, no P&L).
                    if timed and _unpriced_expired(t, et):
                        s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == "OPEN").values(
                            status="NO_PRICE", exit_time=et.astimezone(timezone.utc), exit_price=None, profit=None, profit_pct=None))
                    continue
                last = price.value
                profit, pct = _pnl(t.side, t.entry_price, last, t.shares)
                reason = _exit_reason(_Level(t, stop, target), last, info.get("score"), timed)
                values = {"stop": stop, "target": target, "last_price": last, "profit": profit, "profit_pct": pct}
                if reason:
                    gap = pf.observation_gap(_aware(t.updated_at) or _aware(t.entry_time), et) \
                        if reason in ("STOP", "TARGET") else False
                    exit_px = pf.fill_price(t.side, reason, stop, target, last, gap)
                    profit, pct = _pnl(t.side, t.entry_price, exit_px, t.shares)
                    values.update(exit_price=exit_px, last_price=exit_px, profit=profit, profit_pct=pct,
                                  status=reason, exit_time=et.astimezone(timezone.utc))
                # Guarded by "still OPEN": if another process / tick already closed this trade, nothing is written
                # (no second close, no overwritten P&L, no refresh of a closed row).
                s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == "OPEN").values(**values))

            # open new trades for fresh BUY/SELL (one per strategy+symbol+day)
            if _can_open(et):
                # One trade per strategy+symbol+day AND never a second position while one is still open from an
                # earlier date (a carried position is the same position).
                have = {(x.strategy, x.symbol) for x in
                        s.query(DemoTrade.strategy, DemoTrade.symbol).filter_by(session_date=today).all()}
                have |= {(x.strategy, x.symbol) for x in
                         s.query(DemoTrade.strategy, DemoTrade.symbol).filter_by(status="OPEN").all()}
                for strat, rows in boards.items():
                    for sym, info in rows.items():
                        sig = info.get("signal")
                        entry = info.get("entry")
                        # New entries only from rows whose data passed the freshness gate.
                        if (sig in ("BUY", "SELL") and entry and info.get("tradable", False)
                                and (strat, sym) not in have and _is_equity(sym)):
                            # The last gate, at the moment of opening: a CONFIRMED 15-minute reading and an open
                            # entry lock, read fresh. Exits above never depend on this.
                            if not _entry_allowed(sym, sig, et):
                                continue
                            side = "LONG" if sig == "BUY" else "SHORT"
                            sl, tp = _with_levels(side, entry, info.get("sl"), info.get("tp"))
                            last0 = _live_price(sym, boards, entry)
                            profit0, pct0 = _pnl(side, entry, last0, 100)
                            s.add(DemoTrade(strategy=strat, symbol=sym, side=side, shares=100,
                                            session_date=today, entry_price=entry,
                                            stop=sl, target=tp,
                                            score_at_entry=info.get("score"), last_price=last0,
                                            profit=profit0, profit_pct=pct0, status="OPEN"))
                            have.add((strat, sym))
            s.commit()
        finally:
            s.close()
    except Exception:  # noqa: BLE001 - a failed tick is retried by the next one (nothing is half-written)
        import logging
        logging.getLogger(__name__).exception("paper-trade tick failed; the next tick retries")
    finally:
        _tick_lock.release()


def blotter(strategy: str = "", date: str = "") -> dict:
    ensure_schema()
    # Refresh in the background so the page loads instantly from the DB rather
    # than blocking on the 6-board tick (slow on a cold cache). The next refresh
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
        if day == et.date():                              # positions still open from EARLIER dates (carried over)
            cq = s.query(DemoTrade).filter(DemoTrade.status == "OPEN", DemoTrade.session_date < day)
            if strategy and strategy != "all":
                cq = cq.filter(DemoTrade.strategy == strategy)
            carried = [{
                "id": r.id, "strategy": r.strategy, "strategy_label": MODELS.get(r.strategy, (r.strategy,))[0],
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
            "id": r.id, "strategy": r.strategy, "strategy_label": MODELS.get(r.strategy, (r.strategy,))[0],
            "symbol": r.symbol, "side": r.side, "shares": r.shares,
            "entry_time": r.entry_time.astimezone(ET).strftime("%I:%M:%S %p") if r.entry_time else None,
            "entry_price": r.entry_price, "last_price": r.last_price,
            "chg": round((r.last_price - r.entry_price), 2) if (r.last_price and r.entry_price) else None,
            "stop": r.stop, "target": r.target,
            "profit": r.profit, "profit_pct": r.profit_pct,
            "status": r.status, "score_at_entry": r.score_at_entry,
            "exit_price": r.exit_price,
        } for r in rows]
        # Winners at the top, losers at the bottom (open/flat in the middle).
        out.sort(key=lambda x: (x["profit"] if x["profit"] is not None else 0.0), reverse=True)
        total = round(sum((x["profit"] or 0) for x in out), 2)
        gap_total = round(sum((x["gap_effect"] or 0) for x in out), 2)
        # P&L REALIZED on this date (positions closed today, whatever date they were opened), kept apart from the
        # entry-date attribution above so a carried position never distorts either view
        rq = s.query(DemoTrade).filter(DemoTrade.status != "OPEN", DemoTrade.profit.isnot(None))
        if strategy and strategy != "all":
            rq = rq.filter(DemoTrade.strategy == strategy)
        closed_today = [r for r in rq.all() if r.exit_time and r.exit_time.astimezone(ET).date() == day]
        realized = round(sum(r.profit for r in closed_today), 2)
        wins = sum(1 for x in out if (x["profit"] or 0) > 0)
        closed = sum(1 for x in out if x["status"] != "OPEN")
        return {"status": "OK", "source": SOURCE, "date": str(day),
                "strategies": [{"key": k, "label": v[0]} for k, v in MODELS.items()],
                "rows": out, "total_profit": total, "count": len(out),
                # still-open positions from earlier dates: listed here, NOT in rows / totals (their P&L stays
                # attributed to the date they were opened, as every closed trade's is)
                "carried_open": carried, "carried_open_count": len(carried),
                "gap_effect_total": gap_total, "realized_on_date": realized, "closed_on_date": len(closed_today),
                "realized_note": "realized_on_date = P&L of positions CLOSED on this date; total_profit = trades OPENED on it",
                "open": len(out) - closed, "closed": closed, "wins": wins,
                "rth": _is_rth(et), "after_close": _after_close(et)}
    finally:
        s.close()
