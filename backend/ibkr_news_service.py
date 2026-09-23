"""
News from IBKR's own providers.

This account carries historical-news entitlement (Dow Jones, Briefing.com and
friends) for both headlines and article bodies. The *streaming* bulletin feed
is not entitled and returns error 10276 - that is an entitlement fact, not a
bug, so it is reported as such rather than retried.

No other source is used. Nothing is scraped.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Optional

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB, Stock

from ibkr_client import IBKRUnavailable, ibkr
from live_market_service import EASTERN, cache

PROVIDER_TTL = 3600.0
NEWS_TTL = 180.0

# Dow Jones prefixes each headline with a routing code in braces.
_HEADLINE_CODE = re.compile(r"^\{[^}]*\}")
_TAG = re.compile(r"<[^>]+>")


def _clean_headline(text: str) -> str:
    return html.unescape(_HEADLINE_CODE.sub("", text or "").strip())


def _clean_body(text: str) -> str:
    """IBKR returns article bodies as fragmentary HTML; flatten to plain text."""
    if not text:
        return ""
    body = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    body = re.sub(r"</p\s*>", "\n\n", body, flags=re.I)
    body = _TAG.sub("", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def get_providers() -> list[dict]:
    cached = cache.get("news:providers", PROVIDER_TTL)
    if cached is not None:
        return cached

    async def job(ib: IB):
        return await ib.reqNewsProvidersAsync()

    try:
        provs = ibkr.run(job, timeout=30)
    except IBKRUnavailable:
        return []

    out = [{"code": p.code, "name": p.name} for p in provs]
    cache.put("news:providers", out)
    return out


def provider_status() -> dict:
    """
    Health entry for the provider matrix.

    Headlines and article bodies are entitled on this account; the live
    streaming feed is not. Both facts are reported so the UI can offer what
    works without implying the rest is broken.
    """
    try:
        provs = get_providers()
    except Exception as exc:  # noqa: BLE001
        return {"status": "PROVIDER_OFFLINE", "detail": str(exc)}

    if not provs:
        return {
            "status": "ENTITLEMENT_REQUIRED",
            "detail": (
                "No news providers returned for this account. IBKR news "
                "entitlement is required."
            ),
            "providers": [],
        }

    return {
        "status": "OK",
        "detail": f"{len(provs)} providers entitled (historical headlines + articles)",
        "providers": provs,
        "note": (
            "Streaming news bulletins are not entitled on this account "
            "(IBKR error 10276); headlines and article bodies are."
        ),
    }


def _to_local(value) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            try:
                value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(EASTERN).isoformat()


def get_symbol_news(symbol: str, limit: int = 20) -> dict:
    symbol = symbol.upper()
    key = f"news:{symbol}:{limit}"
    cached = cache.get(key, NEWS_TTL)
    if cached:
        return cached

    providers = get_providers()
    if not providers:
        return {
            "symbol": symbol,
            "items": [],
            "status": "ENTITLEMENT_REQUIRED",
            "detail": "IBKR news entitlement required",
            "source": "IBKR",
        }

    codes = "+".join(p["code"] for p in providers)

    async def job(ib: IB):
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            return None
        return await ib.reqHistoricalNewsAsync(
            contract.conId, codes, "", "", min(limit, 50)
        )

    try:
        headlines = ibkr.run(job, timeout=60)
    except IBKRUnavailable as exc:
        return {"symbol": symbol, "items": [], "status": "PROVIDER_OFFLINE",
                "detail": str(exc), "source": "IBKR"}

    if headlines is None:
        return {"symbol": symbol, "items": [], "status": "UNKNOWN_SYMBOL",
                "source": "IBKR"}

    names = {p["code"]: p["name"] for p in providers}
    items = [
        {
            "article_id": h.articleId,
            "provider_code": h.providerCode,
            "provider": names.get(h.providerCode, h.providerCode),
            "time": _to_local(h.time),
            "headline": _clean_headline(h.headline),
        }
        for h in headlines
    ]

    result = {
        "symbol": symbol,
        "items": items,
        "count": len(items),
        "status": "OK" if items else "DATA_UNAVAILABLE",
        "source": "IBKR",
    }
    cache.put(key, result)
    return result


def get_article(provider_code: str, article_id: str) -> dict:
    key = f"news:article:{provider_code}:{article_id}"
    cached = cache.get(key, 3600.0)
    if cached:
        return cached

    async def job(ib: IB):
        return await ib.reqNewsArticleAsync(provider_code, article_id)

    try:
        article = ibkr.run(job, timeout=45)
    except IBKRUnavailable as exc:
        return {"status": "PROVIDER_OFFLINE", "detail": str(exc), "text": None}

    text = _clean_body(getattr(article, "articleText", "") or "")
    result = {
        "article_id": article_id,
        "provider_code": provider_code,
        "text": text or None,
        "article_type": getattr(article, "articleType", None),
        "status": "OK" if text else "DATA_UNAVAILABLE",
        "source": "IBKR",
    }
    cache.put(key, result)
    return result


# ---------------------------------------------------------------------------
# sentiment
# ---------------------------------------------------------------------------

POSITIVE = {
    "beat", "beats", "surge", "surges", "rally", "rallies", "gain", "gains",
    "record", "upgrade", "upgraded", "outperform", "strong", "growth", "raises",
    "raised", "jump", "jumps", "soar", "soars", "wins", "expands", "profit",
    "bullish", "top", "tops", "boost", "boosts", "higher",
}
NEGATIVE = {
    "miss", "misses", "fall", "falls", "drop", "drops", "plunge", "plunges",
    "downgrade", "downgraded", "underperform", "weak", "cuts", "cut", "slump",
    "loss", "losses", "lawsuit", "probe", "investigation", "warns", "warning",
    "bearish", "decline", "declines", "slides", "lower", "halts", "recall",
}


def get_sentiment(symbol: str, limit: int = 20) -> dict:
    """
    Lexicon sentiment over entitled headline text only.

    V1 deliberately reads headlines rather than full articles: it is the text
    we are certain of for every item, and it keeps the calculation explainable.
    If no headlines are entitled the answer is UNAVAILABLE, never a guess.
    """
    news = get_symbol_news(symbol, limit)
    if news.get("status") != "OK" or not news.get("items"):
        return {
            "symbol": symbol.upper(),
            "score": None,
            "label": "UNAVAILABLE",
            "status": news.get("status", "DATA_UNAVAILABLE"),
            "detail": news.get("detail"),
            "source": "IBKR",
        }

    pos = neg = scored = 0
    per_item = []
    for item in news["items"]:
        words = re.findall(r"[a-z]+", item["headline"].lower())
        p = sum(1 for w in words if w in POSITIVE)
        n = sum(1 for w in words if w in NEGATIVE)
        if p or n:
            scored += 1
            pos += p
            neg += n
        per_item.append({
            "headline": item["headline"], "time": item["time"],
            "positive": p, "negative": n,
            "tone": "Positive" if p > n else "Negative" if n > p else "Neutral",
        })

    total = pos + neg
    if not total:
        return {
            "symbol": symbol.upper(),
            "score": None,
            "label": "NEUTRAL",
            "headlines_scored": 0,
            "headlines_total": len(news["items"]),
            "items": per_item,
            "status": "INSUFFICIENT_DATA",
            "detail": "No sentiment-bearing terms in the entitled headlines",
            "source": "IBKR",
        }

    score = round(pos / total * 100)
    return {
        "symbol": symbol.upper(),
        "score": score,
        "label": "BULLISH" if score >= 60 else "BEARISH" if score <= 40 else "NEUTRAL",
        "positive_terms": pos,
        "negative_terms": neg,
        "headlines_scored": scored,
        "headlines_total": len(news["items"]),
        "items": per_item,
        "method": "Lexicon over entitled IBKR headline text",
        "status": "OK",
        "source": "IBKR",
    }
