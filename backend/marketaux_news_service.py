"""
Marketaux: company news headlines with per-symbol sentiment.

Replaces the IBKR news feed, which needs TWS running and an entitlement.

The sentiment here is the provider's own per-entity ``sentiment_score`` in
[-1, 1], not a keyword count over headlines like the IBKR path used. That is a
real improvement -- the old scorer could only see words it had a list for -- but
it is still a vendor model, so the payload says where the number came from
rather than presenting it as fact.

An article can mention several tickers; only the entity matching the requested
symbol is used, so a story about TPG that merely name-drops AAPL does not move
AAPL's sentiment.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any, Optional

import provider_config as cfg
from live_market_service import cache, num

SOURCE = "MARKETAUX"

# Free tier is 100 requests/day, so cache generously: headlines do not change
# minute to minute and the page polls.
NEWS_TTL = 900.0

# Documented free-tier ceiling per request.
PAGE_LIMIT = 3

BURST_PAUSE = 1.0

_call_lock = threading.Lock()
_last_call_at = 0.0

last_status: Optional[str] = None

# When the daily quota is gone, stop asking until it resets. Every further
# call in the meantime is another failed request against the same quota and
# a slow blank screen.
_blocked_until = 0.0


def _next_utc_midnight() -> float:
    from datetime import timedelta, timezone

    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=5, second=0,
                                             microsecond=0).timestamp()


def blocked() -> bool:
    # NEWS_PROVIDER=yahoo in .env turns Marketaux off entirely.
    if os.environ.get("NEWS_PROVIDER", "").strip().lower() == "yahoo":
        return True
    return time.time() < _blocked_until


def _note_failure(exc: "cfg.ProviderError") -> None:
    global _blocked_until
    text = str(exc)
    if exc.status == cfg.RATE_LIMITED or "402" in text or "429" in text:
        _blocked_until = _next_utc_midnight()


def configured() -> bool:
    return cfg.MARKETAUX.configured


def _throttle() -> None:
    global _last_call_at

    with _call_lock:
        wait = BURST_PAUSE - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def _call(path: str, params: dict) -> dict:
    global last_status

    if blocked():
        raise cfg.ProviderError(cfg.RATE_LIMITED,
                                "Marketaux daily quota used up; resets at midnight UTC.")
    _throttle()
    try:
        return _call_inner(path, params)
    except cfg.ProviderError as exc:
        _note_failure(exc)
        raise


def _call_inner(path: str, params: dict) -> dict:
    global last_status

    payload = cfg.fetch_json(f"{cfg.MARKETAUX.base_url}/{path}", {
        **params,
        "api_token": cfg.MARKETAUX.api_key,
    })

    if not isinstance(payload, dict):
        last_status = cfg.DATA_UNAVAILABLE
        raise cfg.ProviderError(cfg.DATA_UNAVAILABLE, "Unexpected payload.")

    # Errors arrive as a 200 with an `error` object.
    error = payload.get("error")
    if error:
        message = cfg.redact(
            (error or {}).get("message") if isinstance(error, dict) else error)
        code = str((error or {}).get("code", "")) if isinstance(error, dict) else ""
        status = (cfg.RATE_LIMITED if "limit" in f"{code}{message}".lower()
                  else cfg.ENTITLEMENT_REQUIRED if "token" in f"{code}".lower()
                  else cfg.DATA_UNAVAILABLE)
        last_status = status
        raise cfg.ProviderError(status, str(message)[:400])

    last_status = cfg.OK
    return payload


def _local_time(stamp: Optional[str]) -> Optional[str]:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(
            stamp.replace("Z", "+00:00")).astimezone().strftime("%b %d, %H:%M")
    except (ValueError, TypeError):
        return stamp


def _entity_for(article: dict, symbol: str) -> dict:
    """The entity block for `symbol`, or {} if the article only mentions it."""
    for entity in article.get("entities") or []:
        if str(entity.get("symbol") or "").upper() == symbol:
            return entity
    return {}


def get_news(symbol: str, limit: int = PAGE_LIMIT) -> dict:
    """Headlines for one symbol, shaped like ``ibkr_news_service.get_news``."""
    symbol = symbol.upper()
    key = f"mx:news:{symbol}"
    cached = cache.get(key, NEWS_TTL)
    if cached:
        return cached

    if not configured() or blocked():
        return _fallback(symbol, limit)

    try:
        payload = _call("news/all", {
            "symbols": symbol,
            # Without this a passing mention counts as coverage.
            "filter_entities": "true",
            "language": "en",
            "limit": min(limit, PAGE_LIMIT),
        })
    except cfg.ProviderError:
        return _fallback(symbol, limit)

    items = []
    for article in payload.get("data") or []:
        entity = _entity_for(article, symbol)
        items.append({
            "article_id": article.get("uuid"),
            "provider_code": article.get("source"),
            "provider": article.get("source"),
            "time": _local_time(article.get("published_at")),
            "published_at": article.get("published_at"),
            "headline": article.get("title"),
            "summary": article.get("description") or article.get("snippet"),
            "url": article.get("url"),
            "image_url": article.get("image_url"),
            "sentiment_score": num(entity.get("sentiment_score")),
            "match_score": num(entity.get("match_score")),
            "industry": entity.get("industry"),
            "source": SOURCE,
        })

    result = {
        "symbol": symbol,
        "items": items,
        "count": len(items),
        "total_available": (payload.get("meta") or {}).get("found"),
        "status": "OK" if items else "DATA_UNAVAILABLE",
        "detail": None if items else f"No recent English coverage for {symbol}.",
        "source": SOURCE,
    }
    cache.put(key, result)
    return result


def _fallback(symbol: str, limit: int) -> dict:
    """Yahoo headlines when Marketaux cannot answer."""
    import yahoo_news_service as yahoo

    return yahoo.get_news(symbol, max(limit, 10))


def get_sentiment(symbol: str, limit: int = PAGE_LIMIT) -> dict:
    """
    Aggregate sentiment, shaped like ``ibkr_news_service.get_sentiment``.

    Scored 0-100 from the provider's per-entity scores so the existing gauge
    reads correctly: -1 maps to 0, 0 to 50, +1 to 100.
    """
    symbol = symbol.upper()
    news = get_news(symbol, limit)

    if news.get("status") != "OK" or not news.get("items"):
        return {
            "symbol": symbol,
            "score": None,
            "label": "UNAVAILABLE",
            "status": news.get("status", "DATA_UNAVAILABLE"),
            "detail": news.get("detail"),
            "items": [],
            "source": news.get("source") or SOURCE,
        }

    per_item = []
    scored = []
    for item in news["items"]:
        value = item.get("sentiment_score")
        tone = "Neutral"
        if value is not None:
            scored.append(value)
            tone = ("Positive" if value > 0.15
                    else "Negative" if value < -0.15 else "Neutral")
        per_item.append({
            "headline": item["headline"],
            "time": item["time"],
            "url": item.get("url"),
            "sentiment_score": value,
            "tone": tone,
        })

    if not scored:
        return {
            "symbol": symbol,
            "score": None,
            "label": "NEUTRAL",
            "headlines_scored": 0,
            "headlines_total": len(news["items"]),
            "items": per_item,
            "status": "INSUFFICIENT_DATA",
            "detail": "The provider returned no sentiment score for these articles",
            "source": SOURCE,
        }

    average = sum(scored) / len(scored)
    score = round((average + 1) / 2 * 100)
    label = ("BULLISH" if score >= 60
             else "BEARISH" if score <= 40 else "NEUTRAL")

    return {
        "symbol": symbol,
        "score": score,
        "label": label,
        "average_sentiment": round(average, 4),
        "headlines_scored": len(scored),
        "headlines_total": len(news["items"]),
        "items": per_item,
        "status": "OK",
        "detail": (
            news.get("detail") if news.get("source") != SOURCE else
            "Sentiment is Marketaux's own per-article model, not a reading of "
            "the market."
        ),
        "source": news.get("source") or SOURCE,
    }


def provider_status() -> dict:
    base = cfg.MARKETAUX.status()
    return cfg.apply_last_fetch(base, last_status)
