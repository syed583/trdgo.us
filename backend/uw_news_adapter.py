"""
Unusual Whales as the app's news backend.

The News & Sentiment screens speak one small interface -- ``get_news``,
``get_sentiment``, ``provider_status`` -- which Marketaux and the IBKR feed
already implement. This is the same interface over the paid headline feed, so
the pages above it did not change.

One thing is deliberately *not* carried over. Marketaux publishes its own
per-article sentiment model; this feed publishes headlines. The tone here is
a keyword estimate over the headline text, and every payload says so rather
than letting a number that was counted from word lists pass for a model's
opinion.
"""

from __future__ import annotations

from typing import Optional

import uw_company_service as company
import unusualwhales_service as uw

SOURCE = "UNUSUAL_WHALES"

# How many headlines must carry a recognised word before a tone is claimed.
# Below this the honest answer is that the sample is too small, not a label.
MIN_SCORED = 3


def configured() -> bool:
    return uw.configured()


def get_news(symbol: str, limit: int = 20) -> dict:
    """Headlines for one symbol, newest first."""
    return company.news(symbol, limit)


def get_sentiment(symbol: str, limit: int = 20) -> dict:
    """
    Headline tone for one symbol, 0 to 100.

    Same scale the other backends use so the screen reads the same, with the
    basis stated: this is counted from the words in the headline, not a
    provider's model of what the article means.
    """
    news = get_news(symbol, limit)
    items = news.get("items") or []
    if news.get("status") != "OK":
        return {"symbol": symbol, "score": None, "label": "UNAVAILABLE",
                "status": news.get("status", "DATA_UNAVAILABLE"),
                "detail": news.get("detail"), "items": [], "source": SOURCE}

    per_item = [{
        "headline": i["headline"],
        "time": i.get("time_label") or i.get("published_at"),
        "url": i.get("url"),
        "sentiment_score": i.get("sentiment_score"),
        "tone": (i.get("sentiment") or "NEUTRAL").title(),
    } for i in items]

    scored = [i["sentiment_score"] for i in items
              if i.get("sentiment_score") is not None]
    # One matched headline out of fifteen is not a tone. NVDA came back
    # "BEARISH, score 0" off a single word in a single headline, which reads
    # on screen exactly like a considered reading of the news.
    if len(scored) < MIN_SCORED:
        return {
            "symbol": symbol, "score": None, "label": "NEUTRAL",
            "headlines_scored": 0, "headlines_total": len(items),
            "items": per_item, "status": "INSUFFICIENT_DATA",
            "detail": (f"Only {len(scored)} of {len(items)} headlines carried "
                       f"a word this estimate recognises -- fewer than the "
                       f"{MIN_SCORED} it needs before claiming a tone."),
            "source": SOURCE,
        }

    average = sum(scored) / len(scored)
    score = round((average + 1) / 2 * 100)
    return {
        "symbol": symbol,
        "score": score,
        "label": ("BULLISH" if score >= 60
                  else "BEARISH" if score <= 40 else "NEUTRAL"),
        "average_sentiment": round(average, 4),
        "headlines_scored": len(scored),
        "headlines_total": len(items),
        "items": per_item,
        "status": "OK",
        "detail": ("Tone is a keyword estimate over the headline, not a "
                   "provider sentiment model."),
        "source": SOURCE,
    }


def desk_articles(symbols: list, per_symbol: int = 4) -> list[dict]:
    """The merged desk feed: headlines across the names on the board."""
    seen: dict = {}
    for symbol in symbols:
        for item in (get_news(symbol, per_symbol).get("items") or []):
            seen[item["headline"]] = item
    articles = list(seen.values())
    articles.sort(key=lambda a: str(a.get("published_at") or ""), reverse=True)
    return articles


def provider_status() -> dict:
    status = uw.provider_status()
    return {
        "provider": "Unusual Whales",
        "configured": configured(),
        "status": status.get("status"),
        "env_var": uw.ENV_KEY,
        "detail": ("Headlines from the options provider's news feed. Tone is "
                   "this app's keyword estimate over the headline, not the "
                   "provider's own model."),
        "source": SOURCE,
    }
