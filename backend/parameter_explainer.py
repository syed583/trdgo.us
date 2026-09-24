"""
"What does this parameter mean for this company, right now?"

The score panel says a parameter is worth eight points and scored plus four.
That is the arithmetic, not the story. A reader looking at Management Changes
wants to know *who left*; at Implied Volatility, *why options are expensive
this week*; at Funding & Debt, *what the company actually raised*.

The model cannot answer that -- it reads item codes and ratios. The filings
and headlines behind those codes can, so this gathers them and has Claude put
them into two or three sentences.

Claude supplies words here and never numbers. Every figure it is allowed to
use is one this app measured and handed it, and the filings it summarises are
linked beside the answer so the reader can check. Where the evidence does not
actually answer the question -- the 8-K item that says an officer changed but
not who -- the honest answer is that the filing does not say, and the prompt
asks for exactly that rather than a plausible guess.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from live_market_service import cache

SOURCE = "Claude, from the app's own readings"

# A day. These explain a filing or a week's positioning, neither of which
# changes between two clicks, and every call costs money.
TTL = 12 * 3600.0

SYSTEM = """You explain one parameter of a stock model to a retail trader.

You are given: the parameter's name, what it measures, the reading the app took \
for one company, and the evidence behind it -- filings the company made, \
headlines, or figures the app computed.

Write two or three short sentences:
1. What this reading actually says about this company right now, in plain words.
2. What caused it, naming the specific filing, event or figure from the evidence.
3. Only if the evidence does not answer the obvious question, say so plainly -- \
for example that an 8-K reports an officer change without naming the person.

Rules:
- Use only the evidence given. Never add a price, target, percentage, date, \
name or event that is not in the input.
- If the evidence is thin, say what is missing. Never fill a gap with a \
plausible guess, and never speculate about what a company might do next.
- Do not recommend buying, selling, sizing or timing, and do not judge whether \
the reading is good or bad news beyond what the evidence states.
- Plain words. No headings, no bullet points, no hedging boilerplate."""

# What each parameter is asking, in the words a reader would use. Sent to
# Claude so the answer addresses the question rather than restating the label.
MEANING = {
    "implied_volatility": "How expensive options are, which is how big a move "
                          "the market is pricing in. It says nothing about "
                          "direction.",
    "expected_move": "The range the options market expects by expiry.",
    "gamma_exposure": "Whether option dealers' hedging will dampen or amplify "
                      "moves in this stock.",
    "management_change": "Whether directors or senior officers have come or "
                         "gone recently.",
    "merger_activity": "Takeover bids, merger agreements and asset sales the "
                       "company has filed.",
    "funding_activity": "How the company is funding itself: equity raises, new "
                        "or accelerated debt, write-downs.",
    "dividend_trend": "Whether the dividend is rising, held or cut.",
    "fund_flows": "Whether institutions are net buyers or sellers, from their "
                  "quarterly filings.",
    "insider_activity": "Whether company insiders have been buying or selling "
                        "their own stock.",
    "disparity": "How far the options market sits from its usual balance, "
                 "across thirteen readings.",
    "unusual_activity": "Option contracts trading far above their own normal "
                        "volume.",
    "options_flow": "Which side the option premium crossed on -- buying calls "
                    "and selling puts is bullish pressure.",
    "earnings_results": "How recent results landed against expectations.",
    "event_radar": "Analyst upgrades, downgrades and target changes.",
    "ema_trend": "Where price sits against its moving averages.",
    "rsi": "Whether the stock is overbought or oversold on a 14-day basis.",
    "key_levels": "Strikes where open interest is heavy enough to slow price.",
    "volume_pcr": "Put volume against call volume on the day.",
    # Session readings: they exist only in the short outlooks, and they are
    # the ones a reader is most likely to question, because they change
    # while the screen is open.
    "vwap": "Where price sits against the average price actually paid today "
            "-- the session's own reference line.",
    "opening_range": "Whether price broke out of, or fell back inside, the "
                     "range set in the first minutes of trading.",
    "intraday_trend": "The direction of today's own bars, independent of the "
                      "daily chart.",
    "relative_strength_day": "Whether the stock is outpacing or lagging the "
                             "wider market today.",
    "relative_volume": "How today's volume compares with this stock's own "
                       "normal day.",
    "gap_hold": "Whether an opening gap is holding or has been filled.",
    "close_location": "Where the last price sits inside the day's range.",
    "after_hours": "The move since the close, when there has been one.",
    "price_action": "The shape of recent daily bars: higher highs, lower "
                    "lows, or neither.",
    "daily_oi_change": "How open interest changed overnight -- positions "
                       "opened rather than traded.",
    "flow_by_expiry": "Whether the option flow is near-dated or further out.",
    "oi_positioning": "How open interest is distributed across strikes.",
    "ownership_13dg": "Stakes above 5% newly taken or changed.",
    "form4": "Officers' and directors' own trades in their company's stock, "
             "as reported on Form 4.",
}


def _f(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _filings(symbol: str, categories: tuple, limit: int = 6) -> list[dict]:
    """The company's own filings in the categories this parameter reads."""
    try:
        import corporate_events_service as events

        rows = (events.get_events(symbol) or {}).get("events") or []
    except Exception:  # noqa: BLE001
        return []
    keep = [r for r in rows if r.get("category") in categories][:limit]
    return [{"filed": r.get("filed"), "days_ago": r.get("days_ago"),
             "event": r.get("headline"), "means": r.get("detail"),
             "form": r.get("form"), "url": r.get("url")} for r in keep]


def _headlines(symbol: str, limit: int = 6) -> list[dict]:
    try:
        import uw_news_adapter as news

        items = (news.get_news(symbol, limit) or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return []
    return [{"headline": i.get("headline"), "when": i.get("time"),
             "source": i.get("provider")} for i in items[:limit]]


def _context(symbol: str, parameter: str, signal: dict) -> dict:
    """
    The evidence behind one parameter, in the form Claude is allowed to use.

    Filings for the parameters that read filings, headlines for the ones whose
    cause is usually in the news, and the app's own evidence dict for the rest.
    A parameter with no supporting evidence gets none rather than a guess.
    """
    context: dict = {"app_evidence": signal.get("evidence") or {}}

    if parameter in ("management_change",):
        context["filings"] = _filings(symbol, ("leadership",))
        context["headlines"] = _headlines(symbol, 4)
    elif parameter == "merger_activity":
        context["filings"] = _filings(symbol, ("deal",))
        context["headlines"] = _headlines(symbol, 4)
    elif parameter == "funding_activity":
        context["filings"] = _filings(
            symbol, ("funding", "distress", "restructuring", "listing"))
    elif parameter == "dividend_trend":
        try:
            import dividends_service as dividends

            paid = dividends.get_dividends(symbol, 4)
            context["dividends"] = {
                "trend": paid.get("trend"), "growth_pct": paid.get("growth_pct"),
                "yield_pct": paid.get("dividend_yield_pct"),
                "payments": paid.get("payments"),
            }
        except Exception:  # noqa: BLE001
            pass
    elif parameter in ("implied_volatility", "expected_move", "gamma_exposure",
                       "disparity", "unusual_activity", "options_flow"):
        try:
            import disparity_service as disparity

            reading = disparity.get_disparity(symbol)
            context["options_readings"] = [
                {"reading": r["label"], "value": r["value"], "says": r["detail"]}
                for r in reading.get("readings") or [] if r.get("available")]
            context["options_stretch"] = reading.get("stretch")
        except Exception:  # noqa: BLE001
            pass
        if parameter in ("implied_volatility", "expected_move"):
            context["headlines"] = _headlines(symbol, 4)
    elif parameter in ("earnings_results", "event_radar"):
        context["headlines"] = _headlines(symbol, 5)
    return context


def explain(symbol: str, parameter: str, signal: Optional[dict] = None,
            horizon: Optional[str] = None) -> dict:
    """Two or three sentences on what this parameter is saying about a company."""
    import claude_service as claude

    symbol = (symbol or "").upper().strip()
    parameter = (parameter or "").strip()

    if signal is None:
        signal = _signal(symbol, parameter, horizon)
    if not signal:
        return {"status": "NO_DATA", "symbol": symbol, "parameter": parameter,
                "detail": "The model has no reading for that parameter yet."}

    key = f"paramwhy:{symbol}:{parameter}:{signal.get('points_label')}"
    hit = cache.get(key, TTL)
    if hit:
        return {**hit, "cached": True}

    context = _context(symbol, parameter, signal)
    payload = json.dumps({
        "company": symbol,
        "parameter": signal.get("label") or parameter,
        "measures": MEANING.get(parameter, "One input to the model's score."),
        "reading": {
            "scored": signal.get("points_label"),
            "counts_toward_direction": signal.get("directional"),
            "what_the_app_saw": signal.get("detail"),
            "rule_applied": signal.get("rule"),
            "why_unavailable": signal.get("unavailable_reason"),
        },
        "evidence": context,
    }, default=str)

    answer = claude.ask(SYSTEM, payload)
    if answer.get("status") != "OK":
        return {"status": answer.get("status"), "symbol": symbol,
                "parameter": parameter, "detail": answer.get("detail")}

    result = {
        "status": "OK",
        "symbol": symbol,
        "parameter": parameter,
        "label": signal.get("label") or parameter,
        "text": answer.get("text"),
        "filings": context.get("filings") or [],
        "cached": False,
        "model": answer.get("model"),
        "note": ("Written by Claude from the filings and readings above. It "
                 "adds no data of its own; open a filing to check it."),
        "source": SOURCE,
    }
    cache.put(key, result)
    return result


def _find(result: dict, parameter: str) -> Optional[dict]:
    for signal in (result or {}).get("signals") or []:
        if signal.get("name") == parameter:
            return signal
    return None


def _signal(symbol: str, parameter: str,
            horizon: Optional[str] = None) -> Optional[dict]:
    """
    The model's own reading for this parameter.

    The swing score holds most parameters, but the session ones -- VWAP, the
    opening range, relative volume -- exist only inside a short outlook, and
    those are exactly the rows a reader questions while the market is open.
    Looking in one place and reporting "no reading" for the other half of the
    screen is the bug this walks through.
    """
    try:
        import directional_score_service as score

        base = score.get_directional_score(symbol) or {}
    except Exception:  # noqa: BLE001
        return None

    found = _find(base, parameter)
    if found:
        return found

    try:
        import horizon_model as hm

        wanted = (horizon or "").upper()
        order = [h for h in (wanted, "TODAY", "TOMORROW") if h in hm.WEIGHTS]
        seen = set()
        for h in order:
            if h in seen:
                continue
            seen.add(h)
            # Session readings are computed by the outlook but carry no
            # weight, so the weight table is no longer the test of whether
            # an outlook has the reading. Ask the outlook itself.
            if parameter not in hm.WEIGHTS[h] and parameter not in hm.LABELS:
                continue
            found = _find(hm.score_horizon(symbol, h, base=base), parameter)
            if found:
                return found
    except Exception:  # noqa: BLE001 - the swing answer above still stands
        return None
    return None
