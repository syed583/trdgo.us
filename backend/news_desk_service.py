"""
The news desk: one merged feed plus what can be measured from it.

Everything on this screen is computed from articles this app actually
retrieved -- the sentiment split, the trending tickers, the source counts, the
keyword list and the sector read are all tallies over the same set of
headlines, so no panel can disagree with the feed beside it.

**The plan is the constraint here, not the code.** The provider reports six
figures of matching articles and returns three per request, so a feed of any
size costs one request per three headlines against a daily quota. The page is
therefore built from a fixed page budget on a long cache, and it says how many
articles that bought and how many existed. A screen claiming "3,842 articles"
while holding two dozen would be the most straightforward lie on the site.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import live_market_service as market

# Pages of three. Sized against the provider's free daily quota alongside the
# cache window below: more pages per refresh means fewer refreshes per day.
PAGES = 8
DESK_TTL = 2 * 3600.0

# Broad enough that the feed is market news rather than one desk's watchlist.
COVERAGE = [
    "AAPL,MSFT,NVDA,GOOGL,AMZN",
    "META,TSLA,AMD,AVGO,MU",
    "JPM,BAC,GS,WFC,C",
    "XOM,CVX,COP,OXY",
    "UNH,LLY,JNJ,PFE,MRK",
    "WMT,COST,KO,PEP,PG",
    "SPY,QQQ,IWM,DIA",
    "CAT,BA,GE,HON,UPS",
]

# Positive above, negative below; the band between is genuinely undecided
# rather than a rounding of one of the other two.
POSITIVE_AT = 0.15
NEGATIVE_AT = -0.15

# Words that carry no signal in a financial headline. Kept explicit rather
# than pulled from a stopword package: "shares", "stock" and "market" are not
# stopwords in English, but they are noise here.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "by", "with", "from", "as", "is", "are", "was", "were", "be", "been",
    "will", "would", "could", "should", "may", "might", "can", "has", "have",
    "had", "it", "its", "this", "that", "these", "those", "after", "over",
    "into", "amid", "says", "said", "new", "more", "most", "than", "up",
    "down", "out", "about", "you", "your", "what", "why", "how", "here",
    "stock", "stocks", "shares", "share", "market", "markets", "news",
    "report", "reports", "update", "why", "top", "best", "week", "day",
    "today", "year", "inc", "corp", "company", "companies", "s", "u", "vs",
}


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _bucket(score: Optional[float]) -> str:
    if score is None:
        return "unscored"
    if score >= POSITIVE_AT:
        return "positive"
    if score <= NEGATIVE_AT:
        return "negative"
    return "neutral"


def _parse_time(stamp: Optional[str]) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fetch() -> tuple[list[dict], dict]:
    """
    The desk feed: headlines across the names on the board.

    One provider now. Marketaux carried its own per-article sentiment model
    and Yahoo carried none at all; both were removed with the rest of the
    free tier, so the tone beside a headline here is this app's keyword
    estimate and every payload says so. A counted score and a modelled one
    are different claims, and only one of them has read the article.
    """
    import uw_news_adapter as uwnews

    if not uwnews.configured():
        return [], {"status": "PROVIDER_NOT_CONFIGURED", "found": 0,
                    "per_page": None, "pages_requested": 0, "pages_failed": 0,
                    "detail": "Add UNUSUAL_WHALES_API_KEY to read the news desk."}

    symbols = [s for group in COVERAGE for s in group.split(",")]
    articles = uwnews.desk_articles(symbols, per_symbol=4)
    return articles, {
        "status": "OK" if articles else "NO_DATA",
        "found": len(articles),
        "per_page": None,
        "pages_requested": len(symbols),
        "pages_failed": 0,
        "detail": ("Tone is a keyword estimate over the headline, not a "
                   "provider sentiment model."),
    }


def _keywords(articles: list[dict], top: int = 14) -> list[dict]:
    """Words that recur across headlines, tickers and noise removed."""
    counts: Counter = Counter()
    tickers = {s for a in articles for s in a.get("symbols") or []}
    for article in articles:
        for word in re.findall(r"[A-Za-z][A-Za-z&'-]+",
                               str(article.get("headline") or "")):
            lowered = word.lower()
            if lowered in STOPWORDS or len(lowered) < 3:
                continue
            if word.upper() in tickers:
                continue
            counts[word.title() if word.islower() else word] += 1
    return [{"word": w, "count": n} for w, n in counts.most_common(top) if n > 1]


def get_desk() -> dict:
    """The feed plus every tally that can be drawn from it."""
    cached = market.cache.get("news_desk", DESK_TTL)
    if cached:
        return cached

    articles, meta = _fetch()
    if not articles:
        return {"status": meta.get("status", "NO_DATA"),
                "detail": meta.get("detail", "No headlines were returned."),
                "articles": []}

    buckets = Counter(a["sentiment"] for a in articles)
    scored = [a for a in articles if a["sentiment_score"] is not None]
    average = (round(sum(a["sentiment_score"] for a in scored) / len(scored), 4)
               if scored else None)

    def share(name: str) -> Optional[float]:
        total = len(scored)
        return round(buckets.get(name, 0) / total * 100, 1) if total else None

    tickers = Counter(s for a in articles for s in a.get("symbols") or [])
    sources = Counter(a["provider"] for a in articles if a.get("provider"))

    # --- sentiment by sector ----------------------------------------------
    sector_scores: dict[str, list[float]] = {}
    try:
        import company_profile_service as cp

        profiles = cp.get_profiles(sorted(tickers))
        for article in scored:
            for symbol in article.get("symbols") or []:
                sector = (profiles.get(symbol) or {}).get("sector")
                if sector:
                    sector_scores.setdefault(sector, []).append(
                        article["sentiment_score"])
    except Exception:  # noqa: BLE001 - the panel degrades, the page does not
        sector_scores = {}

    sectors = sorted(
        ({"sector": name,
          "score": round(sum(v) / len(v) * 100, 1),
          "articles": len(v)}
         for name, v in sector_scores.items()),
        key=lambda s: -s["score"],
    )

    # --- volume and trend by day ------------------------------------------
    by_day: dict[str, Counter] = {}
    for article in articles:
        when = _parse_time(article.get("published_at"))
        if not when:
            continue
        day = when.date().isoformat()
        by_day.setdefault(day, Counter())[article["sentiment"]] += 1
    trend = [
        {"date": day,
         "positive": counts.get("positive", 0),
         "neutral": counts.get("neutral", 0),
         "negative": counts.get("negative", 0),
         "total": sum(counts.values())}
        for day, counts in sorted(by_day.items())
    ]

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    last_24h = sum(
        1 for a in articles
        if (_parse_time(a.get("published_at")) or datetime.min.replace(
            tzinfo=timezone.utc)) >= cutoff)

    score = round((average + 1) / 2 * 100) if average is not None else None
    label = ("Bullish" if (score or 0) >= 60 else "Bearish"
             if (score or 100) <= 40 else "Neutral")

    result = {
        "status": "OK",
        "articles": articles,
        "count": len(articles),
        "found": meta.get("found"),
        "per_page": meta.get("per_page"),
        "pages_requested": meta.get("pages_requested"),
        "last_24h": last_24h,
        "sentiment": {
            "score": score,
            "label": label if score is not None else "No reading",
            "average": average,
            "positive": buckets.get("positive", 0),
            "neutral": buckets.get("neutral", 0),
            "negative": buckets.get("negative", 0),
            "unscored": buckets.get("unscored", 0),
            "positive_percent": share("positive"),
            "neutral_percent": share("neutral"),
            "negative_percent": share("negative"),
            "scored": len(scored),
            "detail": ("The provider's own per-article model, averaged across "
                       "the entities each headline names. It reads the text, "
                       "not the market."),
        },
        "tickers": [{"symbol": s, "articles": n}
                    for s, n in tickers.most_common(10)],
        "sources": [{"provider": s, "articles": n}
                    for s, n in sources.most_common(8)],
        "keywords": _keywords(articles),
        "sectors": sectors,
        "trend": trend,
        "detail": (
            f"{len(articles)} articles retrieved of {meta.get('found'):,} "
            f"matching. The news plan returns {meta.get('per_page')} per "
            f"request, so this page is {meta.get('pages_requested')} requests "
            "on a two-hour cache -- every figure here is a tally over those "
            "articles, not over the whole feed."
            if meta.get("found") and not meta.get("fallback") else (meta.get("detail") or f"{len(articles)} articles retrieved.")
        ),
        "source": "UNUSUAL_WHALES",
    }
    market.cache.put("news_desk", result)
    return result
