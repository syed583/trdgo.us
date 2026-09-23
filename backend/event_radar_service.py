"""
Upcoming and recent catalysts: earnings proximity, analyst actions, news tone.

What counts as a catalyst here
------------------------------
Three things, and only two of them vote.

Analyst upgrades, downgrades and price-target changes are decisions someone
made about the company, dated, and they point somewhere. News tone is a
weaker read on the same question -- weaker because headline sentiment is a
crude instrument and the samples are small -- but it is available for every
symbol, which the analyst feed is not.

Earnings proximity is the third, and it is deliberately not scored. A report
in three days does not make a stock bullish or bearish; it makes the next
move larger and less predictable. That belongs in sizing and timing, so it is
reported alongside the score rather than folded into it.

Coverage is uneven and the module says so
-----------------------------------------
The analyst feed covers a restricted universe -- AAPL returns 24 actions in
sixty days while NVDA and TSLA return none at all. Where it is missing, the
reading falls back to news tone alone and the evidence records that, so a
thin signal is visible as thin rather than passing for a full one.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import provider_config as cfg

# Sentiment sample sizes are small -- a handful of headlines is normal -- so
# the tone has to be pronounced before it means anything.
NEWS_STRONG = 0.25
NEWS_MILD = 0.08

# Headlines needed before news tone counts at full strength.
#
# Three articles producing a near-maximal reading is not a signal, it is an
# accident of what happened to be published. Below this the component is
# scaled down in proportion, so a thin sample reads as a weak lean rather
# than a strong one.
NEWS_FULL_SAMPLE = 10

# Analyst actions inside this window are treated as current.
ACTION_WINDOW_DAYS = 60

# Inside this many days, an upcoming report dominates everything else the
# model has to say about the next move.
EARNINGS_IMMINENT_DAYS = 7
EARNINGS_NEAR_DAYS = 21

# Providers return the epoch when they mean "unknown", which reads as a date
# fifty-six years in the past rather than as missing data.
_NULL_DATES = {"1970-01-01", "0000-00-00", ""}

CACHE_TTL = 900.0


def _num(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def upcoming_earnings(symbol: str) -> dict:
    """
    Days until the next report.

    Sourced from the options provider rather than the earnings calendar because that
    calendar covers a restricted universe, and a missing earnings date would
    silently read as "no catalyst" for exactly the busiest names.
    """
    try:
        import unusualwhales_service as uw
    except ImportError:
        return {"status": "NOT_CONFIGURED"}

    # Their unusual-activity rows carry the next reporting date for the
    # underlying, which is the same fact the old provider published on its
    # symbol metadata.
    rows = uw._rows(uw.unusual_activity(symbol, limit=1))
    raw = str((rows[0] if rows else {}).get("next_earnings_date") or "").strip()
    if raw in _NULL_DATES:
        return {"status": "NO_DATE",
                "detail": "No scheduled report published for this symbol."}

    try:
        when = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return {"status": "NO_DATE", "detail": f"Unparseable date {raw!r}."}

    days = (when - date.today()).days
    if days < 0:
        return {"status": "PAST", "date": raw, "days_away": days}

    proximity = ("IMMINENT" if days <= EARNINGS_IMMINENT_DAYS
                 else "NEAR" if days <= EARNINGS_NEAR_DAYS else "DISTANT")
    return {"status": "OK", "date": raw, "days_away": days,
            "proximity": proximity}


def _from_unusual_whales(symbol: str, days: int):
    """Analyst actions from the paid feed, or None if it cannot answer."""
    from datetime import timedelta

    try:
        import uw_company_service as uwc

        out = uwc.analyst_actions(symbol, limit=100)
    except Exception:  # noqa: BLE001 - Benzinga still gets its turn
        return None
    if out.get("status") != "OK" or not out.get("rows"):
        return None

    since = (date.today() - timedelta(days=days)).isoformat()
    rows = [r for r in out["rows"] if (r.get("date") or "") >= since]
    if not rows:
        return {"status": "NO_RECENT_ACTIONS",
                "detail": (f"No analyst action on {symbol.upper()} in the "
                           f"last {days} days."),
                "actions": [], "count": 0, "upgrades": 0, "downgrades": 0,
                "targets_raised": 0, "targets_cut": 0, "net": 0,
                "source": out["source"]}

    ups = sum(1 for r in rows if r["lean"] == "UP")
    downs = sum(1 for r in rows if r["lean"] == "DOWN")
    # A target move is its own signal and is counted separately: a firm that
    # keeps its rating and lifts its target has said something, and folding
    # that into the rating count would lose it.
    raised = sum(1 for r in rows if str(r.get("action") or "").upper()
                 in ("RAISED", "UPGRADED", "INITIATED"))
    cut = sum(1 for r in rows if str(r.get("action") or "").upper()
              in ("LOWERED", "DOWNGRADED"))
    latest = rows[0]
    return {
        "status": "OK",
        "targets_raised": raised,
        "targets_cut": cut,
        "actions": [{
            "date": r.get("date"),
            "firm": r.get("firm"),
            "analyst": r.get("analyst"),
            "action": r.get("action"),
            "rating": r.get("recommendation"),
            "target": r.get("target"),
            "lean": r.get("lean"),
        } for r in rows],
        "count": len(rows),
        "upgrades": ups,
        "downgrades": downs,
        "net": ups - downs,
        "latest": {"date": latest.get("date"), "firm": latest.get("firm"),
                   "analyst": latest.get("analyst"),
                   "action": latest.get("action"),
                   "rating": latest.get("recommendation"),
                   "target": latest.get("target")},
        "window_days": days,
        "source": out["source"],
    }


def analyst_actions(symbol: str, days: int = ACTION_WINDOW_DAYS) -> dict:
    """
    Upgrades, downgrades and price-target revisions.

    Unusual Whales leads and Benzinga stays behind it. Benzinga's ratings
    feed covers a restricted universe -- plenty of the board came back "no
    analyst actions published", which reads as "nobody is covering this
    stock" and is a different statement from "our provider does not carry
    it". The paid feed covers the market.
    """
    provider = _from_unusual_whales(symbol, days)
    if provider:
        return provider

    if not cfg.BENZINGA.configured:
        return {"status": "NOT_CONFIGURED"}

    import urllib.parse
    import urllib.request
    from datetime import timedelta

    since = (date.today() - timedelta(days=days)).isoformat()
    params = {
        "token": cfg.BENZINGA.api_key,
        "parameters[tickers]": symbol.upper(),
        "parameters[date_from]": since,
        "pagesize": 100,
    }
    url = (f"{cfg.BENZINGA.base_url}/v2.1/calendar/ratings?"
           + urllib.parse.urlencode(params))
    try:
        request = urllib.request.Request(
            url, headers={"Accept": "application/json",
                          "User-Agent": cfg.USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            import json
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"status": "PROVIDER_OFFLINE"}

    rows = payload.get("ratings") if isinstance(payload, dict) else payload
    rows = rows or []
    if not rows:
        return {"status": "NO_COVERAGE",
                "detail": ("No analyst actions published for this symbol. "
                           "The provider's ratings feed covers a restricted "
                           "universe.")}

    upgrades = sum(1 for r in rows if r.get("action_company") == "Upgrades")
    downgrades = sum(1 for r in rows if r.get("action_company") == "Downgrades")

    raised = cut = 0
    for r in rows:
        now, before = _num(r.get("pt_current")), _num(r.get("pt_prior"))
        if not now or not before:
            continue
        if now > before:
            raised += 1
        elif now < before:
            cut += 1

    latest = rows[0]
    return {
        "status": "OK",
        "actions": len(rows),
        "window_days": days,
        "upgrades": upgrades,
        "downgrades": downgrades,
        "targets_raised": raised,
        "targets_cut": cut,
        "latest": {
            "analyst": latest.get("analyst"),
            "date": latest.get("date"),
            "action": latest.get("action_company"),
            "rating": latest.get("rating_current"),
            "price_target": _num(latest.get("pt_current")),
        },
    }


def news_tone(symbol: str) -> dict:
    """Per-symbol headline sentiment."""
    try:
        import marketaux_news_service as news
    except ImportError:
        return {"status": "NOT_CONFIGURED"}
    if not news.configured():
        return {"status": "NOT_CONFIGURED"}

    try:
        data = news.get_sentiment(symbol, 20)
    except Exception:  # noqa: BLE001
        return {"status": "PROVIDER_OFFLINE"}

    scored = data.get("headlines_scored") or 0
    if not scored:
        return {"status": "NO_HEADLINES"}

    return {
        "status": "OK",
        "average_sentiment": data.get("average_sentiment"),
        "label": data.get("label"),
        "headlines_scored": scored,
        "top": [
            {"headline": i.get("headline"), "tone": i.get("tone")}
            for i in (data.get("items") or [])[:3]
        ],
    }


def get_event_radar(symbol: str) -> dict:
    """
    Catalysts for one symbol, combined into a directional bias.

    Analyst actions lead where they exist: an upgrade is a dated decision by a
    named firm, where headline tone is an aggregate of whatever was written.
    Earnings proximity is carried but never scored -- a report in three days
    changes how much to risk, not which way to lean.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"symbol": symbol, "status": "INVALID_SYMBOL"}

    from live_market_service import cache

    key = f"eventradar:{symbol}"
    cached = cache.get(key, CACHE_TTL)
    if cached:
        return cached

    earnings = upcoming_earnings(symbol)
    actions = analyst_actions(symbol)
    tone = news_tone(symbol)

    parts: list[tuple[str, float, float]] = []
    notes: list[str] = []

    if actions.get("status") == "OK":
        bullish = actions["upgrades"] + actions["targets_raised"]
        bearish = actions["downgrades"] + actions["targets_cut"]
        total = bullish + bearish
        if total:
            parts.append(("analyst_actions",
                          (bullish - bearish) / total, 0.65))
            notes.append(
                f"{actions['upgrades']} upgrades and "
                f"{actions['targets_raised']} target raises against "
                f"{actions['downgrades']} downgrades and "
                f"{actions['targets_cut']} cuts")

    if tone.get("status") == "OK":
        average = _num(tone.get("average_sentiment")) or 0.0
        # Scaled so a pronounced tone reaches full strength and a faint one
        # counts for little; headline sentiment is a crude instrument.
        scaled = max(-1.0, min(1.0, average / NEWS_STRONG))
        if abs(average) < NEWS_MILD:
            scaled *= 0.4
        # A handful of articles cannot support a confident reading, however
        # one-sided they happen to be.
        sample = tone["headlines_scored"]
        if sample < NEWS_FULL_SAMPLE:
            scaled *= sample / NEWS_FULL_SAMPLE
        parts.append(("news_tone", scaled, 0.35))
        notes.append(
            f"news tone {tone.get('label', '').lower()} across "
            f"{sample} headline{'s' if sample != 1 else ''}"
            + (" (thin sample)" if sample < NEWS_FULL_SAMPLE else ""))

    if not parts:
        return {
            "symbol": symbol,
            "bias": None,
            "status": "NO_DATA",
            "detail": ("No analyst actions and no scored headlines. The "
                       "ratings feed covers a restricted universe and the "
                       "news provider returned nothing for this symbol."),
            "earnings": earnings,
            "analyst_actions": actions,
            "news": tone,
            "source": "Benzinga + Marketaux",
        }

    weight = sum(w for _, _, w in parts)
    bias = sum(v * w for _, v, w in parts) / weight

    result = {
        "symbol": symbol,
        "bias": round(bias, 3),
        "detail": "; ".join(notes),
        "components": {name: round(value, 3) for name, value, _ in parts},
        "earnings": earnings,
        "analyst_actions": actions,
        "news": tone,
        # Said explicitly because the number above does not carry it: a report
        # in three days is the dominant fact about the next move regardless of
        # which way the catalysts lean.
        "timing_warning": (
            f"Earnings in {earnings['days_away']} days -- expect a larger and "
            f"less predictable move."
            if earnings.get("status") == "OK"
            and earnings.get("proximity") in ("IMMINENT", "NEAR") else None),
        "status": "OK",
        "source": "Benzinga + Marketaux",
    }
    cache.put(key, result)
    return result
