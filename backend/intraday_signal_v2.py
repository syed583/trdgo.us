"""Intraday signal engine v2 -- turns the shared scorer into tracked signals.

Wraps intraday_model.score() with the parts that need time and memory:
  * G2 time window: new signals only 10:00-15:30 ET; all open closed at 15:55.
  * G8 confirmation: score must stay beyond the threshold on 2 scans >=3 min
    apart before a signal is issued.
  * Hysteresis: a BUY stays BUY until score < 60 (SELL until > 40), with a 15-min
    minimum hold before any score-based exit (TP/SL exit any time).
  * Entry = price at confirmation; SL = entry -/+ 0.5*ATR(14); TP = +/-1.0*ATR
    (2:1); trail to breakeven at +0.5 ATR then 0.5 ATR behind the best price.
  * Exit reasons: TP, SL, TRAIL, SCORE_DROP, MARKET_FLIP, EOD.

State and the log live in the DB (table intraday_sig_v2), so a page refresh never
creates, removes or alters rows -- rows change only when the state actually
changes. A daily summary gives the real win rate.

Session = America/New_York. Only the Trdgo Stock TODAY scan drives the lifecycle;
the Analysis page just displays the same score read-only.
"""
from __future__ import annotations

import uw_priority as _uwp

from datetime import datetime, time, timezone
from typing import Optional

import tab_settings as cfg
import intraday_indicators as ind
import intraday_model as im

_ET = ind._ET

# Tunables (spec defaults; override via tab_settings IM2_*).
_NEW_START = time(10, 0)
_NEW_END = time(15, 30)
_EOD = time(15, 55)
_CONFIRM_SCANS = 2
_CONFIRM_GAP_MIN = 3
_MIN_HOLD_MIN = 15
_BUY_EXIT_BELOW = 60.0            # hysteresis: BUY holds until score < this
_SELL_EXIT_ABOVE = 40.0          # SELL holds until score > this
_SL_ATR = 0.5
_TP_ATR = 1.0
_TRAIL_TRIGGER_ATR = 0.5

_ready = False


def _now_et() -> datetime:
    return datetime.now(_ET)


def _session(now: Optional[datetime] = None) -> str:
    return (now or _now_et()).date().isoformat()


def _atr(symbol: str) -> Optional[float]:
    return im.atr(symbol)              # cached ~6h in the model


# --------------------------------------------------------------------------- #
#  Persistence
# --------------------------------------------------------------------------- #
def _db():
    from database import SessionLocal
    return SessionLocal()


def _ensure() -> bool:
    global _ready
    if _ready:
        return True
    try:
        from sqlalchemy import text
        db = _db()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS intraday_sig_v2 ("
                " id SERIAL PRIMARY KEY,"
                " symbol VARCHAR(20) NOT NULL,"
                " session_date DATE NOT NULL,"
                " state VARCHAR(16) NOT NULL,"       # STRONG BUY/BUY/SELL/STRONG SELL/CLOSED
                " side VARCHAR(4),"                  # BUY / SELL
                " is_open BOOLEAN DEFAULT TRUE,"
                " entry DOUBLE PRECISION,"
                " now_price DOUBLE PRECISION,"
                " exit_price DOUBLE PRECISION,"
                " tp DOUBLE PRECISION,"
                " sl DOUBLE PRECISION,"
                " best_price DOUBLE PRECISION,"
                " atr DOUBLE PRECISION,"
                " be_moved BOOLEAN DEFAULT FALSE,"
                " score_in DOUBLE PRECISION,"
                " score_now DOUBLE PRECISION,"
                " signal_ts TIMESTAMPTZ,"
                " status_ts TIMESTAMPTZ,"
                " exit_reason VARCHAR(16),"
                " was_state VARCHAR(16))"))
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS intraday_sig_v2_changes ("
                " id SERIAL PRIMARY KEY,"
                " symbol VARCHAR(20) NOT NULL,"
                " session_date DATE NOT NULL,"
                " ts TIMESTAMPTZ NOT NULL,"
                " from_state VARCHAR(16),"
                " to_state VARCHAR(16),"
                " pl_pct DOUBLE PRECISION)"))
            # per (symbol, session) confirmation progress (not shown)
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS intraday_sig_v2_pending ("
                " symbol VARCHAR(20) NOT NULL,"
                " session_date DATE NOT NULL,"
                " side VARCHAR(4),"
                " count INTEGER DEFAULT 0,"
                " first_ts TIMESTAMPTZ,"
                " last_ts TIMESTAMPTZ,"
                " PRIMARY KEY (symbol, session_date))"))
            db.commit()
            _ready = True
            return True
        except Exception:  # noqa: BLE001
            db.rollback()
            return False
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return False


def _open_row(db, symbol: str, sess: str):
    """The open signal for ``symbol`` -- whatever session it was opened in. A signal stays open (and keeps being
    managed) until a rule closes it; a change of date is not a rule. ``sess`` is kept for callers that pass it."""
    from sqlalchemy import text
    return db.execute(text(
        "SELECT * FROM intraday_sig_v2 WHERE symbol=:s AND is_open=TRUE ORDER BY id DESC LIMIT 1"),
        {"s": symbol}).mappings().first()


def _pending(db, symbol: str, sess: str):
    from sqlalchemy import text
    return db.execute(text(
        "SELECT * FROM intraday_sig_v2_pending WHERE symbol=:s AND session_date=:d"),
        {"s": symbol, "d": sess}).mappings().first()


def _pl_pct(side: str, entry: float, price: float) -> float:
    if not entry:
        return 0.0
    return ((price - entry) / entry * 100) if side == "BUY" else ((entry - price) / entry * 100)


# --------------------------------------------------------------------------- #
#  The per-ticker step (called by the TODAY board scan)
# --------------------------------------------------------------------------- #
@_uwp.tagged(_uwp.LIVE, "intraday_engine")
def step(symbol: str, sc: Optional[dict] = None, now: Optional[datetime] = None) -> Optional[dict]:
    """Advance one ticker's intraday signal using the current v2 score. Returns
    the current open/closed row as a dict (or None if nothing tracked). Writes to
    the DB only when the state actually changes."""
    if not _ensure():
        return None
    symbol = symbol.upper()
    now = now or _now_et()
    sess = _session(now)
    sc = sc or im.score(symbol)
    score = sc.get("score")
    price = sc.get("spot")
    from sqlalchemy import text
    db = _db()
    try:
        row = _open_row(db, symbol, sess)
        # ---- manage an open position ----
        if row:
            out = _manage_open(db, dict(row), sc, now)
            db.commit()
            return out
        # ---- no open position: look for a new confirmed signal ----
        out = _maybe_open(db, symbol, sess, sc, now)
        db.commit()
        return out
    except Exception:  # noqa: BLE001
        db.rollback()
        return None
    finally:
        db.close()


def _manage_open(db, row: dict, sc: dict, now: datetime) -> dict:
    from sqlalchemy import text
    side = row["side"]
    price = sc.get("spot")
    score = sc.get("score")
    if price is None:
        return _row_public(row)
    if sc.get("stock_stale"):                  # the stock's own bars are old: no executable price, no exit booked
        return _row_public(row)
    entry = row["entry"]
    atr = row["atr"] or 0.0
    best = row["best_price"] or entry
    sl = row["sl"]
    be_moved = row["be_moved"]
    # update best + trailing stop
    if side == "BUY":
        best = max(best, price)
        if not be_moved and atr and price >= entry + _TRAIL_TRIGGER_ATR * atr:
            sl = max(sl, entry); be_moved = True
        if be_moved and atr:
            sl = max(sl, best - _SL_ATR * atr)
    else:
        best = min(best, price)
        if not be_moved and atr and price <= entry - _TRAIL_TRIGGER_ATR * atr:
            sl = min(sl, entry); be_moved = True
        if be_moved and atr:
            sl = min(sl, best + _SL_ATR * atr)

    held_min = _minutes(row["signal_ts"], now)
    exit_reason = None
    tp, cur_sl = row["tp"], sl
    if side == "BUY":
        if tp and price >= tp:
            exit_reason = "TP"
        elif cur_sl and price <= cur_sl:
            exit_reason = "TRAIL" if be_moved else "SL"
    else:
        if tp and price <= tp:
            exit_reason = "TP"
        elif cur_sl and price >= cur_sl:
            exit_reason = "TRAIL" if be_moved else "SL"
    # market flip: SPY crosses VWAP against the trade
    if not exit_reason and sc.get("spy_above_vwap") is not None:
        if side == "BUY" and sc["spy_above_vwap"] is False:
            exit_reason = "MARKET_FLIP"
        elif side == "SELL" and sc["spy_above_vwap"] is True:
            exit_reason = "MARKET_FLIP"
    # score drop (hysteresis) -- only after the minimum hold
    if not exit_reason and held_min >= _MIN_HOLD_MIN and score is not None:
        if side == "BUY" and score < _BUY_EXIT_BELOW:
            exit_reason = "SCORE_DROP"
        elif side == "SELL" and score > _SELL_EXIT_ABOVE:
            exit_reason = "SCORE_DROP"
    # EOD: five minutes before the session's close on a TRADING day (15:55 ET; 12:55 on an early-close day) -- never
    # on a weekend or an exchange holiday, and never because the date changed.
    if not exit_reason:
        import market_calendar
        if market_calendar.is_trading_day(now.date()) and now.time() >= market_calendar.eod_time(now.date()):
            exit_reason = "EOD"

    if exit_reason:
        # Guarded by "still open": if another scan already closed this signal nothing is written, so there is no
        # second close, no second change-log row and no overwritten exit price.
        res = db.execute(text(
            "UPDATE intraday_sig_v2 SET is_open=FALSE, state='CLOSED', was_state=:ws,"
            " now_price=:p, exit_price=:p, sl=:sl, best_price=:b, be_moved=:be,"
            " score_now=:sn, status_ts=:ts, exit_reason=:er WHERE id=:id AND is_open=TRUE"),
            {"ws": row["state"], "p": price, "sl": cur_sl, "b": best, "be": be_moved,
             "sn": score, "ts": now, "er": exit_reason, "id": row["id"]})
        if getattr(res, "rowcount", 1) == 0:
            cur = db.execute(text("SELECT * FROM intraday_sig_v2 WHERE id=:id"), {"id": row["id"]}).mappings().first()
            return _row_public(dict(cur) if cur else row)
        _change(db, row["symbol"], row["session_date"], now, row["state"], "CLOSED",
                _pl_pct(side, entry, price))
        row.update({"is_open": False, "state": "CLOSED", "was_state": row["state"],
                    "now_price": price, "exit_price": price, "sl": cur_sl,
                    "exit_reason": exit_reason, "status_ts": now, "score_now": score})
        return _row_public(row)

    # still open: possibly upgrade/downgrade the state badge (BUY <-> STRONG BUY)
    new_state = _state_for(side, score)
    changed = new_state != row["state"]
    res = db.execute(text(
        "UPDATE intraday_sig_v2 SET now_price=:p, sl=:sl, best_price=:b, be_moved=:be,"
        " score_now=:sn, state=:st" + (", status_ts=:ts" if changed else "") +
        " WHERE id=:id AND is_open=TRUE"),
        {"p": price, "sl": cur_sl, "b": best, "be": be_moved, "sn": score,
         "st": new_state, "ts": now, "id": row["id"]})
    if getattr(res, "rowcount", 1) == 0:                  # closed meanwhile by another scan: do not touch it
        cur = db.execute(text("SELECT * FROM intraday_sig_v2 WHERE id=:id"), {"id": row["id"]}).mappings().first()
        return _row_public(dict(cur) if cur else row)
    if changed:
        _change(db, row["symbol"], row["session_date"], now, row["state"], new_state,
                _pl_pct(side, entry, price))
    row.update({"now_price": price, "sl": cur_sl, "best_price": best, "be_moved": be_moved,
                "score_now": score, "state": new_state})
    return _row_public(row)


def _maybe_open(db, symbol: str, sess: str, sc: dict, now: datetime) -> Optional[dict]:
    from sqlalchemy import text
    call = sc.get("call")
    side = sc.get("side")
    score = sc.get("score")
    price = sc.get("spot")
    # Only a fully gated BUY/SELL call is eligible, inside the new-signal window.
    # A new entry (or one more confirmation) needs HEALTHY data: status OK means SPY and the
    # stock's own 5-minute bars are live, not reused last-good bars (MARKET_STALE/STOCK_STALE).
    eligible = (call in ("BUY", "STRONG BUY", "SELL", "STRONG SELL")
                and sc.get("status") == "OK"
                and side in ("BUY", "SELL") and price is not None
                and _NEW_START <= now.time() <= _NEW_END)
    if eligible:
        import intraday_15m
        if not intraday_15m.entry_check(symbol, side, ind.market_phase(now))["allowed"]:
            eligible = False                       # the 15-minute candles do not confirm this side (no switch)
    pend = _pending(db, symbol, sess)
    if not eligible:
        if pend:                                   # lost the setup -> reset confirmation
            db.execute(text("DELETE FROM intraday_sig_v2_pending WHERE symbol=:s AND session_date=:d"),
                       {"s": symbol, "d": sess})
        return None
    # G8 confirmation: need _CONFIRM_SCANS scans >= _CONFIRM_GAP_MIN min apart, same side.
    if not pend or pend["side"] != side:
        db.execute(text(
            "INSERT INTO intraday_sig_v2_pending (symbol, session_date, side, count, first_ts, last_ts)"
            " VALUES (:s,:d,:sd,1,:ts,:ts)"
            " ON CONFLICT (symbol, session_date) DO UPDATE SET side=:sd, count=1,"
            " first_ts=:ts, last_ts=:ts"),
            {"s": symbol, "d": sess, "sd": side, "ts": now})
        return None
    gap = _minutes(pend["last_ts"], now)
    if gap < _CONFIRM_GAP_MIN:
        return None                                 # too soon to count as a second scan
    new_count = (pend["count"] or 1) + 1
    db.execute(text("UPDATE intraday_sig_v2_pending SET count=:c, last_ts=:ts"
                    " WHERE symbol=:s AND session_date=:d"),
               {"c": new_count, "ts": now, "s": symbol, "d": sess})
    if new_count < _CONFIRM_SCANS:
        return None
    # Confirmed -> issue the signal, but only if the 15-minute confirmation STILL holds and entries are not
    # locked, read fresh at this very moment (minutes may have passed since the first scan).
    import intraday_15m
    if not intraday_15m.entry_check(symbol, side, ind.market_phase(now), execution=True)["allowed"]:
        db.execute(text("DELETE FROM intraday_sig_v2_pending WHERE symbol=:s AND session_date=:d"),
                   {"s": symbol, "d": sess})
        return None
    atr = sc.get("atr") or _atr(symbol) or 0.0
    if side == "BUY":
        tp = price + _TP_ATR * atr if atr else None
        sl = price - _SL_ATR * atr if atr else None
    else:
        tp = price - _TP_ATR * atr if atr else None
        sl = price + _SL_ATR * atr if atr else None
    state = _state_for(side, score)
    db.execute(text(
        "INSERT INTO intraday_sig_v2 (symbol, session_date, state, side, is_open, entry,"
        " now_price, tp, sl, best_price, atr, score_in, score_now, signal_ts, status_ts)"
        " VALUES (:s,:d,:st,:sd,TRUE,:e,:e,:tp,:sl,:e,:atr,:sc,:sc,:ts,:ts)"),
        {"s": symbol, "d": sess, "st": state, "sd": side, "e": price, "tp": tp, "sl": sl,
         "atr": atr, "sc": score, "ts": now})
    db.execute(text("DELETE FROM intraday_sig_v2_pending WHERE symbol=:s AND session_date=:d"),
               {"s": symbol, "d": sess})
    _change(db, symbol, sess, now, None, state, 0.0)
    return step_public_new(symbol, sess, state, side, price, tp, sl, atr, score, now)


def _state_for(side: str, score: Optional[float]) -> str:
    if score is None:
        return "BUY" if side == "BUY" else "SELL"
    if side == "BUY":
        return "STRONG BUY" if score >= im._s("STRONG_BUY") else "BUY"
    return "STRONG SELL" if score <= im._s("STRONG_SELL") else "SELL"


def _minutes(ts, now: datetime) -> float:
    if ts is None:
        return 0.0
    try:
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts.astimezone(_ET)).total_seconds() / 60.0
    except Exception:  # noqa: BLE001
        return 0.0


def _change(db, symbol, sess, now, frm, to, pl):
    from sqlalchemy import text
    db.execute(text(
        "INSERT INTO intraday_sig_v2_changes (symbol, session_date, ts, from_state, to_state, pl_pct)"
        " VALUES (:s,:d,:ts,:f,:t,:pl)"),
        {"s": symbol, "d": str(sess), "ts": now, "f": frm, "t": to, "pl": round(pl, 3)})


def _row_public(row: dict) -> dict:
    side = row.get("side")
    entry = row.get("entry")
    nowp = row.get("exit_price") if not row.get("is_open") else row.get("now_price")
    pl = _pl_pct(side, entry, nowp) if (entry and nowp) else None
    return {
        "symbol": row.get("symbol"), "state": row.get("state"), "side": side,
        "is_open": bool(row.get("is_open")),
        "entry": entry, "now": nowp, "exit": row.get("exit_price") if not row.get("is_open") else None,
        "tp": row.get("tp"), "sl": row.get("sl"),
        "pl_pct": round(pl, 2) if pl is not None else None,
        "pl_dollar_100": round(pl / 100 * (entry or 0) * 100, 2) if pl is not None else None,
        "score_in": row.get("score_in"), "score_now": row.get("score_now"),
        "signal_ts": _iso(row.get("signal_ts")), "status_ts": _iso(row.get("status_ts")),
        "exit_reason": row.get("exit_reason"), "was_state": row.get("was_state"),
        "session": str(row.get("session_date")) if row.get("session_date") else None,
    }


def step_public_new(symbol, sess, state, side, entry, tp, sl, atr, score, now):
    return _row_public({"symbol": symbol, "state": state, "side": side, "is_open": True,
                        "entry": entry, "now_price": entry, "tp": tp, "sl": sl,
                        "score_in": score, "score_now": score, "signal_ts": now,
                        "status_ts": now, "exit_price": None, "exit_reason": None})


def _iso(ts):
    if ts is None:
        return None
    try:
        if isinstance(ts, str):
            return ts
        return ts.astimezone(_ET).isoformat()
    except Exception:  # noqa: BLE001
        return str(ts)


# --------------------------------------------------------------------------- #
#  Read side: the signal log + the daily summary
# --------------------------------------------------------------------------- #
def log_rows(session_date: Optional[str] = None, limit: int = 300) -> list[dict]:
    if not _ensure():
        return []
    from sqlalchemy import text
    sess = session_date or _session()
    db = _db()
    try:
        rows = db.execute(text(
            "SELECT * FROM intraday_sig_v2 WHERE session_date=:d ORDER BY"
            " is_open DESC, status_ts DESC LIMIT :n"), {"d": sess, "n": limit}).mappings().all()
        if sess == _session():                                # still-open signals from EARLIER sessions, first
            carried = db.execute(text(
                "SELECT * FROM intraday_sig_v2 WHERE is_open=TRUE AND session_date<:d ORDER BY session_date, id"),
                {"d": sess}).mappings().all()
            rows = list(carried) + list(rows)
        out = []
        for r in rows:
            pub = _row_public(dict(r))
            pub["carried"] = bool(r["is_open"] and str(r["session_date"]) < sess)
            pub["held_min"] = round(_minutes(r["signal_ts"],
                                    r["status_ts"].astimezone(_ET) if r["status_ts"] and not r["is_open"]
                                    else _now_et()), 1) if r["signal_ts"] else None
            out.append(pub)
        return out
    except Exception:  # noqa: BLE001
        return []
    finally:
        db.close()


def summary(session_date: Optional[str] = None) -> dict:
    if not _ensure():
        return {}
    from sqlalchemy import text
    sess = session_date or _session()
    db = _db()
    try:
        rows = db.execute(text(
            "SELECT side, entry, exit_price, is_open, exit_reason FROM intraday_sig_v2"
            " WHERE session_date=:d"), {"d": sess}).mappings().all()
        carried_open = 0
        if sess == _session():
            carried_open = int(db.execute(text(
                "SELECT COUNT(*) FROM intraday_sig_v2 WHERE is_open=TRUE AND session_date<:d"), {"d": sess}).scalar() or 0)
        issued = len(rows)
        closed = [r for r in rows if not r["is_open"] and r["entry"] and r["exit_price"]]
        wins = sum(1 for r in closed if _pl_pct(r["side"], r["entry"], r["exit_price"]) > 0)
        losses = len(closed) - wins
        pls = [_pl_pct(r["side"], r["entry"], r["exit_price"]) for r in closed]
        by_reason: dict = {}
        for r in closed:
            by_reason.setdefault(r["exit_reason"] or "?", 0)
            by_reason[r["exit_reason"] or "?"] += 1
        return {
            "session": sess, "issued": issued, "closed": len(closed),
            "open": issued - len(closed), "carried_open": carried_open, "wins": wins, "losses": losses,
            "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "avg_pl_pct": round(sum(pls) / len(pls), 2) if pls else None,
            "by_exit_reason": by_reason,
        }
    except Exception:  # noqa: BLE001
        return {}
    finally:
        db.close()


def universe() -> list[str]:
    try:
        import ai_trade_service as ats
        return [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    except Exception:  # noqa: BLE001
        return []


_last_scored: dict = {}            # symbol -> epoch of its last live rescore
_last_score: dict = {}             # symbol -> its last display score (ranks the tiers)
_cost = {"per_pass": None, "ctx": None}      # measured live requests: one rescore / one context refresh
_last_ctx = [0.0]


def _live_calls() -> int:
    import uw_budget
    return int(uw_budget.get().state["groups"]["live"])


def _active_symbols(sess: str) -> set:
    """Names with an open position or a confirmation in progress: they are always rescored."""
    if not _ensure():
        return set()
    from sqlalchemy import text
    db = _db()
    try:
        a = db.execute(text("SELECT symbol FROM intraday_sig_v2 WHERE is_open=TRUE")).all()   # any date
        b = db.execute(text("SELECT symbol FROM intraday_sig_v2_pending WHERE session_date=:d"),
                       {"d": sess}).all()
        return {r[0] for r in a} | {r[0] for r in b}
    except Exception:  # noqa: BLE001
        return set()
    finally:
        db.close()


def _hours_left(now: datetime) -> float:
    end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return max(0.0, (end - now).total_seconds() / 3600.0)


def tier_plan(syms: list, now: datetime) -> Optional[dict]:
    """The tier split for this pass, or None when tiering is off."""
    t = cfg.INTRADAY_TIERS
    if not t.get("enabled", True):
        return None
    import intraday_tiers as tiers
    import uw_budget
    snap = uw_budget.get().snapshot()
    per_hour = tiers.hourly_budget(snap, _hours_left(now))
    forced = _active_symbols(_session(now)) & set(syms)
    ctx = (_cost["ctx"] or 0.0) * 3600.0 / float(t["hot_period_s" if forced else "warm_period_s"])   # context bars per scan
    cost = _cost["per_pass"] or float(t["default_cost_per_pass"])
    p = tiers.plan(tiers.rank(syms, _last_score), forced, max(0.0, per_hour - ctx), cost)
    p["budget_per_hour"] = round(per_hour, 1)
    p["cost_per_pass"] = round(cost, 2)
    return p


def _is_due(sym: str, tiers_: Optional[dict], ts: float) -> bool:
    last = _last_scored.get(sym)
    if last is None or tiers_ is None:
        return True
    import intraday_tiers as tiers
    return ts - last >= tiers.period_for(sym, tiers_) - 5.0


@_uwp.tagged(_uwp.LIVE, "intraday_engine")
def scan_once(now: Optional[datetime] = None) -> dict:
    """Score + step the universe names that are DUE this pass (tiered by how close they are to an
    actionable signal), then compose the Today board. Names not due keep their last score on the board
    -- the data-safety gate turns a score that has aged past its limit into WAIT. This is what drives
    confirmations and exits -- call it on a cadence (the board endpoint warms it in the background)."""
    import time as _t
    now = now or _now_et()
    syms = universe()
    # an open position is managed even if its symbol has left the universe since it was opened
    syms = syms + [x for x in sorted(_active_symbols(_session(now))) if x not in syms]
    ts = _t.time()
    tiers_ = tier_plan(syms, now)
    due = [s for s in syms if _is_due(s, tiers_, ts)]
    # Critical market data FIRST, when any name is due: SPY, QQQ and the sector ETFs the universe maps
    # to (retry up to 2x, reuse last-good < 5 min). Every stock score then reads them from the warm cache.
    spy_status = "missing"
    try:
        import intraday_inputs as ii
        before = _live_calls()
        _, spy_stale = im.bars_meta("SPY", critical=True)
        spy_bars, _ = im.bars_meta("SPY", critical=True)
        spy_status = "ok" if (spy_bars and not spy_stale) else "stale" if spy_bars else "missing"
        if due or ts - _last_ctx[0] > 600:
            im.bars_meta("QQQ", critical=True)
            etfs = {ii._TICKER_ETF.get(s.upper()) for s in syms}
            for etf in {e for e in etfs if e}:
                im.bars_meta(etf, critical=True)
            _last_ctx[0] = ts
            spent = _live_calls() - before
            if spent > 0:
                _cost["ctx"] = float(spent)
    except Exception:  # noqa: BLE001
        pass

    import swr
    import intraday_tiers as tiers_mod
    rows = []
    for sym in syms:
        try:
            tier = None if tiers_ is None else tiers_mod.tier_of(sym, tiers_)
            if sym in due:
                c0 = _live_calls()
                sc = im.score(sym)
                spent = _live_calls() - c0
                if spent > 0:
                    prev = _cost["per_pass"]
                    _cost["per_pass"] = min(40.0, float(spent) if prev is None else 0.8 * prev + 0.2 * spent)
                _last_scored[sym] = _t.time()
                sig = step(sym, sc, now)
            else:
                sc = dict(swr.peek(f"im2:{sym}") or {})      # last live score, kept for display
                at = swr.held_at(f"im2:{sym}")
                if at is None or _t.time() - at > float(cfg.INTRADAY_FRESHNESS["engine_score"][1]):
                    sc["status"], sc["call"] = "STALE_SCORE", None     # too old to be a candidate at all
                sig = None
            if sc.get("score") is not None:
                _last_score[sym] = sc.get("score")
            rows.append({
                "symbol": sym, "score": sc.get("score"), "call": sc.get("call"),
                "status": sc.get("status"), "coverage_pct": sc.get("coverage_pct"),
                "side": sc.get("side"), "spot": sc.get("spot"),
                "bars_ok": sc.get("bars_ok"), "signal": sig, "live_tier": tier,
                "rescored": sym in due,
            })
        except Exception:  # noqa: BLE001
            continue
    out = _compose(rows, now, spy_status)
    if tiers_ is not None:
        out["tiers"] = {k: tiers_[k] for k in ("hot", "warm", "cold", "periods_s", "cost_per_hour",
                                               "budget_per_hour", "cost_per_pass", "fits", "reliable")}
        out["tiers"]["rescored_this_pass"] = len(due)
    return out


def _compose(rows: list[dict], now: datetime, spy_status: str = "ok") -> dict:
    def _open(r, side):
        s = r.get("signal")
        return bool(s and s.get("is_open") and s.get("side") == side)
    buyers = [r for r in rows if _open(r, "BUY")]
    sellers = [r for r in rows if _open(r, "SELL")]
    withheld = [r for r in rows if r.get("status") == "WITHHELD"]
    open_syms = {r["symbol"] for r in buyers + sellers}
    no_edge = [r for r in rows if r["symbol"] not in open_syms
               and r.get("status") == "OK"
               and r.get("call") in ("BUY", "STRONG BUY", "SELL", "STRONG SELL")]
    buyers.sort(key=lambda r: r.get("score") or 0, reverse=True)
    sellers.sort(key=lambda r: r.get("score") or 100)
    no_edge.sort(key=lambda r: abs((r.get("score") or 50) - 50), reverse=True)
    bars_ok = sum(1 for r in rows if r.get("bars_ok"))
    total = len(rows)
    return {
        "status": "OK", "model": im.MODEL, "asof": now.isoformat(),
        "session": _session(now), "window_open": _NEW_START <= now.time() <= _NEW_END,
        "phase": ind.market_phase(now),
        "counts": {"buy": len(buyers), "sell": len(sellers),
                   "no_edge": len(no_edge), "withheld": len(withheld)},
        "health": {"bars_ok": bars_ok, "total": total, "spy_status": spy_status,
                   "market_stale": spy_status != "ok"},
        "buyers": buyers, "sellers": sellers, "no_edge": no_edge, "withheld": withheld,
        "summary": summary(_session(now)),
    }


def changes(session_date: Optional[str] = None, limit: int = 300) -> list[dict]:
    if not _ensure():
        return []
    from sqlalchemy import text
    sess = session_date or _session()
    db = _db()
    try:
        rows = db.execute(text(
            "SELECT symbol, ts, from_state, to_state, pl_pct FROM intraday_sig_v2_changes"
            " WHERE session_date=:d ORDER BY ts DESC LIMIT :n"), {"d": sess, "n": limit}).mappings().all()
        return [{"symbol": r["symbol"], "ts": _iso(r["ts"]), "from": r["from_state"],
                 "to": r["to_state"], "pl_pct": r["pl_pct"]} for r in rows]
    except Exception:  # noqa: BLE001
        return []
    finally:
        db.close()
