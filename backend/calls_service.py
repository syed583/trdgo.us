"""
Writing calls down, and reading them back.

`record` is called wherever the app reaches a decision on a stock -- a
deliberate analysis run, or the AI Trade board's scan. It stores the decision
together with the working that produced it, so the "why" a screen shows is the
one that was actually used, and so the scorecard can later check it.

`why` turns a stored call into the explanation the screens show: the
parameters that pushed hardest toward the decision, the ones that pushed
against it, and why a call was withheld. It is built from the stored points,
never recomputed, so it cannot drift from the call it explains.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from database import SessionLocal, engine
from models_calls import HORIZONS, TradeCall, create_all

EASTERN = ZoneInfo("America/New_York")
MODEL_VERSION = "directional-16/v1"

# The board rescans every few minutes. Writing a row on every pass would bury
# the real decisions under thousands of identical ones, so a board call is
# only stored when its decision changes or the last stored one has gone stale.
# A deliberate analysis run is always stored: someone asked.
BOARD_RECORD_EVERY = 30 * 60

_ready = False
_ready_lock = threading.Lock()


def _ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if not _ready:
            create_all(engine)
            _ready = True


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _session_dates(horizon: str, now: Optional[datetime] = None) -> tuple:
    """The session a call is made in, and the session it is about."""
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    today = now_et.date()
    if horizon == "TOMORROW":
        target = today + timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
        return today, target
    if horizon == "TODAY":
        return today, today
    # A swing call is about the next few weeks; judged at five sessions.
    target = today
    added = 0
    while added < 5:
        target += timedelta(days=1)
        if target.weekday() < 5:
            added += 1
    return today, target


def _compact_parameters(signals: list[dict]) -> list[dict]:
    """What is worth keeping from each parameter -- not the whole payload."""
    out = []
    for s in signals or []:
        out.append({
            "name": s.get("name"),
            "label": s.get("label"),
            "weight": s.get("weight"),
            "available": s.get("available"),
            "directional": s.get("directional"),
            "points": _num(s.get("points")),
            "points_label": s.get("points_label"),
            "leaning": s.get("leaning"),
            "detail": s.get("detail"),
            "unavailable_reason": s.get("unavailable_reason"),
            "source": s.get("source"),
        })
    return out


def record(symbol: str, result: dict, *, horizon: str = "SWING",
           origin: str = "analysis", price: Optional[float] = None,
           spy: Optional[float] = None,
           explanation: Optional[str] = None) -> Optional[int]:
    """
    Store one call. Returns its id, or None if nothing was stored.

    Never raises: failing to write a call down must not stop the screen that
    made it from showing it.
    """
    try:
        if not result or result.get("status") not in (None, "OK"):
            return None
        decision = result.get("decision")
        if not decision:
            return None
        horizon = horizon if horizon in HORIZONS else "SWING"
        symbol = (symbol or "").upper()

        _ensure_table()
        db = SessionLocal()
        try:
            if origin == "board":
                last = (db.query(TradeCall)
                        .filter(TradeCall.symbol == symbol,
                                TradeCall.horizon == horizon,
                                TradeCall.origin == "board")
                        .order_by(TradeCall.made_at.desc())
                        .first())
                if last is not None:
                    made = last.made_at
                    if made.tzinfo is None:
                        made = made.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - made).total_seconds()
                    if last.decision == decision and age < BOARD_RECORD_EVERY:
                        return last.id

            session, target = _session_dates(horizon)
            market = result.get("market") or {}
            row = TradeCall(
                symbol=symbol,
                horizon=horizon,
                origin=origin,
                session_date=session,
                target_date=target,
                market_session=market.get("session"),
                decision=decision,
                direction_score=_num(result.get("direction_score")),
                confidence=_num(result.get("confidence")),
                agreement_pct=_num(result.get("agreement_pct")),
                coverage_pct=_num(result.get("coverage_pct")),
                actionable=1 if result.get("actionable") else 0,
                price_at_call=_num(price),
                spy_at_call=_num(spy),
                parameters=json.dumps(_compact_parameters(result.get("signals"))),
                reasons=json.dumps(result.get("reasons") or []),
                blocked_reasons=json.dumps(result.get("blocked_reasons") or []),
                explanation=explanation,
                model_version=MODEL_VERSION,
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - recording is never allowed to break a screen
        return None


def why(row: TradeCall, top: int = 4) -> dict:
    """
    The explanation for one stored call.

    Built from the stored points only. Parameters that pushed toward the
    decision come first, strongest first; then the ones that pushed against
    it, because a buy with three strong reasons against it is a different call
    from a buy with none.
    """
    params = json.loads(row.parameters or "[]")
    directional = [p for p in params
                   if p.get("available") and p.get("directional")
                   and p.get("points") not in (None, 0)]

    bullish = sorted((p for p in directional if p["points"] > 0),
                     key=lambda p: -p["points"])
    bearish = sorted((p for p in directional if p["points"] < 0),
                     key=lambda p: p["points"])

    decision = (row.decision or "").upper()
    if "SELL" in decision:
        for_call, against = bearish, bullish
    else:
        for_call, against = bullish, bearish

    def brief(p: dict) -> dict:
        return {"label": p.get("label"), "points": p.get("points"),
                "points_label": p.get("points_label"),
                "detail": p.get("detail")}

    missing = [p.get("label") for p in params
               if p.get("directional") and not p.get("available")]

    return {
        "for": [brief(p) for p in for_call[:top]],
        "against": [brief(p) for p in against[:top]],
        "missing": missing,
        "blocked": json.loads(row.blocked_reasons or "[]"),
        "explanation": row.explanation,
    }


def serialise(row: TradeCall, with_parameters: bool = False) -> dict:
    out = {
        "id": row.id,
        "symbol": row.symbol,
        "horizon": row.horizon,
        "origin": row.origin,
        "made_at": row.made_at.isoformat() if row.made_at else None,
        "session_date": row.session_date.isoformat() if row.session_date else None,
        "target_date": row.target_date.isoformat() if row.target_date else None,
        "market_session": row.market_session,
        "decision": row.decision,
        "direction_score": row.direction_score,
        "confidence": row.confidence,
        "agreement_pct": row.agreement_pct,
        "coverage_pct": row.coverage_pct,
        "price_at_call": row.price_at_call,
        "spy_at_call": row.spy_at_call,
        "model_version": row.model_version,
        "why": why(row),
        "outcome": {
            "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
            "price_close": row.price_close,
            "next_close": row.next_close,
            "return_pct": row.return_pct,
            "spy_return_pct": row.spy_return_pct,
            "excess_pct": row.excess_pct,
            "correct": None if row.correct is None else bool(row.correct),
        },
    }
    if with_parameters:
        out["parameters"] = json.loads(row.parameters or "[]")
    return out


def latest(symbol: str, horizon: Optional[str] = None) -> Optional[dict]:
    _ensure_table()
    db = SessionLocal()
    try:
        q = db.query(TradeCall).filter(TradeCall.symbol == (symbol or "").upper())
        if horizon:
            q = q.filter(TradeCall.horizon == horizon)
        row = q.order_by(TradeCall.made_at.desc()).first()
        return serialise(row, with_parameters=True) if row else None
    finally:
        db.close()


def history(symbol: Optional[str] = None, horizon: Optional[str] = None,
            limit: int = 50) -> list[dict]:
    _ensure_table()
    db = SessionLocal()
    try:
        q = db.query(TradeCall)
        if symbol:
            q = q.filter(TradeCall.symbol == symbol.upper())
        if horizon:
            q = q.filter(TradeCall.horizon == horizon)
        rows = q.order_by(TradeCall.made_at.desc()).limit(max(1, min(limit, 500))).all()
        return [serialise(r) for r in rows]
    finally:
        db.close()


def get(call_id: int) -> Optional[dict]:
    _ensure_table()
    db = SessionLocal()
    try:
        row = db.get(TradeCall, call_id)
        return serialise(row, with_parameters=True) if row else None
    finally:
        db.close()


def _price(symbol: str) -> Optional[float]:
    try:
        import live_market_service as market

        q = market.get_quote(symbol) or {}
        return _num(q.get("price"))
    except Exception:  # noqa: BLE001
        return None


def record_async(symbol: str, result: dict, *, horizon: str = "SWING",
                 origin: str = "analysis") -> None:
    """
    Record a call off the request thread.

    Pricing the stock and SPY for the record can take a provider round trip;
    the screen that made the call should not wait on its own bookkeeping.
    """
    def run() -> None:
        record(symbol, result, horizon=horizon, origin=origin,
               price=_price(symbol), spy=_price("SPY"))

    threading.Thread(target=run, daemon=True, name="record-call").start()


# ---------------------------------------------------------------------------
# the scorecard: was the call right?
# ---------------------------------------------------------------------------

# A session's close is only final a little after four. Judging a Today call at
# 16:00:01 against a close that is still being printed would record a
# provisional price as the outcome for good.
CLOSE_FINAL_ET = (16, 15)

BUY_DECISIONS = {"BUY", "STRONG BUY"}
SELL_DECISIONS = {"SELL", "STRONG SELL"}


def _closes(symbol: str) -> dict:
    """{'2026-09-15': {'open': .., 'close': ..}} from daily bars."""
    import live_market_service as market

    chart = market.get_chart(symbol, "1M") or {}
    out: dict = {}
    for b in chart.get("bars") or []:
        day = str(b.get("t") or "")[:10]
        if day:
            out[day] = {"open": _num(b.get("open")), "close": _num(b.get("close"))}
    return out


def _judge(decision: str, excess: Optional[float]) -> Optional[int]:
    """
    Right or wrong, measured against SPY.

    Against the market, not in absolute terms: a buy that rose 0.3% on a day
    SPY rose 1.2% lagged the thing it was an alternative to, and counting it
    as a win is how a scorecard ends up rewarding nothing but a rising market.
    A withheld call is not judged at all -- it made no directional claim.
    """
    if excess is None:
        return None
    d = (decision or "").upper()
    if d in BUY_DECISIONS:
        return 1 if excess > 0 else 0
    if d in SELL_DECISIONS:
        return 1 if excess < 0 else 0
    return None


def _ready_to_judge(target: date, now: Optional[datetime] = None) -> bool:
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    if target < now_et.date():
        return True
    if target == now_et.date():
        return (now_et.hour, now_et.minute) >= CLOSE_FINAL_ET
    return False


def evaluate_pending(limit: int = 200) -> dict:
    """
    Fill in outcomes for every call whose target session has closed.

    The comparison runs from the price at the moment of the call to the close
    of the session the call was about, for the stock and for SPY. A call with
    no recorded price cannot be judged; after a few days it is marked
    evaluated with no verdict so it is not retried for ever.
    """
    _ensure_table()
    db = SessionLocal()
    judged = skipped = 0
    try:
        rows = (db.query(TradeCall)
                .filter(TradeCall.evaluated_at.is_(None))
                .order_by(TradeCall.made_at.asc())
                .limit(limit).all())
        bars: dict = {}

        def closes(sym: str) -> dict:
            if sym not in bars:
                try:
                    bars[sym] = _closes(sym)
                except Exception:  # noqa: BLE001
                    bars[sym] = {}
            return bars[sym]

        today_et = datetime.now(timezone.utc).astimezone(EASTERN).date()
        for row in rows:
            if not row.target_date or not _ready_to_judge(row.target_date):
                continue
            day = row.target_date.isoformat()
            mine, spy = closes(row.symbol).get(day), closes("SPY").get(day)

            if (not row.price_at_call or not row.spy_at_call
                    or not mine or not spy
                    or not mine.get("close") or not spy.get("close")):
                # A missing bar for a day that should have one is a data gap,
                # not an answer -- give it a few days before giving up.
                if row.target_date < today_et - timedelta(days=3):
                    row.evaluated_at = datetime.now(timezone.utc)
                    skipped += 1
                continue

            ret = (mine["close"] / row.price_at_call - 1.0) * 100.0
            spy_ret = (spy["close"] / row.spy_at_call - 1.0) * 100.0
            excess = ret - spy_ret

            if row.horizon == "TODAY":
                row.price_close, row.spy_close = mine["close"], spy["close"]
            else:
                row.next_open, row.next_close = mine["open"], mine["close"]
                row.spy_next_close = spy["close"]
            row.return_pct = round(ret, 3)
            row.spy_return_pct = round(spy_ret, 3)
            row.excess_pct = round(excess, 3)
            row.correct = _judge(row.decision, excess)
            row.evaluated_at = datetime.now(timezone.utc)
            judged += 1
        db.commit()
    finally:
        db.close()
    return {"judged": judged, "skipped": skipped}


def scorecard(horizon: Optional[str] = None, days: int = 30) -> dict:
    """
    How often the calls were right, and by how much, over a window.

    Split by decision and by score band, because an overall hit rate hides the
    one question that matters for tuning: do higher scores actually do
    better? If a score of 80 is right no more often than a score of 60, the
    number on the screen is not measuring anything.
    """
    _ensure_table()
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    db = SessionLocal()
    try:
        q = db.query(TradeCall).filter(TradeCall.made_at >= since,
                                       TradeCall.correct.isnot(None))
        if horizon:
            q = q.filter(TradeCall.horizon == horizon)
        rows = q.all()
        pq = db.query(TradeCall).filter(TradeCall.made_at >= since,
                                        TradeCall.evaluated_at.is_(None))
        if horizon:
            pq = pq.filter(TradeCall.horizon == horizon)
        pending = pq.count()
    finally:
        db.close()

    def summarise(items: list) -> dict:
        if not items:
            return {"calls": 0, "hit_rate": None, "avg_excess_pct": None}
        right = sum(1 for r in items if r.correct == 1)
        excess = [r.excess_pct for r in items if r.excess_pct is not None]
        return {
            "calls": len(items),
            "hit_rate": round(right / len(items) * 100.0, 1),
            "avg_excess_pct": round(sum(excess) / len(excess), 3) if excess else None,
        }

    by_decision = {d: summarise([r for r in rows if (r.decision or "").upper() == d])
                   for d in ("STRONG BUY", "BUY", "SELL", "STRONG SELL")}

    by_score = {}
    for lo, hi in ((0, 30), (30, 40), (40, 60), (60, 70), (70, 101)):
        by_score[f"{lo}-{min(hi, 100)}"] = summarise(
            [r for r in rows if r.direction_score is not None
             and lo <= r.direction_score < hi])

    overall = summarise(rows)
    return {
        "status": "OK",
        "horizon": horizon or "ALL",
        "days": days,
        "overall": overall,
        "by_decision": by_decision,
        "by_score": by_score,
        "pending": pending,
        "enough_data": overall["calls"] >= 30,
        "note": ("Right means beating SPY in the called direction over the "
                 "call's horizon, measured from the price at the moment of the "
                 "call. Withheld calls make no claim and are not counted. A "
                 "few weeks of calls are needed before these rates mean much."),
    }


# ---------------------------------------------------------------------------
# plain-English explanation, written once and kept
# ---------------------------------------------------------------------------


NEWS_BUDGET = 8.0


def explain(call_id: int, symbol_news: bool = True) -> dict:
    """
    Two plain sentences on a stored call, plus the tone of the symbol's news.

    Written the first time somebody asks and saved on the call, so opening
    the same "why" again costs nothing and always reads the same. The news
    tone is not saved with the call -- headlines move -- and is cached for
    half an hour per symbol instead.
    """
    import claude_service

    _ensure_table()
    db = SessionLocal()
    try:
        row = db.get(TradeCall, call_id)
        if row is None:
            return {"status": "NO_DATA", "detail": "No such call."}
        call = serialise(row)
        explanation = row.explanation
        result = {"status": "OK", "cached": bool(explanation)}

        if not explanation:
            answer = claude_service.explain_call(call)
            if answer.get("status") != "OK":
                return {"status": answer.get("status"),
                        "detail": answer.get("detail")}
            explanation = answer["text"]
            row.explanation = explanation
            db.commit()
            result["model"] = answer.get("model")
        result["explanation"] = explanation
        symbol = row.symbol
    finally:
        db.close()

    if symbol_news:
        headlines: list = []
        try:
            import uw_news_adapter as symbol_news_feed

            # Headlines are a nice-to-have: never let a slow news farm hold
            # the explanation hostage.
            from concurrent.futures import ThreadPoolExecutor

            pool = ThreadPoolExecutor(max_workers=1)
            try:
                news = pool.submit(symbol_news_feed.get_news, symbol, 15).result(
                    timeout=NEWS_BUDGET) or {}
            finally:
                pool.shutdown(wait=False)
            headlines = [i.get("headline") for i in news.get("items") or []]
        except Exception:  # noqa: BLE001 - the explanation stands without news
            headlines = []
        result["news"] = claude_service.news_tone(symbol, headlines)
    return result
