"""
The same question asked over three different spans of time.

The directional model was built as a swing read: insider filings, earnings
history and the 200-day average all move over weeks. Asked about the next few
hours it answered anyway, and after the open 5 of 18 of its morning calls went
the right way -- not because the parameters were wrong, but because they were
answering a question nobody had asked. A Form 4 filed last Tuesday says
nothing about the next ninety minutes.

So a call now names its horizon, and each horizon is scored on the inputs that
actually bear on it:

  TODAY     Where does this go between now and today's close? Price against
            VWAP, the opening range, the intraday trend, how it is doing
            against SPY today, whether volume is backing the move, whether the
            gap is holding -- and the options tape as it prints.

  TOMORROW  Is it set up for the next session? Where it closed inside the
            day's range, the day against SPY, the after-hours move, the full
            day's options flow, the trend, and anything that landed after the
            bell. Asked after the close, this is the default.

  SWING     The original model, unchanged: the next few weeks.

Scoring is deliberately identical across all three -- the same bias-times-
weight arithmetic and the same coverage, agreement and confidence gates -- so
a TODAY BUY and a SWING BUY clear the same bar. Only the inputs and their
weights differ. None of the weights here has been validated yet; the
scorecard exists to do that, and they should be tuned from it, not from
intuition.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import directional_model as dm

HORIZONS = ("TODAY", "TOMORROW", "SWING")

# Parameters and their weights per horizon. Each set sums to 100 so a score
# means the same thing on every horizon. Names shared with the swing model
# reuse its reading of that parameter; the rest are computed below.
# Every outlook scores the same thirteen market parameters and the same
# seven company ones, on the weights the operator specified.
#
# This is a deliberate simplification of what used to be here. Each outlook
# used to swap in its own inputs -- the today outlook scored VWAP, the
# opening range and the intraday trend in place of forty-one points of tape
# and trend, on the reasoning that a Form 4 filed last week says little
# about the next two hours. One table everywhere costs that: an intraday
# call is now made on the same evidence as a swing call.
#
# What the outlooks still change is *when* a call is recorded and judged
# against -- a TODAY call is scored against today's close, a SWING call
# against the weeks after it. The session readings are still computed and
# shown on the screen; they simply no longer carry weight.
WEIGHTS: dict[str, dict[str, int]] = {
    horizon: dict(dm.WEIGHTS)
    for horizon in ("TODAY", "TOMORROW", "TODAY_PREMARKET", "TOMORROW_EARLY")
}

COMPANY_PARAMS = (
    "insider_activity", "fund_flows", "earnings_results",
    "merger_activity", "funding_activity", "dividend_trend",
    "event_radar",
)

LABELS = {
    "vwap": "Price vs VWAP",
    "opening_range": "Opening Range",
    "intraday_trend": "Intraday Trend",
    "relative_strength_day": "Today vs SPY",
    "relative_volume": "Relative Volume",
    "gap_hold": "Gap Holding",
    "close_location": "Close in Day's Range",
    "after_hours": "After-Hours Move",
}


def default_horizon(session: Optional[str]) -> str:
    """During the session, today; any other time, the next session."""
    return "TODAY" if session in ("OPEN", "PRE_MARKET") else "TOMORROW"


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _ema(values: list[float], span: int) -> Optional[float]:
    if len(values) < span:
        return None
    k = 2.0 / (span + 1)
    e = sum(values[:span]) / span
    for v in values[span:]:
        e = v * k + e * (1 - k)
    return e


def _signal(name: str, bias: Optional[float], weight: int, detail: str = "",
            evidence: Optional[dict] = None, source: str = "",
            unavailable: str = "") -> dm.Signal:
    s = dm.Signal(name, bias, detail=detail, directional=True,
                  label=LABELS.get(name, name.replace("_", " ").title()),
                  evidence=evidence, source=source,
                  unavailable_reason=unavailable)
    s.weight = weight
    return s


def _reuse(base: dict, name: str, weight: int) -> dm.Signal:
    """Take the swing model's reading of a shared parameter, reweighted."""
    for s in base.get("signals") or []:
        if s.get("name") == name:
            # Directionality is the model's own rule, not this function's.
            # Forcing True here made implied volatility vote on direction in
            # the short outlooks -- "-1.1 of 2" on a parameter the app says
            # everywhere is sizing only, which is exactly the corruption the
            # non-directional list exists to prevent.
            sig = dm.Signal(name, s.get("bias") if s.get("available") else None,
                            detail=s.get("detail") or "",
                            directional=name not in dm.NON_DIRECTIONAL,
                            label=s.get("label") or name,
                            evidence=s.get("evidence") or {},
                            source=s.get("source") or "",
                            unavailable_reason=s.get("unavailable_reason") or "")
            sig.weight = weight
            return sig
    return _signal(name, None, weight, unavailable="Not computed for this symbol.")


# ---------------------------------------------------------------------------
# computed parameters
# ---------------------------------------------------------------------------


EASTERN = None


def _et(stamp: Any) -> Optional[datetime]:
    """A bar timestamp in US/Eastern, whatever zone the feed wrote it in."""
    global EASTERN
    if EASTERN is None:
        from zoneinfo import ZoneInfo
        EASTERN = ZoneInfo("America/New_York")
    try:
        t = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(EASTERN)


def _in_session(t: datetime) -> bool:
    return (9, 30) <= (t.hour, t.minute) < (16, 0)


def _today_bars(intraday: dict) -> list[dict]:
    """
    Today's regular-session bars, in order.

    The feed stamps bars in the machine's local time and includes the
    pre-market, so filtering by the date string kept everything from 04:50 ET
    onward. The opening range then came out as the first half hour of the
    pre-market, the gap was measured from a 5 AM print, and VWAP averaged in
    thin overnight trading. Bars are converted to Eastern time and kept only
    between 09:30 and 16:00.
    """
    bars = intraday.get("bars") or []
    stamped = [(_et(b.get("t")), b) for b in bars]
    stamped = [(t, b) for t, b in stamped if t and _f(b.get("close")) is not None]
    if not stamped:
        return []
    today = max(t.date() for t, _ in stamped)
    return [b for t, b in stamped if t.date() == today and _in_session(t)]


def _vwap(bars: list[dict], price: Optional[float], w: int) -> dm.Signal:
    num = den = 0.0
    for b in bars:
        v = _f(b.get("volume")) or 0.0
        hi, lo, cl = _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
        if v <= 0 or None in (hi, lo, cl):
            continue
        num += (hi + lo + cl) / 3.0 * v
        den += v
    if not den or not price:
        return _signal("vwap", None, w, unavailable="No intraday volume yet.")
    vwap = num / den
    dist = (price - vwap) / vwap * 100.0
    # A full vote at 1% from VWAP; beyond that price is extended, not stronger.
    return _signal("vwap", _clamp(dist / 1.0), w,
                   detail=f"Price {price:.2f} is {dist:+.2f}% from VWAP {vwap:.2f}",
                   evidence={"vwap": round(vwap, 2), "distance_pct": round(dist, 3)},
                   source="IBKR 5-min")


def _opening_range(bars: list[dict], price: Optional[float], w: int) -> dm.Signal:
    if len(bars) < 6 or not price:
        return _signal("opening_range", None, w,
                       unavailable="The opening range is set after the first 30 minutes.")
    first = bars[:6]
    hi = max(_f(b.get("high")) or 0 for b in first)
    lo = min(_f(b.get("low")) or 1e12 for b in first)
    if hi <= lo:
        return _signal("opening_range", None, w, unavailable="Flat opening range.")
    if price > hi:
        bias = _clamp((price - hi) / hi * 100.0 / 0.75, 0.35, 1.0)
        detail = f"Broke above the opening range high {hi:.2f}"
    elif price < lo:
        bias = -_clamp((lo - price) / lo * 100.0 / 0.75, 0.35, 1.0)
        detail = f"Broke below the opening range low {lo:.2f}"
    else:
        pos = (price - lo) / (hi - lo)
        bias = (pos - 0.5) * 0.6
        detail = f"Inside the opening range {lo:.2f}-{hi:.2f}"
    return _signal("opening_range", bias, w, detail=detail,
                   evidence={"or_high": round(hi, 2), "or_low": round(lo, 2)},
                   source="IBKR 5-min")


def _intraday_trend(bars: list[dict], w: int) -> dm.Signal:
    closes = [_f(b.get("close")) for b in bars if _f(b.get("close"))]
    fast, slow = _ema(closes, 9), _ema(closes, 20)
    if fast is None or slow is None:
        return _signal("intraday_trend", None, w,
                       unavailable="Needs about 100 minutes of the session.")
    gap = (fast - slow) / slow * 100.0
    return _signal("intraday_trend", _clamp(gap / 0.4), w,
                   detail=f"5-min EMA9 {fast:.2f} vs EMA20 {slow:.2f} ({gap:+.2f}%)",
                   evidence={"ema9": round(fast, 2), "ema20": round(slow, 2)},
                   source="IBKR 5-min")


def _day_change(daily: dict, quote: dict) -> Optional[float]:
    pct = _f(quote.get("change_percent"))
    if pct is not None:
        return pct
    bars = daily.get("bars") or []
    if len(bars) >= 2:
        prev, last = _f(bars[-2].get("close")), _f(bars[-1].get("close"))
        if prev and last:
            return (last / prev - 1.0) * 100.0
    return None


def _relative_strength(stock_pct: Optional[float], spy_pct: Optional[float],
                       w: int) -> dm.Signal:
    if stock_pct is None or spy_pct is None:
        return _signal("relative_strength_day", None, w,
                       unavailable="No day change for the stock or SPY.")
    diff = stock_pct - spy_pct
    return _signal("relative_strength_day", _clamp(diff / 2.0), w,
                   detail=f"{stock_pct:+.2f}% today against SPY {spy_pct:+.2f}% ({diff:+.2f})",
                   evidence={"stock_pct": round(stock_pct, 3),
                             "spy_pct": round(spy_pct, 3)},
                   source="Quotes")


# Earlier sessions' volume does not change during the day, so their history is
# fetched once per half hour per symbol. Asking TWS for ten days of bars on
# every rescore would spend IBKR's historical-data allowance -- about sixty
# requests in ten minutes, shared with every chart in the app -- on figures
# that cannot have moved, and the board alone scores thirty-eight names.
_HISTORY_TTL = 1800.0
_HISTORY_FAIL_TTL = 300.0
_history: dict = {}


def _has_prior_session(chart: dict) -> bool:
    """Does this chart hold a regular session before today?"""
    today = _et(datetime.now(timezone.utc))
    for bar in (chart or {}).get("bars") or []:
        t = _et(bar.get("t"))
        if t and _in_session(t) and (not today or t.date() < today.date()):
            return True
    return False


def _prior_sessions(symbol: str) -> dict:
    import time as _time

    import live_market_service as market

    now = _time.time()
    hit = _history.get(symbol)
    if hit:
        stamp, chart = hit
        ttl = _HISTORY_TTL if (chart.get("bars") or []) else _HISTORY_FAIL_TTL
        if now - stamp < ttl:
            return chart
    # The intraday history comes from TWS, which intermittently refuses one
    # request under concurrency and serves the identical one a second later.
    # Observed live: NVDA's 5D came back empty while TSLA's succeeded, then
    # both succeeded on the next ask. One retry, because the failure is the
    # request rather than the symbol.
    chart: dict = {}
    for _ in range(2):
        try:
            chart = market.get_chart(symbol, "5D") or {}
        except Exception:  # noqa: BLE001
            chart = {}
        if _has_prior_session(chart):
            break

    # A 5D chart holding only today is useless here and must not be kept for
    # half an hour: relative volume compares today against *earlier* sessions,
    # so caching a today-only answer as a success turns the parameter off for
    # thirty minutes and says nothing about why.
    _history[symbol] = (now, chart if _has_prior_session(chart) else {})
    return chart


def _relative_volume_matched(today_bars: list[dict], history: dict,
                             day_pct: Optional[float], w: int) -> dm.Signal:
    """
    Today's volume so far against earlier sessions over the same stretch.

    Both sides run from 09:30 to the same quarter-hour cutoff: today from its
    five-minute bars, earlier sessions from their fifteen-minute ones. Same
    feed, same units, same window -- the first version divided IBKR bar volume
    by a consolidated daily average from another provider and read NVDA at
    0.2x on a day it was running at 0.8x.
    """
    if not today_bars or day_pct is None:
        return _signal("relative_volume", None, w,
                       unavailable="No intraday bars for today yet.")
    last = _et(today_bars[-1].get("t"))
    if not last:
        return _signal("relative_volume", None, w, unavailable="Undated bars.")
    minute = (last.hour * 60 + last.minute) // 15 * 15
    cutoff = (minute // 60, minute % 60)
    if cutoff <= (9, 30):
        return _signal("relative_volume", None, w,
                       unavailable="Too early in the session to compare volume.")

    so_far = sum(_f(b.get("volume")) or 0.0 for b in today_bars
                 if (t := _et(b.get("t"))) and (t.hour, t.minute) < cutoff)

    by_day: dict = {}
    opened: set = set()
    for b in history.get("bars") or []:
        t = _et(b.get("t"))
        if not t or t.date() >= last.date() or not _in_session(t):
            continue
        if (t.hour, t.minute) == (9, 30):
            opened.add(t.date())
        if (t.hour, t.minute) < cutoff:
            by_day[t.date()] = by_day.get(t.date(), 0.0) + (_f(b.get("volume")) or 0.0)

    prior = [v for d, v in by_day.items() if d in opened and v > 0]
    # Two different failures, said as two different things: one is today's
    # feed, the other is the history behind it, and a single message for both
    # sends anyone reading it to look in the wrong place.
    if not so_far:
        return _signal("relative_volume", None, w,
                       unavailable="Today's bars carry no volume yet.")
    if not prior:
        return _signal("relative_volume", None, w,
                       unavailable="No earlier sessions to compare this time of day with.")
    rv = so_far / (sum(prior) / len(prior))
    strength = _clamp((rv - 1.0) / 1.5, 0.0, 1.0)
    bias = strength * (1 if day_pct > 0 else -1 if day_pct < 0 else 0)
    return _signal("relative_volume", bias, w,
                   detail=(f"Volume {rv:.1f}x the last {len(prior)} session(s) "
                           f"by {cutoff[0]}:{cutoff[1]:02d}"),
                   evidence={"relative_volume": round(rv, 2),
                             "baseline_sessions": len(prior)},
                   source="IBKR intraday")


def _gap_hold(daily: dict, bars: list[dict], price: Optional[float],
              w: int) -> dm.Signal:
    d = daily.get("bars") or []
    if not d or not bars or not price:
        return _signal("gap_hold", None, w, unavailable="No prior close.")
    # The previous session's close, found by date. Taking the second-last bar
    # assumed today's live candle was already appended, which it is not before
    # the first update of the day.
    today = _et(bars[0].get("t"))
    earlier = [b for b in d if today and (_et(b.get("t")) or today).date() < today.date()]
    prev_close = _f(earlier[-1].get("close")) if earlier else None
    open_ = _f(bars[0].get("open"))
    if not prev_close or not open_:
        return _signal("gap_hold", None, w, unavailable="No opening print.")
    gap = (open_ / prev_close - 1.0) * 100.0
    if abs(gap) < 0.25:
        return _signal("gap_hold", 0.0, w, detail=f"No meaningful gap ({gap:+.2f}%)")
    holding = (price - open_) * gap >= 0
    bias = _clamp(gap / 2.0) * (1.0 if holding else -0.6)
    # Worded by direction. "Giving it back" read as bearish on a stock that
    # gapped down and then rallied, which is the bullish case.
    if gap > 0:
        detail = (f"Gapped up {gap:.2f}% and is "
                  f"{'holding the gain' if holding else 'fading back toward the close'}")
    else:
        detail = (f"Gapped down {abs(gap):.2f}% and is "
                  f"{'staying down' if holding else 'recovering'}")
    return _signal("gap_hold", bias, w, detail=detail,
                   evidence={"gap_pct": round(gap, 3), "open": open_},
                   source="IBKR 5-min")


def _close_location(daily: dict, w: int) -> dm.Signal:
    bars = daily.get("bars") or []
    if not bars:
        return _signal("close_location", None, w, unavailable="No daily bar.")
    b = bars[-1]
    hi, lo, cl = _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
    if None in (hi, lo, cl) or hi <= lo:
        return _signal("close_location", None, w, unavailable="No range today.")
    loc = (cl - lo) / (hi - lo)
    where = "near the high" if loc > 0.75 else "near the low" if loc < 0.25 else "mid-range"
    return _signal("close_location", (loc - 0.5) * 2.0, w,
                   detail=f"Closed {where} ({loc * 100:.0f}% of the {lo:.2f}-{hi:.2f} range)",
                   evidence={"location": round(loc, 3)}, source="Daily bars")


def _after_hours(quote: dict, w: int) -> dm.Signal:
    ext = quote.get("extended") or {}
    pct = _f(ext.get("change_percent"))
    if pct is None:
        return _signal("after_hours", None, w,
                       unavailable="No extended-hours print yet.")
    label = ext.get("session_label") or "Extended hours"
    return _signal("after_hours", _clamp(pct / 2.0), w,
                   detail=f"{label}: {pct:+.2f}% vs the close at {_f(ext.get('price')) or 0:.2f}",
                   evidence={"change_pct": pct},
                   source=ext.get("source") or "UNUSUAL_WHALES")


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def _score(signals: list[dm.Signal], possible: int) -> dict:
    """
    The model's own arithmetic and gates, against this horizon's weight.

    Coverage is measured against the horizon's full weight, not the weight
    that happened to arrive -- the same rule the swing model keeps, for the
    same reason: one lone parameter must not claim full coverage.
    """
    available = [s for s in signals if s.available]
    present = sum(s.weight for s in available)
    if not present:
        return {
            "direction_score": None, "decision": dm.NO_TRADE, "lean": "NO_DATA",
            "actionable": False, "confidence": 0, "coverage_pct": 0.0,
            "agreement_pct": 0.0,
            "blocked_reasons": ["No parameter for this horizon could be computed."],
            "signals": [s.as_dict() for s in signals], "reasons": [],
        }

    net = sum(s.points for s in available) / present
    score = max(0.0, min(100.0, 50 + net * 50))
    coverage = present / possible * 100 if possible else 0.0
    if net > 0:
        agreeing = sum(s.weight for s in available if (s.bias or 0) > 0)
    elif net < 0:
        agreeing = sum(s.weight for s in available if (s.bias or 0) < 0)
    else:
        agreeing = 0
    agreement = agreeing / present * 100
    conviction = abs(score - 50) * 2
    confidence = max(0.0, min(100.0,
                              coverage * 0.40 + agreement * 0.35 + conviction * 0.25))

    lean = dm._decision(score)
    blocks: list[str] = []
    if coverage < dm.MIN_COVERAGE:
        blocks.append(f"Only {coverage:.0f}% of this horizon's inputs returned "
                      f"data (needs {dm.MIN_COVERAGE:.0f}%).")
    if agreement < dm.MIN_AGREEMENT:
        blocks.append(f"The parameters disagree -- {agreement:.0f}% of the "
                      f"available weight leans the same way (needs "
                      f"{dm.MIN_AGREEMENT:.0f}%).")
    if confidence < dm.MIN_CONFIDENCE:
        blocks.append(f"Confidence {confidence:.0f} is below the "
                      f"{dm.MIN_CONFIDENCE:.0f} threshold.")
    if lean == "WAIT":
        blocks.append("The evidence does not point anywhere decisively.")

    actionable = not blocks
    return {
        "direction_score": round(score, 1),
        "decision": lean if actionable else dm.NO_TRADE,
        "lean": lean,
        "actionable": actionable,
        "blocked_reasons": blocks,
        "confidence": round(confidence, 1),
        "coverage_pct": round(coverage, 1),
        "agreement_pct": round(agreement, 1),
        "conviction_pct": round(conviction, 1),
        "weight_available": present,
        "weight_possible": possible,
        "signals": [s.as_dict() for s in signals],
        "reasons": [s.detail for s in sorted(available, key=lambda s: abs(s.points),
                                             reverse=True) if s.detail][:6],
    }


# The board rescores every name every few minutes; five-minute bars cannot
# change faster than five minutes, so it reads them from a short cache rather
# than asking TWS for them again on every pass. A deliberate analysis run does
# not use this -- it wants the bar that printed a moment ago.
_INTRADAY_TTL = 240.0
# How long a good session read stands in for a failed refetch.
_INTRADAY_KEEP = 900.0
_intraday_cache: dict = {}


def _intraday(symbol: str, cached: bool) -> dict:
    import time as _time

    import live_market_service as market

    if cached:
        hit = _intraday_cache.get(symbol)
        if hit and _time.time() - hit[0] < _INTRADAY_TTL and (hit[1].get("bars") or []):
            return hit[1]
    chart = market.get_chart(symbol, "1D") or {}
    if _today_bars(chart):
        _intraday_cache[symbol] = (_time.time(), chart)
        return chart
    # A failed or empty fetch must not wipe out bars that were fine a minute
    # ago: that turned a BUY into DO NOT TRADE ("no intraday bars") and back
    # again on the next pass, so names kept appearing and vanishing from the
    # board. Keep the last good session read for a while instead.
    hit = _intraday_cache.get(symbol)
    if hit and _time.time() - hit[0] < _INTRADAY_KEEP and _today_bars(hit[1]):
        return hit[1]
    return chart


def score_horizon(symbol: str, horizon: str, base: Optional[dict] = None,
                  cached: bool = False) -> dict:
    """
    Score one symbol for one horizon.

    ``base`` is the swing model's result when the caller already has it; the
    shared parameters are read from it rather than fetched a second time.
    """
    import directional_score_service as ds
    import live_market_service as market

    horizon = (horizon or "SWING").upper()
    base = base or ds.get_directional_score(symbol)
    if horizon not in WEIGHTS:
        return {**base, "horizon": "SWING"}

    weights = WEIGHTS[horizon]
    quote = market.get_quote(symbol) or {}
    spy = market.get_quote("SPY") or {}
    daily = market.get_chart(symbol, "1M") or {}
    price = _f(quote.get("price"))
    day_pct = _day_change(daily, quote)
    spy_pct = _f(spy.get("change_percent"))

    signals: list[dm.Signal] = []
    session = ((base.get("market") or market.market_clock() or {}).get("session"))
    # Before the bell there is no session to read yet, so a call for today is
    # made from what the day will open on: yesterday's close and range, the
    # pre-market move, and yesterday's options positioning.
    premarket = horizon == "TODAY" and session != "OPEN"
    if horizon == "TODAY" and not premarket:
        intraday = _intraday(symbol, cached)
        bars = _today_bars(intraday)
        session_builders = (
            ("vwap", lambda w: _vwap(bars, price, w)),
            ("opening_range", lambda w: _opening_range(bars, price, w)),
            ("intraday_trend", lambda w: _intraday_trend(bars, w)),
            ("relative_strength_day",
             lambda w: _relative_strength(day_pct, spy_pct, w)),
            ("relative_volume",
             lambda w: _relative_volume_matched(
                 bars, _prior_sessions(symbol), day_pct, w)),
            ("gap_hold", lambda w: _gap_hold(daily, bars, price, w)),
        )
        # Built whatever the table says. A parameter the table does not
        # weigh is still worth showing -- VWAP and the opening range are the
        # reason to look at a today call at all -- so it is computed at zero
        # weight: visible to the reader, silent in the arithmetic.
        for name, build in session_builders:
            signals.append(build(weights.get(name, 0)))
        for name in ("options_flow", "unusual_activity", "volume_pcr",
                     "key_levels"):
            if name in weights:
                signals.append(_reuse(base, name, weights[name]))
        # The rest of the table, read from the swing result.
        built = {s.name for s in signals}
        for name, weight in weights.items():
            if name not in built and name not in COMPANY_PARAMS:
                signals.append(_reuse(base, name, weight))
        basis = "Intraday price and volume since the open, and the options tape as it prints."
    else:
        early = horizon == "TOMORROW" and session in ("PRE_MARKET", "OPEN")
        if premarket:
            weights = WEIGHTS["TODAY_PREMARKET"]
        elif early:
            weights = WEIGHTS["TOMORROW_EARLY"]
        builders = {
            "close_location": lambda w: _close_location(daily, w),
            "relative_strength_day": lambda w: _relative_strength(day_pct, spy_pct, w),
            "after_hours": lambda w: _after_hours(quote, w),
        }
        # Same rule as the intraday branch: what the table does not weigh is
        # still shown, at zero.
        for name, build in builders.items():
            if name not in weights:
                signals.append(build(0))
        for name, w in weights.items():
            if name in COMPANY_PARAMS:
                continue          # appended below, for both branches alike
            build = builders.get(name)
            signals.append(build(w) if build else _reuse(base, name, w))
        basis = ("Today's full session through the close, the after-hours move, "
                 "and the day's options flow.")
        if premarket:
            basis = ("Before the open: the pre-market move leads, with yesterday's "
                     "close and options flow behind it. Switches to the live "
                     "intraday read at 9:30 AM ET.")
        elif early:
            basis = ("Before today's close: trend, options positioning, earnings and "
                     "scheduled events -- what carries into the next session.")
    # Everything this outlook weighs but has not built above -- the company
    # twenty, and whatever the branch did not compute itself -- read from the
    # swing result that already has it.
    #
    # Swept generically rather than by name on purpose. Adding a weight and
    # forgetting to attach its signal silently shrank the outlook: today's
    # column totalled eighty-six of a hundred and the missing fourteen showed
    # as "no data" on parameters that were sitting in the base result.
    built = {s.name for s in signals}
    for name, weight in weights.items():
        if name not in built:
            signals.append(_reuse(base, name, weight))

    out = _score(signals, sum(weights.values()))
    out.update({
        "status": "OK",
        "symbol": (symbol or "").upper(),
        "horizon": horizon,
        "basis": basis,
        "market": base.get("market") or market.market_clock(),
        "model": ("HORIZON_TODAY_PREMARKET" if premarket else f"HORIZON_{horizon}"),
        "premarket": premarket,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "note": ("Weights for this horizon are a starting point and have not "
                 "been validated; the scorecard measures how they perform."),
    })
    return out
