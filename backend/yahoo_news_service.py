"""
Yahoo Finance headlines: the fallback when Marketaux is out of quota.

Marketaux's free plan is 100 requests a day, and when it runs out every news
screen went blank until midnight UTC. Yahoo publishes a per-symbol RSS feed
that needs no key, so headlines keep coming.

What it does not carry is a sentiment model. The tone here is a keyword
estimate over the headline and summary -- cruder than Marketaux's per-entity
scores -- and every item says so, so it is never passed off as the vendor
model it stands in for.
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from live_market_service import cache

SOURCE = "YAHOO_RSS"
FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
TTL = 900.0
FAIL_TTL = 120.0
TIMEOUT = 10.0

SENTIMENT_NOTE = ("Tone is a keyword estimate over the headline, not a "
                  "provider sentiment model.")

POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps",
    "rally", "rallies", "gain", "gains", "record", "upgrade", "upgrades",
    "upgraded", "raise", "raises", "raised", "strong", "growth", "profit",
    "outperform", "buy", "bullish", "boost", "boosts", "wins", "win", "tops",
    "higher", "rise", "rises", "climb", "climbs", "breakthrough", "approval",
    "approved", "partnership", "expands", "optimistic",
}
NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "drop", "drops", "fall", "falls",
    "slump", "slumps", "sink", "sinks", "downgrade", "downgrades", "downgraded",
    "cut", "cuts", "weak", "loss", "losses", "lawsuit", "probe", "investigation",
    "recall", "bearish", "sell", "underperform", "lower", "decline", "declines",
    "warning", "warns", "layoffs", "tumble", "tumbles", "fears", "concern",
    "concerns", "delay", "delays", "fine", "fined", "crash", "slides",
}


def _tone(text: str) -> Optional[float]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    if not pos and not neg:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


def _when(stamp: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    try:
        t = parsedate_to_datetime(stamp or "")
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone.utc).isoformat(), t.astimezone().strftime("%b %d, %H:%M")
    except (TypeError, ValueError):
        return None, stamp


def get_news(symbol: str, limit: int = 10) -> dict:
    """Headlines for one symbol, shaped like ``marketaux_news_service.get_news``."""
    symbol = (symbol or "").upper().strip()
    key = f"yahoo:news:{symbol}"
    hit = cache.get(key, TTL)
    if hit:
        return {**hit, "items": hit["items"][:limit]}
    miss = cache.get(key + ":fail", FAIL_TTL)
    if miss:
        return miss

    try:
        req = urllib.request.Request(FEED.format(symbol=symbol),
                                     headers={"User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=TIMEOUT).read()
        root = ET.fromstring(body)
    except Exception as exc:  # noqa: BLE001
        result = {"symbol": symbol, "items": [], "status": "PROVIDER_OFFLINE",
                  "detail": f"Yahoo feed unavailable: {type(exc).__name__}",
                  "source": SOURCE}
        cache.put(key + ":fail", result)
        return result

    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        summary = (node.findtext("description") or "").strip()
        published, label = _when(node.findtext("pubDate"))
        items.append({
            "article_id": node.findtext("guid") or node.findtext("link"),
            "provider_code": "Yahoo Finance",
            "provider": "Yahoo Finance",
            "time": label,
            "published_at": published,
            "headline": title,
            "summary": summary,
            "url": node.findtext("link"),
            "image_url": None,
            "sentiment_score": _tone(f"{title} {summary}"),
            "sentiment_method": "keyword",
            "match_score": None,
            "industry": None,
            "source": SOURCE,
        })
    items.sort(key=lambda i: i.get("published_at") or "", reverse=True)

    result = {
        "symbol": symbol,
        "items": items,
        "count": len(items),
        "total_available": len(items),
        "status": "OK" if items else "DATA_UNAVAILABLE",
        "detail": SENTIMENT_NOTE if items else f"No recent Yahoo headlines for {symbol}.",
        "source": SOURCE,
    }
    cache.put(key, result)
    return {**result, "items": items[:limit]}


def desk_articles(symbols: list[str], per_symbol: int = 4) -> list[dict]:
    """Merged feed across symbols, shaped like the news desk's articles."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda s: get_news(s, per_symbol), symbols))

    seen: dict[str, dict] = {}
    for symbol, news in zip(symbols, results):
        for item in news.get("items") or []:
            key = item.get("url") or item["headline"]
            score = item.get("sentiment_score")
            sentiment = ("positive" if (score or 0) >= 0.15
                         else "negative" if (score or 0) <= -0.15 else "neutral")
            impact = {"symbol": symbol, "name": symbol, "industry": None,
                      "score": score, "sentiment": sentiment, "match": None}
            if key in seen:
                if symbol not in seen[key]["symbols"]:
                    seen[key]["symbols"].append(symbol)
                    seen[key]["impacts"].append(impact)
                continue
            seen[key] = {
                "id": key,
                "headline": item["headline"],
                "summary": item.get("summary"),
                "url": item.get("url"),
                "image_url": None,
                "provider": "Yahoo Finance",
                "published_at": item.get("published_at"),
                "time_label": item.get("time"),
                "symbols": [symbol],
                "impacts": [impact],
                "sentiment_score": score,
                "sentiment": sentiment,
            }
    articles = list(seen.values())
    articles.sort(key=lambda a: str(a.get("published_at") or ""), reverse=True)
    return articles
