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
from datetime import datetime, time as dtime
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
    """Timed exit: a regular-session trade at/after the 4pm close, and any trade
    carried over from a previous trading day (closed on the next tick)."""
    n = (now or _et_now())
    if t.session_date and t.session_date < n.date():
        return True
    et_entry = t.entry_time.astimezone(ET) if t.entry_time else None
    return bool(n.time() >= _MARKET_CLOSE and et_entry and et_entry.time() < _MARKET_CLOSE)


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
    out: dict[str, dict] = {}
    for bucket in ("buyers", "sellers", "neutral"):
        for r in (board.get(bucket) or []):
            s = (r.get("symbol") or "").upper()
            if s:
                out[s] = {"signal": r.get("signal"), "score": r.get("score"),
                          "entry": r.get("entry"), "now": r.get("now"),
                          "sl": r.get("sl"), "tp": r.get("tp")}
    return out


def _pnl(side: str, entry: float, last: float, shares: int):
    diff = (last - entry) if side == "LONG" else (entry - last)
    profit = round(diff * shares, 2)
    pct = round((diff / entry) * 100, 2) if entry else 0.0
    return profit, pct


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
            open_trades = s.query(DemoTrade).filter_by(session_date=today, status="OPEN").all()
            for t in open_trades:
                info = boards.get(t.strategy, {}).get(t.symbol) or {}
                if t.stop is None or t.target is None:   # backfill levels on older rows
                    t.stop, t.target = _with_levels(t.side, t.entry_price, t.stop, t.target)
                last = info.get("now") or _live_price(t.symbol, boards, t.last_price or t.entry_price)
                t.last_price = last
                t.profit, t.profit_pct = _pnl(t.side, t.entry_price, last, t.shares)
                reason = _exit_reason(t, last, info.get("score"), _exit_timed(t, et))
                if reason:
                    # Close AT the level that was hit (clean fill), then freeze:
                    # a closed trade is never touched again by later ticks.
                    exit_px = (t.target if reason == "TARGET"
                               else t.stop if reason == "STOP" else last)
                    t.exit_price = exit_px
                    t.last_price = exit_px
                    t.profit, t.profit_pct = _pnl(t.side, t.entry_price, exit_px, t.shares)
                    t.status = reason
                    from models_demo_trades import _utcnow
                    t.exit_time = _utcnow()

            # open new trades for fresh BUY/SELL (one per strategy+symbol+day)
            if _can_open(et):
                have = {(x.strategy, x.symbol) for x in
                        s.query(DemoTrade.strategy, DemoTrade.symbol).filter_by(session_date=today).all()}
                for strat, rows in boards.items():
                    for sym, info in rows.items():
                        sig = info.get("signal")
                        entry = info.get("entry")
                        if sig in ("BUY", "SELL") and entry and (strat, sym) not in have:
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
    except Exception:  # noqa: BLE001
        pass
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
        out = [{
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
        wins = sum(1 for x in out if (x["profit"] or 0) > 0)
        closed = sum(1 for x in out if x["status"] != "OPEN")
        return {"status": "OK", "source": SOURCE, "date": str(day),
                "strategies": [{"key": k, "label": v[0]} for k, v in MODELS.items()],
                "rows": out, "total_profit": total, "count": len(out),
                "open": len(out) - closed, "closed": closed, "wins": wins,
                "rth": _is_rth(et), "after_close": _after_close(et)}
    finally:
        s.close()
