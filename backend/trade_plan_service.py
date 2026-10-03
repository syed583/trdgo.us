"""
Trade Plan.

Turns the app's own readings into the levels a trader actually asks for: where a
long or short is supported, where to stop chasing, where it is wrong (stop), and
where it is going (targets) -- with the reward-to-risk those levels imply and a
plain-English read.

Every number is derived from data already computed elsewhere: the directional
score (your parameters) decides the bias; price-action support/resistance and a
14-day ATR set the entry band and stop; option levels (expected move, call/put
walls, max pain, gamma flip) set the targets. Nothing is invented.

This is analysis, not advice. It shows the levels the data supports and what
invalidates them; it does not size positions or tell anyone to trade.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

SOURCE = "Trdgo model"
EASTERN = ZoneInfo("America/New_York")

# Each horizon is a different trade, so it gets different geometry (all in units
# of ATR). Today is an intraday scalp -- a shallow entry, a tight stop, modest
# targets inside the session, and no distant structural levels. Tomorrow is an
# overnight hold. Swing runs multiple days with the widest targets and blends in
# real support/resistance and the options expected move.
#   dip  = how far below spot (long) the entry zone reaches
#   stop = stop distance below the entry
#   tp1/tp2 = target distances above spot
#   dnc  = do-not-chase distance above spot
#   struct = blend in resistance / call wall / expected move
HORIZON_PARAMS = {
    "TODAY":    {"dip": 0.3, "stop": 0.5, "tp1": 0.8, "tp2": 1.5, "dnc": 0.2, "struct": False, "rr": 1.2},
    "TOMORROW": {"dip": 0.4, "stop": 0.7, "tp1": 1.2, "tp2": 2.2, "dnc": 0.3, "struct": True, "rr": 1.3},
    "SWING":    {"dip": 0.6, "stop": 0.7, "tp1": 1.5, "tp2": 3.0, "dnc": 0.4, "struct": True, "rr": 1.5},
}
RR_MIN = 1.5            # below this the location is poor -- say "wait", don't force it
ATR_PERIOD = 14

_SYSTEM = """You are the Trdgo analyst writing the read for a trade-plan card.

You are given computed LEVELS for one stock (bias, entry zone, do-not-chase line,
stop, targets, reward:risk). Write 2-4 short sentences: what the setup is, why the
entry sits there, what invalidates it (the stop / a bias flip), and what to watch.

Rules:
- Analysis only. Describe the levels; never tell the reader to buy, sell, hold or
  size a position, and give no personalized advice.
- Use only the numbers provided. Do not invent levels or predict exact prices.
- Plain and tight. No disclaimers beyond the card's own."""


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _r(v: Optional[float], d: int = 2) -> Optional[float]:
    return None if v is None else round(v, d)


def _bars(symbol: str) -> list[dict]:
    import live_market_service as market
    chart = market.get_chart(symbol, "1Y")
    if (chart or {}).get("status") != "OK":
        return []
    return [{"date": b["t"], "high": b["high"], "low": b["low"],
             "close": b["close"]} for b in chart.get("bars", [])]


def _intraday_bars(symbol: str) -> list[dict]:
    """Recent intraday bars, for an intraday (Today) ATR."""
    import live_market_service as market
    chart = market.get_chart(symbol, "5D")
    if (chart or {}).get("status") != "OK":
        return []
    return [{"date": b["t"], "high": b["high"], "low": b["low"],
             "close": b["close"]} for b in chart.get("bars", [])]


def _intraday_atr(symbol: str) -> Optional[float]:
    """
    A true intraday volatility unit for the Today plan: the average per-session
    high-to-low range over recent days, built from intraday bars. Unlike the
    daily ATR it excludes the overnight gap, so it reflects only what price
    actually travels while the market is open -- the right scale for a scalp.
    """
    bars = _intraday_bars(symbol)
    if not bars:
        return None
    sessions: dict[str, dict] = {}
    for b in bars:
        day = (b.get("date") or "")[:10]
        if not day or b["high"] is None or b["low"] is None:
            continue
        s = sessions.setdefault(day, {"hi": b["high"], "lo": b["low"]})
        s["hi"] = max(s["hi"], b["high"])
        s["lo"] = min(s["lo"], b["low"])
    ranges = [s["hi"] - s["lo"] for s in sessions.values() if s["hi"] > s["lo"]]
    return sum(ranges) / len(ranges) if ranges else None


def _atr(bars: list[dict], period: int = ATR_PERIOD) -> Optional[float]:
    """Average true range -- how far the stock travels, for stop/zone buffers."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l = bars[i]["high"], bars[i]["low"]
        pc = bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-period:]
    return sum(window) / len(window) if window else None


def _validity(horizon: str) -> dict:
    """When the plan stops being the current read."""
    now = datetime.now()
    h = (horizon or "SWING").upper()
    if h in ("TODAY", "0DTE"):
        return {"label": "This session", "days": 0,
                "expires": now.replace(hour=16, minute=0, second=0,
                                       microsecond=0).isoformat()}
    if h == "TOMORROW":
        return {"label": "Next session", "days": 1,
                "expires": (now + timedelta(days=1)).isoformat()}
    return {"label": "~5 trading days", "days": 5,
            "expires": (now + timedelta(days=7)).isoformat()}


def _read(plan: dict) -> Optional[str]:
    """Claude's plain-English read, grounded only on the computed plan."""
    try:
        import claude_service
        if not claude_service.configured():
            return None
        b = plan
        payload = (
            f"Stock: {b['symbol']}  Spot: {b['spot']}\n"
            f"Bias: {b['bias']} (score {b['score']}, confidence {b['confidence']})\n"
            f"Entry zone: {b['entry']['low']} to {b['entry']['high']}\n"
            f"Do not chase past: {b['do_not_chase']}\n"
            f"Stop loss: {b['stop']}\n"
            f"Target 1: {b['targets']['tp1']}   Target 2: {b['targets']['tp2']}\n"
            f"Reward:Risk: {b['reward_risk']}\n"
            f"Horizon: {b['horizon']} (valid {b['validity']['label']})")
        out = claude_service.ask(_SYSTEM, payload)
        return out.get("text") if out.get("status") == "OK" else None
    except Exception:  # noqa: BLE001 - the read is a nicety, never a blocker
        return None


def get_plan(symbol: str, horizon: str = "SWING", with_read: bool = True) -> dict:
    """A data-derived trade plan for the underlying stock."""
    symbol = (symbol or "").upper().strip()
    horizon = (horizon or "SWING").upper()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    import directional_score_service as dss
    import options_levels_service as ol
    import price_action_service as pa

    import live_market_service as market
    score = dss.get_directional_score(symbol)
    levels = ol.get_levels(symbol)
    quote = market.get_quote(symbol) or {}

    # Live top-of-book for the underlying. Outside regular hours the regular
    # bid/ask can be empty while the extended session has one, so fall back.
    ext = quote.get("extended") or {}
    bid = _f(quote.get("bid")) if quote.get("bid") is not None else _f(ext.get("bid"))
    ask = _f(quote.get("ask")) if quote.get("ask") is not None else _f(ext.get("ask"))
    spread = round(ask - bid, 2) if (bid is not None and ask is not None) else None

    spot = _f(levels.get("spot")) if levels.get("status") == "OK" else None
    if spot is None:
        spot = _f(quote.get("price"))
    if spot is None:
        return {"status": "NO_PRICE", "symbol": symbol,
                "detail": "No underlying price to anchor levels.", "source": SOURCE}

    decision = (score.get("decision") or "WAIT").upper()
    direction_score = _f(score.get("direction_score"))
    confidence = _f(score.get("confidence"))
    actionable = bool(score.get("actionable"))
    long = "BUY" in decision
    short = "SELL" in decision

    bars = _bars(symbol)
    daily_atr = _atr(bars) or (spot * 0.02)   # 2% fallback if history is thin
    # Today scales to the true intraday range (no overnight gap); the other
    # horizons use the daily ATR. Fall back to daily if intraday is unavailable.
    if horizon == "TODAY":
        intraday = _intraday_atr(symbol)
        atr = intraday or daily_atr
        atr_basis = "intraday range" if intraday else "daily ATR"
    else:
        atr = daily_atr
        atr_basis = "daily ATR"
    sr = pa.support_resistance(bars) if bars else {}
    support = _f(sr.get("support"))
    resistance = _f(sr.get("resistance"))

    # Option levels as targets/barriers.
    oi = levels.get("open_interest") or {}
    call_wall = _f(oi.get("call_wall"))
    put_wall = _f(oi.get("put_wall"))
    em = levels.get("expected_move") or {}
    em_range = em.get("range") or {}
    if isinstance(em_range, dict):
        em_hi, em_lo = _f(em_range.get("upper")), _f(em_range.get("lower"))
    elif isinstance(em_range, (list, tuple)) and len(em_range) == 2:
        em_lo, em_hi = _f(em_range[0]), _f(em_range[1])
    else:
        em_hi = em_lo = None

    base = {
        "symbol": symbol, "horizon": horizon, "spot": _r(spot),
        "bid": bid, "ask": ask, "spread": spread,
        "change_percent": _f(quote.get("change_percent")),
        "quote_session": (quote.get("market") or {}).get("session"),
        "score": _r(direction_score, 1), "confidence": _r(confidence, 1),
        "decision": decision,
        "atr": _r(atr), "atr_basis": atr_basis,
        "support": support, "resistance": resistance,
        "validity": _validity(horizon),
        "as_of": datetime.now().isoformat(),
        "source": SOURCE,
        "disclaimer": ("Data-derived reference levels. Analysis, not financial "
                       "advice -- no position sizing or recommendation to trade."),
    }

    # No actionable bias -> no setup. The lean is still reported.
    if not (long or short) or not actionable:
        return {**base, "status": "NO_SETUP", "bias": "NEUTRAL",
                "lean": decision,
                "reasons": score.get("blocked_reasons") or
                ["The model does not point decisively enough for a plan."]}

    # Geometry for this horizon (see HORIZON_PARAMS): Today is a tight intraday
    # scalp, Tomorrow an overnight hold, Swing a multi-day trade.
    hp = HORIZON_PARAMS.get(horizon, HORIZON_PARAMS["SWING"])
    struct = hp["struct"]

    if long:
        bias = "LONG"
        # Buy from a shallow dip up to just above the current price. The small
        # buffer above spot means an at-market entry reads as active, not
        # "waiting", when price ticks a cent higher.
        entry_high = spot + 0.1 * atr
        entry_low = spot - hp["dip"] * atr
        if support and entry_low < support < spot:
            entry_low = support
        entry_mid = (entry_low + entry_high) / 2
        do_not_chase = spot + hp["dnc"] * atr
        stop = entry_low - hp["stop"] * atr
        if support and 0 < (entry_low - support) <= 0.9 * atr:
            stop = support - 0.25 * atr
        # Swing/Tomorrow blend in a real level or the expected move; Today stays
        # on a pure intraday ATR projection (structural levels are too far off).
        tp1_ref = [spot + hp["tp1"] * atr]
        tp2_ref = [spot + hp["tp2"] * atr]
        if struct:
            tp1_ref += [x for x in (resistance, em_hi) if x]
            tp2_ref += [x for x in (call_wall, em_hi) if x]
        tp1, tp2 = max(tp1_ref), max(tp2_ref)
        if tp2 <= tp1:
            tp2 = tp1 + hp["tp1"] * atr
        risk, reward = entry_mid - stop, tp1 - entry_mid
    else:
        bias = "SHORT"
        entry_low = spot - 0.1 * atr
        entry_high = spot + hp["dip"] * atr
        if resistance and spot < resistance < entry_high:
            entry_high = resistance
        entry_mid = (entry_low + entry_high) / 2
        do_not_chase = spot - hp["dnc"] * atr
        stop = entry_high + hp["stop"] * atr
        if resistance and 0 < (resistance - entry_high) <= 0.9 * atr:
            stop = resistance + 0.25 * atr
        tp1_ref = [spot - hp["tp1"] * atr]
        tp2_ref = [spot - hp["tp2"] * atr]
        if struct:
            tp1_ref += [x for x in (support, em_lo) if x]
            tp2_ref += [x for x in (put_wall, em_lo) if x]
        tp1, tp2 = min(tp1_ref), min(tp2_ref)
        if tp2 >= tp1:
            tp2 = tp1 - hp["tp1"] * atr
        risk, reward = stop - entry_mid, entry_mid - tp1

    rr = round(reward / risk, 2) if risk and risk > 0 else None
    rr_min = hp["rr"]

    plan = {
        **base,
        "bias": bias,
        "style": {"TODAY": "Intraday", "TOMORROW": "Overnight",
                  "SWING": "Multi-day swing"}.get(horizon, "Swing"),
        "entry": {"low": _r(entry_low), "high": _r(entry_high),
                  "mid": _r(entry_mid)},
        "do_not_chase": _r(do_not_chase),
        "do_not_chase_label": ("Do not buy above" if long else "Do not sell below"),
        "stop": _r(stop),
        "targets": {"tp1": _r(tp1), "tp2": _r(tp2)},
        "reward_risk": rr,
        "rr_min": rr_min,
        "status": "OK" if (rr and rr >= rr_min) else "WEAK_SETUP",
    }
    if plan["status"] == "WEAK_SETUP":
        plan["reasons"] = [
            f"Reward:risk is {rr if rr is not None else 'undefined'} at these "
            f"levels (needs {rr_min}). The location is poor -- the target is too "
            f"close to entry relative to the stop."]

    # Issue the plan (or advance the one already issued). The freshly computed
    # levels above are only the CANDIDATE -- used to open a new plan or to detect
    # a bias flip. Once a plan is live its levels are fixed; a trader can't act on
    # an entry/stop that moves every time the price ticks.
    tracking = _track(plan)
    plan["tracking"] = tracking

    if tracking:
        # Lock the displayed levels to the issued plan so they do not drift.
        plan["bias"] = tracking["bias"]
        plan["entry"] = tracking["entry"]
        plan["stop"] = tracking["stop"]
        plan["do_not_chase"] = tracking["do_not_chase"]
        plan["do_not_chase_label"] = (
            "Do not buy above" if tracking["bias"] == "LONG" else "Do not sell below")
        plan["targets"] = tracking["targets"]
        plan["reward_risk"] = tracking["reward_risk"]
        plan["issued_at"] = tracking.get("issued_at")
        plan["issued_spot"] = tracking.get("spot_at_issue")
        # An issued plan is a real, live plan regardless of how the candidate
        # scored on this later pass; the lifecycle status carries the rest.
        plan["status"] = "OK"

    if with_read:
        plan["read"] = _read(plan)
    return plan


# ---------------------------------------------------------------------------
# tracking: issue a plan once, then watch what price does to it
# ---------------------------------------------------------------------------

_ready = False
_ready_lock = threading.Lock()


def _ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if not _ready:
            from database import engine
            from models_trade_plans import create_all
            create_all(engine)
            _ready = True


def _session_date(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(EASTERN)).astimezone(EASTERN).date()


def _live_price(symbol: str) -> Optional[float]:
    try:
        import live_market_service as market
        return _f((market.get_quote(symbol) or {}).get("price"))
    except Exception:  # noqa: BLE001
        return None


def _evaluate(row, price: Optional[float]):
    """
    Advance one open plan's status given the current price. Mutates row and
    returns (status, note) if the plan closed on this pass, else None.
    """
    now = datetime.now(timezone.utc)
    was_open = row.resolved_at is None
    row.updated_at = now
    if price is not None:
        row.last_price = price
        if row.best_price is None:
            row.best_price = price
        elif row.bias == "LONG":
            row.best_price = max(row.best_price, price)
        else:
            row.best_price = min(row.best_price, price)

    # Expiry closes anything still open.
    exp = row.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    expired = exp is not None and now > exp

    if price is None:
        if expired and row.status in ("PENDING", "ACTIVE"):
            _close(row, "EXPIRED", "The validity window passed without resolving.", now)
        return (row.status, row.outcome_note) if (was_open and row.resolved_at) else None

    long = row.bias == "LONG"
    entry_hi, entry_lo = row.entry_high, row.entry_low
    p = round(price, 2)

    # Entry: price traded into the zone. For a long the zone sits below spot
    # (buy the dip); until price reaches it the plan is PENDING -- that is the
    # normal state, not an invalidation.
    if not row.entered:
        reached = (price <= (entry_hi or price)) if long \
            else (price >= (entry_lo or price))
        if reached:
            row.entered = 1
            if row.status == "PENDING":
                row.status = "ACTIVE"

    if row.entered:
        # A live trade: stop and targets apply.
        if long:
            if row.stop is not None and price <= row.stop:
                _close(row, "SL_HIT", f"Price {p} hit the stop {row.stop}.", now)
            elif row.tp2 is not None and price >= row.tp2:
                _close(row, "TP2_HIT", f"Price {p} reached target 2 {row.tp2}.", now)
            elif row.tp1 is not None and price >= row.tp1:
                _close(row, "TP1_HIT", f"Price {p} reached target 1 {row.tp1}.", now)
        else:
            if row.stop is not None and price >= row.stop:
                _close(row, "SL_HIT", f"Price {p} hit the stop {row.stop}.", now)
            elif row.tp2 is not None and price <= row.tp2:
                _close(row, "TP2_HIT", f"Price {p} reached target 2 {row.tp2}.", now)
            elif row.tp1 is not None and price <= row.tp1:
                _close(row, "TP1_HIT", f"Price {p} reached target 1 {row.tp1}.", now)
    else:
        # Never entered, but the whole move played out anyway: missed, not hit.
        missed = (row.tp1 is not None and price >= row.tp1) if long \
            else (row.tp1 is not None and price <= row.tp1)
        if missed:
            _close(row, "INVALIDATED",
                   f"Price reached the target ({p}) without ever pulling back "
                   f"into the entry zone -- the move was missed.", now)

    if row.resolved_at is None and expired and row.status in ("PENDING", "ACTIVE"):
        _close(row, "EXPIRED", "The validity window passed without resolving.", now)

    # Report a close that happened on THIS pass, so the caller can log a signal.
    if was_open and row.resolved_at is not None:
        return (row.status, row.outcome_note)
    return None


def _close(row, status: str, note: str, now: datetime) -> None:
    row.status = status
    row.outcome_note = note
    row.resolved_at = now


def _log_event(db, symbol: str, horizon: str, etype: str, note: str,
               from_bias: Optional[str] = None, to_bias: Optional[str] = None,
               price: Optional[float] = None) -> None:
    from models_trade_plans import TradePlanEvent
    db.add(TradePlanEvent(
        symbol=symbol, horizon=horizon, type=etype, note=note,
        from_bias=from_bias, to_bias=to_bias, price=price))


def _serialise(row) -> dict:
    def iso(dt):
        return dt.isoformat() if dt else None
    return {
        "id": row.id, "symbol": row.symbol, "horizon": row.horizon,
        "bias": row.bias, "status": row.status,
        "issued_at": iso(row.issued_at), "expires_at": iso(row.expires_at),
        "spot_at_issue": row.spot_at_issue,
        "entry": {"low": row.entry_low, "high": row.entry_high},
        "do_not_chase": row.do_not_chase, "stop": row.stop,
        "targets": {"tp1": row.tp1, "tp2": row.tp2},
        "reward_risk": row.reward_risk,
        "entered": bool(row.entered),
        "last_price": row.last_price, "best_price": row.best_price,
        "outcome_note": row.outcome_note, "resolved_at": iso(row.resolved_at),
    }


_OPEN = ("PENDING", "ACTIVE")


def _track(plan: dict) -> Optional[dict]:
    """
    Issue the plan (once per symbol/horizon/session) and advance any open one.

    Never raises: tracking must not break the plan a screen is showing.
    """
    try:
        from database import SessionLocal
        from models_trade_plans import TradePlan

        symbol, horizon = plan["symbol"], plan["horizon"]
        _ensure_table()
        db = SessionLocal()
        try:
            price = _live_price(symbol) or plan.get("spot")
            open_row = (db.query(TradePlan)
                        .filter(TradePlan.symbol == symbol,
                                TradePlan.horizon == horizon,
                                TradePlan.status.in_(_OPEN))
                        .order_by(TradePlan.issued_at.desc())
                        .first())

            # An issued plan is fixed: once BUY/SELL is given, its direction and
            # levels do not change if the model's view flips -- it stays live
            # until its stop or a target is hit (or its validity window ends).
            # So a bias flip never retires or re-issues an open plan.
            if open_row is not None:
                ev = _evaluate(open_row, price)
                if ev:
                    _log_event(db, symbol, horizon, ev[0], ev[1],
                               to_bias=open_row.bias, price=price)
                db.commit()
                return _serialise(open_row)

            # Only real setups are issued and tracked.
            if plan.get("status") != "OK":
                return None

            exp = plan.get("validity", {}).get("expires")
            row = TradePlan(
                symbol=symbol, horizon=horizon, session_date=_session_date(),
                expires_at=datetime.fromisoformat(exp) if exp else None,
                bias=plan["bias"], decision=plan.get("decision"),
                direction_score=plan.get("score"), confidence=plan.get("confidence"),
                spot_at_issue=plan.get("spot"),
                entry_low=plan["entry"]["low"], entry_high=plan["entry"]["high"],
                do_not_chase=plan.get("do_not_chase"), stop=plan.get("stop"),
                tp1=plan["targets"]["tp1"], tp2=plan["targets"]["tp2"],
                reward_risk=plan.get("reward_risk"), read=plan.get("read"),
                status="PENDING", entered=0, last_price=price, best_price=price,
            )
            ev = _evaluate(row, price)   # a brand-new plan may already be in its zone
            db.add(row)
            db.flush()
            _log_event(db, symbol, horizon, "NEW_SETUP",
                       f"New {plan['bias']} setup — entry {plan['entry']['low']}"
                       f"–{plan['entry']['high']}, stop {plan.get('stop')}, "
                       f"target {plan['targets']['tp1']}.",
                       to_bias=plan["bias"], price=price)
            if ev:
                _log_event(db, symbol, horizon, ev[0], ev[1],
                           to_bias=row.bias, price=price)
            db.commit()
            return _serialise(row)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def history(symbol: str = "", horizon: str = "", limit: int = 50) -> dict:
    """Past plans and how they resolved, newest first."""
    try:
        from database import SessionLocal
        from models_trade_plans import TradePlan
        _ensure_table()
        db = SessionLocal()
        try:
            q = db.query(TradePlan)
            if symbol:
                q = q.filter(TradePlan.symbol == symbol.upper())
            if horizon:
                q = q.filter(TradePlan.horizon == horizon.upper())
            rows = (q.order_by(TradePlan.issued_at.desc())
                    .limit(max(1, min(limit, 200))).all())
            return {"status": "OK", "plans": [_serialise(r) for r in rows],
                    "source": SOURCE}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__, "plans": [],
                "source": SOURCE}


def evaluate_open(limit: int = 300) -> dict:
    """Advance every open plan against the live price. For a background tick."""
    try:
        from database import SessionLocal
        from models_trade_plans import TradePlan
        _ensure_table()
        db = SessionLocal()
        try:
            rows = (db.query(TradePlan)
                    .filter(TradePlan.status.in_(_OPEN))
                    .limit(limit).all())
            for row in rows:
                ev = _evaluate(row, _live_price(row.symbol))
                if ev:
                    _log_event(db, row.symbol, row.horizon, ev[0], ev[1],
                               to_bias=row.bias, price=row.last_price)
            db.commit()
            return {"status": "OK", "evaluated": len(rows)}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__}


# ---------------------------------------------------------------------------
# signals: a feed of view-changes and plan outcomes across the watchlist
# ---------------------------------------------------------------------------

import threading as _threading  # noqa: E402
import time as _time            # noqa: E402

_scanner_started = False


def _event_row(e) -> dict:
    return {
        "id": e.id, "symbol": e.symbol, "horizon": e.horizon, "type": e.type,
        "from_bias": e.from_bias, "to_bias": e.to_bias, "price": e.price,
        "note": e.note,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def events(symbols: Optional[list[str]] = None, limit: int = 60) -> dict:
    """The signal feed, newest first. Optionally scoped to a set of symbols."""
    try:
        from database import SessionLocal
        from models_trade_plans import TradePlanEvent
        _ensure_table()
        db = SessionLocal()
        try:
            q = db.query(TradePlanEvent)
            if symbols:
                q = q.filter(TradePlanEvent.symbol.in_([s.upper() for s in symbols]))
            rows = (q.order_by(TradePlanEvent.created_at.desc())
                    .limit(max(1, min(limit, 200))).all())
            return {"status": "OK", "events": [_event_row(e) for e in rows],
                    "source": SOURCE}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__, "events": []}


def unread_count(owner: str, symbols: Optional[list[str]] = None) -> dict:
    """How many events since this user last opened the feed."""
    try:
        from database import SessionLocal
        from models_trade_plans import TradePlanEvent, TradePlanSeen
        _ensure_table()
        db = SessionLocal()
        try:
            seen = db.get(TradePlanSeen, owner or "admin")
            q = db.query(TradePlanEvent)
            if symbols:
                q = q.filter(TradePlanEvent.symbol.in_([s.upper() for s in symbols]))
            if seen is not None:
                q = q.filter(TradePlanEvent.created_at > seen.seen_at)
            return {"status": "OK", "count": q.count()}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__, "count": 0}


def mark_seen(owner: str) -> dict:
    """Clear the unread badge for this user."""
    try:
        from database import SessionLocal
        from models_trade_plans import TradePlanSeen
        _ensure_table()
        db = SessionLocal()
        try:
            row = db.get(TradePlanSeen, owner or "admin")
            now = datetime.now(timezone.utc)
            if row is None:
                db.add(TradePlanSeen(owner=owner or "admin", seen_at=now))
            else:
                row.seen_at = now
            db.commit()
            return {"status": "OK"}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__}


def _all_watch_symbols(limit: int = 60) -> list[str]:
    """Distinct symbols on every watchlist -- the universe the scanner monitors."""
    try:
        from database import SessionLocal
        from models_user import WatchlistItem
        db = SessionLocal()
        try:
            rows = db.query(WatchlistItem.symbol).distinct().limit(limit).all()
            return [r[0].upper() for r in rows if r[0]]
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return []


def scan(symbols: Optional[list[str]] = None, horizon: str = "SWING") -> dict:
    """
    Recompute plans for the watched universe so view-flips and hits are detected
    without anyone opening each page. Each get_plan logs its own transitions.
    """
    syms = symbols if symbols is not None else _all_watch_symbols()
    done = 0
    for s in syms:
        try:
            get_plan(s, horizon=horizon, with_read=False)
            done += 1
        except Exception:  # noqa: BLE001 - one bad symbol never stops the scan
            continue
    return {"status": "OK", "scanned": done}


def start_scanner(every: float = 300.0) -> None:
    """Background loop: scan the watchlist every few minutes during market hours."""
    global _scanner_started
    if _scanner_started:
        return
    _scanner_started = True

    def loop() -> None:
        _time.sleep(20)  # let the app settle before the first pass
        while True:
            try:
                import live_market_service as market
                if (market.market_clock() or {}).get("is_open"):
                    scan()
                else:
                    evaluate_open()   # still close out anything that expired
            except Exception:  # noqa: BLE001
                pass
            _time.sleep(every)

    t = _threading.Thread(target=loop, name="trade-plan-scanner", daemon=True)
    t.start()
