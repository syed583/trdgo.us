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
    atr = _atr(bars) or (spot * 0.02)   # 2% fallback if history is thin
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
        "atr": _r(atr), "support": support, "resistance": resistance,
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

    if long:
        bias = "LONG"
        anchor = support if (support and support < spot) else spot - 0.5 * atr
        entry_low = anchor
        entry_high = min(spot, anchor + 0.4 * atr)
        if entry_high <= entry_low:
            entry_high = entry_low + 0.3 * atr
        cap = (resistance - 0.1 * atr) if resistance and resistance > entry_high \
            else None
        do_not_chase = min(entry_high + 0.6 * atr, cap) if cap \
            else entry_high + 0.6 * atr
        stop = (min(support, anchor) if support else anchor) - 0.7 * atr
        tp1 = next((x for x in (resistance, em_hi) if x and x > spot),
                   spot + 1.5 * atr)
        tp2 = next((x for x in (call_wall, em_hi, spot + 3 * atr)
                    if x and x > tp1), tp1 + 1.5 * atr)
        entry_mid = (entry_low + entry_high) / 2
        risk, reward = entry_mid - stop, tp1 - entry_mid
    else:
        bias = "SHORT"
        anchor = resistance if (resistance and resistance > spot) else spot + 0.5 * atr
        entry_high = anchor
        entry_low = max(spot, anchor - 0.4 * atr)
        if entry_low >= entry_high:
            entry_low = entry_high - 0.3 * atr
        floor = (support + 0.1 * atr) if support and support < entry_low else None
        do_not_chase = max(entry_low - 0.6 * atr, floor) if floor \
            else entry_low - 0.6 * atr
        stop = (max(resistance, anchor) if resistance else anchor) + 0.7 * atr
        tp1 = next((x for x in (support, em_lo) if x and x < spot),
                   spot - 1.5 * atr)
        tp2 = next((x for x in (put_wall, em_lo, spot - 3 * atr)
                    if x and x < tp1), tp1 - 1.5 * atr)
        entry_mid = (entry_low + entry_high) / 2
        risk, reward = stop - entry_mid, entry_mid - tp1

    rr = round(reward / risk, 2) if risk and risk > 0 else None

    plan = {
        **base,
        "bias": bias,
        "entry": {"low": _r(entry_low), "high": _r(entry_high),
                  "mid": _r(entry_mid)},
        "do_not_chase": _r(do_not_chase),
        "do_not_chase_label": ("Do not buy above" if long else "Do not sell below"),
        "stop": _r(stop),
        "targets": {"tp1": _r(tp1), "tp2": _r(tp2)},
        "reward_risk": rr,
        "rr_min": RR_MIN,
        "status": "OK" if (rr and rr >= RR_MIN) else "WEAK_SETUP",
    }
    if plan["status"] == "WEAK_SETUP":
        plan["reasons"] = [
            f"Reward:risk is {rr if rr is not None else 'undefined'} at these "
            f"levels (needs {RR_MIN}). The location is poor -- the target is too "
            f"close to entry relative to the stop."]

    if with_read:
        plan["read"] = _read(plan)

    # Issue/advance the tracked plan and attach its live status.
    plan["tracking"] = _track(plan)
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


def _evaluate(row, price: Optional[float]) -> None:
    """Advance one open plan's status given the current price. Mutates row."""
    from models_trade_plans import TradePlan  # noqa: F401 (type clarity)

    now = datetime.now(timezone.utc)
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
        return

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


def _close(row, status: str, note: str, now: datetime) -> None:
    row.status = status
    row.outcome_note = note
    row.resolved_at = now


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

            # A fresh, actionable plan whose bias flips the open one retires it.
            if (open_row is not None and plan.get("status") == "OK"
                    and plan.get("bias") and open_row.bias != plan["bias"]):
                _close(open_row, "INVALIDATED",
                       f"Bias flipped to {plan['bias']}; a new plan was issued.",
                       datetime.now(timezone.utc))
                db.commit()
                open_row = None

            if open_row is not None:
                _evaluate(open_row, price)
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
            _evaluate(row, price)   # a brand-new plan may already be in its zone
            db.add(row)
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
                _evaluate(row, _live_price(row.symbol))
            db.commit()
            return {"status": "OK", "evaluated": len(rows)}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__}
