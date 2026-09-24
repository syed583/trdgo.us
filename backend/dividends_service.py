"""
Dividends: what the company pays, when, and whether it is growing.

Nasdaq publishes the full declared history per symbol for free -- amount,
declaration, ex-date, record and payment dates -- which is more than the
Benzinga calendar returns for this account (it had nothing for NVDA, which
has paid every quarter). So Nasdaq is the source and Benzinga stays as the
fallback for anything Nasdaq does not answer.

The one number worth deriving is the direction of travel: four quarters
against the four before them says whether the payout is rising, flat or being
cut, which is the part a holder actually reads.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime
from typing import Any, Optional

from live_market_service import cache

SOURCE = "NASDAQ"


def _from_unusual_whales(symbol: str, limit: int):
    """The same panel, from the paid feed, or None if it cannot answer."""
    try:
        import uw_company_service as uwc

        out = uwc.dividends(symbol, limit)
    except Exception:  # noqa: BLE001 - Nasdaq still gets its turn
        return None
    if out.get("status") != "OK" or not out.get("payments"):
        return None

    payments = out.get("all_payments") or out["payments"]
    cash = [p for p in payments if str(p["type"]).lower().startswith("cash")]
    recent = sum(p["amount"] for p in cash[:4]) if len(cash) >= 4 else None
    prior = sum(p["amount"] for p in cash[4:8]) if len(cash) >= 8 else None
    growth = (round((recent / prior - 1) * 100, 1)
              if recent and prior and prior > 0 else None)
    today = date.today().isoformat()
    upcoming = [p for p in payments if (p["ex_date"] or "") >= today]

    return {
        "symbol": symbol,
        "status": "OK",
        "pays_dividend": True,
        "payments": payments[:limit],
        "count": len(payments),
        "latest": payments[0],
        "next_ex_date": upcoming[-1]["ex_date"] if upcoming else None,
        "next_pay_date": upcoming[-1]["pay_date"] if upcoming else None,
        # Not published per-symbol by this feed; the trailing twelve months
        # is the honest substitute and is labelled as that rather than
        # presented as a forward annual rate.
        "annual_dividend": round(recent, 4) if recent else None,
        "dividend_yield_pct": None,
        "ttm_total": round(recent, 4) if recent else None,
        "growth_pct": growth,
        "trend": ("rising" if growth is not None and growth > 1
                  else "cut" if growth is not None and growth < -1
                  else "flat" if growth is not None else None),
        "detail": ("Declared dividends as filed. Growth compares the last "
                   "four cash payments with the four before them; the annual "
                   "figure is the trailing twelve months, not a forward rate."),
        "source": out["source"],
    }
URL = "https://api.nasdaq.com/api/quote/{symbol}/dividends?assetclass=stocks"
TTL = 12 * 3600.0
FAIL_TTL = 600.0
TIMEOUT = 15.0
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _date(value: Any) -> Optional[str]:
    """Nasdaq writes MM/DD/YYYY; everything else here is ISO."""
    try:
        return datetime.strptime(str(value).strip(), "%m/%d/%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def _amount(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _percent(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _header(rows: list, label: str) -> Any:
    for row in rows or []:
        if str(row.get("label", "")).lower().startswith(label.lower()):
            return row.get("value")
    return None


def get_dividends(symbol: str, limit: int = 8) -> dict:
    """
    Declared dividends for one symbol, newest first.

    Unusual Whales leads: it is a keyed feed with a contract behind it, where
    Nasdaq's is a public page this app scrapes and which breaks whenever
    their markup changes. Nasdaq stays as the fallback.
    """
    # Interpolated into a Nasdaq URL and a cache key, so it is validated
    # before either. A malformed symbol is not a dividend query.
    import input_validation as validate

    symbol = (symbol or "").upper().strip()
    if not validate.is_symbol(symbol):
        return {"symbol": symbol, "status": "INVALID", "payments": [],
                "detail": "Not a valid symbol.", "source": SOURCE}
    limit = validate.clamp_int(limit, low=1, high=50, default=8)

    provider = _from_unusual_whales(symbol, limit)
    if provider:
        return provider
    key = f"div:{symbol}"
    hit = cache.get(key, TTL)
    if hit:
        return {**hit, "payments": hit["payments"][:limit]}
    miss = cache.get(key + ":fail", FAIL_TTL)
    if miss:
        return miss

    try:
        request = urllib.request.Request(URL.format(symbol=symbol), headers=HEADERS)
        payload = json.load(urllib.request.urlopen(request, timeout=TIMEOUT))
    except Exception as exc:  # noqa: BLE001
        result = {"symbol": symbol, "status": "PROVIDER_OFFLINE", "payments": [],
                  "detail": f"Dividend feed unavailable: {type(exc).__name__}",
                  "source": SOURCE}
        cache.put(key + ":fail", result)
        return result

    data = payload.get("data") or {}
    headers = data.get("dividendHeaderValues") or []
    rows = ((data.get("dividends") or {}).get("rows")) or []

    payments = []
    for row in rows:
        amount = _amount(row.get("amount"))
        ex = _date(row.get("exOrEffDate"))
        if amount is None or not ex:
            continue
        payments.append({
            "amount": amount,
            "type": row.get("type") or "Cash",
            "declared": _date(row.get("declarationDate")),
            "ex_date": ex,
            "record_date": _date(row.get("recordDate")),
            "pay_date": _date(row.get("paymentDate")),
            "currency": row.get("currency") or "USD",
        })
    payments.sort(key=lambda p: p["ex_date"], reverse=True)

    cash = [p for p in payments if str(p["type"]).lower().startswith("cash")]
    recent = sum(p["amount"] for p in cash[:4]) if len(cash) >= 4 else None
    prior = sum(p["amount"] for p in cash[4:8]) if len(cash) >= 8 else None
    growth = (round((recent / prior - 1) * 100, 1)
              if recent and prior and prior > 0 else None)

    today = date.today().isoformat()
    upcoming = [p for p in payments if p["ex_date"] >= today]

    if not payments:
        result = {"symbol": symbol, "status": "NO_DATA", "payments": [],
                  "pays_dividend": False,
                  "detail": f"{symbol} has no declared dividends on record.",
                  "source": SOURCE}
        cache.put(key, {**result, "payments": []})
        return result

    result = {
        "symbol": symbol,
        "status": "OK",
        "pays_dividend": True,
        "payments": payments,
        "count": len(payments),
        "latest": payments[0],
        "next_ex_date": upcoming[-1]["ex_date"] if upcoming else None,
        "next_pay_date": upcoming[-1]["pay_date"] if upcoming else None,
        "annual_dividend": _amount(_header(headers, "Annual Dividend")),
        "dividend_yield_pct": _percent(_header(headers, "Dividend Yield")),
        "ttm_total": round(recent, 4) if recent else None,
        "growth_pct": growth,
        "trend": ("rising" if growth is not None and growth > 1
                  else "cut" if growth is not None and growth < -1
                  else "flat" if growth is not None else None),
        "detail": (
            "Declared dividends as published by Nasdaq. Growth compares the "
            "last four cash payments with the four before them."
        ),
        "source": SOURCE,
    }
    cache.put(key, result)
    return {**result, "payments": payments[:limit]}
