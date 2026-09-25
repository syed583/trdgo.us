"""
Who owns the company and who is trading it, from Unusual Whales.

What this replaces, and why it is worth replacing
-------------------------------------------------
**Institutional ownership** was built by downloading the SEC's quarterly 13F
bulk datasets -- roughly 1.3 GB a quarter -- parsing two million positions,
inverting them by CUSIP and storing the result, because no per-ticker 13F
feed existed. It worked, but it meant a quarter's data landed weeks late, the
comparison only ever held two quarters, five of the largest names in the
market could not be matched to their CUSIP at all, and every ticker asked
about had to be reconciled by company name against filer spellings.

**Insider activity** was parsed out of Form 4 documents one at a time: a
large issuer files hundreds a year, each a separate fetch, so the window was
capped at sixty filings and the rest silently dropped.

Both now come per ticker, already reconciled, with the filing dates attached.
The SEC remains the record of what was filed -- this is a faster path to the
same filings, not a replacement for them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import unusualwhales_service as uw

SOURCE = "Unusual Whales"

STALENESS_NOTE = (
    "13F is filed up to 45 days after quarter end. This is a slow "
    "confirmation signal, not a trading trigger.")

# Codes as the SEC defines them, kept so the rest of the app's insider
# vocabulary is unchanged.
PURCHASE, SALE = "P", "S"
CODE_LABELS = {
    "P": "Open-market purchase", "S": "Open-market sale",
    "A": "Grant or award", "M": "Option exercise", "F": "Tax withholding",
    "G": "Gift", "C": "Conversion", "X": "Option exercise",
}
DISCRETIONARY = ("P", "S")


def configured() -> bool:
    return uw.configured()


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# institutional ownership
# ---------------------------------------------------------------------------


def institutional_activity(symbol: str, top: int = 10) -> dict:
    """
    Who holds this stock, and how that changed since their last filing.

    Their rows carry the current position, the change against the previous
    report and a run of historical unit counts, so the quarter-over-quarter
    comparison is read rather than reconstructed from two bulk downloads.
    """
    symbol = (symbol or "").upper().strip()
    limit = 500
    out = uw.institutional_ownership(symbol, limit=limit)
    if out["status"] != "OK":
        return {"ticker": symbol, "status": out["status"],
                "detail": out.get("detail"), "source": SOURCE}

    rows = uw._rows(out)
    if not rows:
        return {"ticker": symbol, "status": "NO_DATA",
                "detail": f"No institutional holders on record for {symbol}.",
                "source": SOURCE}

    holders, buyers, sellers = [], [], []
    now = before = 0.0
    increasing = decreasing = unchanged = new = closed = 0

    for r in rows:
        units = _f(r.get("units")) or 0.0
        changed = _f(r.get("units_changed")) or 0.0
        previous = units - changed
        now += units
        before += max(previous, 0.0)

        if changed > 0:
            increasing += 1
        elif changed < 0:
            decreasing += 1
        else:
            unchanged += 1
        if previous <= 0 and units > 0:
            new += 1
        if units <= 0 and previous > 0:
            closed += 1

        holder = {
            "fund": r.get("name"),
            "cik": r.get("cik"),
            "state": ("NEW POSITION" if previous <= 0 < units
                      else "CLOSED" if units <= 0 < previous
                      else "INCREASED" if changed > 0
                      else "REDUCED" if changed < 0 else "UNCHANGED"),
            "shares": units,
            "previous_shares": max(previous, 0.0),
            "share_change": changed,
            "value": _f(r.get("value")),
            "avg_price": _f(r.get("avg_price")),
            "filed": r.get("filing_date"),
            "report_date": r.get("report_date"),
        }
        holders.append(holder)
        if changed > 0:
            buyers.append(holder)
        elif changed < 0:
            sellers.append(holder)

    buyers.sort(key=lambda h: -(h["share_change"] or 0))
    sellers.sort(key=lambda h: (h["share_change"] or 0))
    change = now - before
    change_pct = round(change / before * 100, 2) if before else None

    report = max((r.get("report_date") or "" for r in rows), default="")
    # Their list is the largest holders, not every filer. Reporting it as
    # "total funds" would quietly turn a top-500 sample into a census: NVDA
    # has 5,960 filers and this would have said 500.
    capped = len(rows) >= limit
    return {
        "ticker": symbol,
        "cusip": None,
        "holders_listed": len(rows),
        "holders_capped": capped,
        "signal": _signal(change_pct, increasing, decreasing),
        "score": _score(change_pct, increasing, decreasing),
        "latest_quarter": _quarter(report),
        "previous_quarter": _quarter(report, back=1),
        "report_date": report or None,
        "total_funds": None if capped else len(rows),
        "funds_increasing": increasing,
        "funds_decreasing": decreasing,
        "funds_unchanged": unchanged,
        "new_positions": new,
        "closed_positions": closed,
        "total_shares_current": round(now, 0),
        "total_shares_previous": round(before, 0),
        "net_share_change": round(change, 0),
        "net_share_change_pct": change_pct,
        "top_buyers": buyers[:top],
        "top_sellers": sellers[:top],
        "holders": holders[:top],
        "family_transfers": [],
        "staleness_note": STALENESS_NOTE,
        # 13F holdings are a quarterly filing; the badge names the quarter so
        # nobody reads a three-month-old census as current.
        "freshness": __import__("freshness").for_quarterly(_quarter(report)),
        "detail": ((f"The {len(rows)} largest holders on file, not every "
                    "filer -- the share totals below are theirs alone.")
                   if capped else
                   f"All {len(rows)} institutional holders on file."),
        "status": "OK",
        "source": SOURCE,
    }


def _quarter(report_date: str, back: int = 0) -> Optional[str]:
    """'2026-06-30' -> '2026-Q2', or the quarter ``back`` before it."""
    try:
        when = datetime.strptime(str(report_date)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    quarter = (when.month - 1) // 3 + 1 - back
    year = when.year
    while quarter < 1:
        quarter += 4
        year -= 1
    return f"{year}-Q{quarter}"


def _signal(change_pct: Optional[float], up: int, down: int) -> str:
    """
    Which way institutions moved, on the two readings that disagree least.

    Share count and holder count can point opposite ways -- one large seller
    against many small buyers -- and when they do, the honest answer is
    neutral rather than whichever number is larger.
    """
    if change_pct is None:
        return "neutral"
    by_shares = 1 if change_pct > 1.5 else -1 if change_pct < -1.5 else 0
    by_holders = 1 if up > down * 1.2 else -1 if down > up * 1.2 else 0
    if by_shares > 0 and by_holders >= 0:
        return "bullish"
    if by_shares < 0 and by_holders <= 0:
        return "bearish"
    return "neutral"


def _score(change_pct: Optional[float], up: int, down: int) -> float:
    if change_pct is None:
        return 0.0
    return round(max(-5.0, min(5.0, change_pct / 2.0)), 1)


# ---------------------------------------------------------------------------
# insider activity
# ---------------------------------------------------------------------------


def insider_transactions(symbol: str, days: int = 180) -> dict:
    """
    Form 4 activity for one symbol, in the shape the model already reads.

    Every filing in the window arrives in one request, so the sixty-filing
    cap the document-by-document path needed is gone -- and with it the
    silent truncation of the oldest trades on a heavily-filing issuer.
    """
    symbol = (symbol or "").upper().strip()
    since = (date.today() - timedelta(days=days)).isoformat()
    out = uw.get("/api/insider/transactions",
                 {"ticker_symbol": symbol, "limit": 500})
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"],
                "detail": out.get("detail"), "transactions": [],
                "source": SOURCE}

    transactions = []
    for r in uw._rows(out):
        filed = str(r.get("filing_date") or "")[:10]
        when = str(r.get("transaction_date") or filed)[:10]
        if when < since:
            continue
        code = str(r.get("transaction_code") or "").upper()[:1]
        # Their amount is signed: negative is shares leaving the insider.
        amount = _f(r.get("amount")) or 0.0
        shares = abs(amount)
        price = _f(r.get("price"))
        planned = bool(r.get("is_10b5_1"))
        is_director = bool(r.get("is_director"))
        is_officer = bool(r.get("is_officer"))
        is_ten_pct = bool(r.get("is_ten_percent_owner"))
        transactions.append({
            "filed": filed,
            "date": when,
            "owner": r.get("owner_name"),
            "role": ("10% owner" if is_ten_pct
                     else "Director" if is_director
                     else "Officer" if is_officer else "Insider"),
            "is_director": is_director,
            "is_officer": is_officer,
            "is_ten_percent_owner": is_ten_pct,
            "code": code,
            "code_label": CODE_LABELS.get(code, code),
            # Discretionary means a decision: an open-market trade that is
            # not running off a pre-adopted plan.
            "discretionary": code in DISCRETIONARY and not planned,
            "planned_10b5_1": planned,
            "direction": ("Buy" if code == PURCHASE
                          else "Sell" if code == SALE else None),
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2) if price else None,
            "shares_held_after": None,
            "acquired_disposed": "A" if amount > 0 else "D",
            "security": r.get("security_title"),
            "url": None,
        })

    transactions.sort(key=lambda t: t["date"], reverse=True)

    import sec_filings_service as sec_filings

    return {
        "symbol": symbol,
        "window_days": days,
        "filings_read": len(transactions),
        "transactions": transactions,
        **sec_filings.summarise_insiders(transactions),
        "status": "OK" if transactions else "NO_FILINGS",
        "source": SOURCE,
    }


def insider_transactions_preferred(symbol: str, days: int = 180) -> dict:
    """
    Form 4 activity from Unusual Whales, or SEC's own filings as the fallback.

    UW returns the same shape this app already reads and needs no local filing
    store, so it leads; SEC EDGAR stands in when the feed is unconfigured or
    has no coverage for a name. Both the insider panel and the directional
    model's insider parameter go through here so they never disagree on source.
    """
    if configured():
        out = insider_transactions(symbol, days=days)
        if out.get("status") == "OK":
            return out
    import sec_filings_service as sec

    return sec.insider_transactions(symbol, days=days)


# Transaction-code labels, in the words a filing uses.
_TXN_LABELS = {
    "P": "Open Market Purchase", "S": "Open Market Sale",
    "A": "Grant / Award", "M": "Option Exercise", "X": "Option Exercise",
    "F": "Tax Withholding", "G": "Gift", "C": "Conversion",
    "D": "Disposition to Issuer", "W": "Acquisition/Disposition by Will",
}


def market_insider_transactions(limit: int = 100, buys_only: bool = False) -> dict:
    """
    The largest insider transactions across the whole market.

    One row per filing: who, what ticker, buy or sell, how many shares, at what
    price, for how much. Sorted by dollar size so the ones worth seeing lead.
    Purchases and sales are both returned; the caller colours them.
    """
    out = uw.get("/api/insider/transactions", {"limit": 500})
    if out["status"] != "OK":
        return {"status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out):
        code = str(r.get("transaction_code") or "").upper()
        shares = abs(_f(r.get("amount")) or 0.0)
        price = _f(r.get("price"))
        if not shares or price is None:
            continue
        is_buy = code == "P"
        is_sell = code == "S"
        if buys_only and not is_buy:
            continue
        role = ("10% Owner" if r.get("is_ten_percent_owner")
                else r.get("officer_title") if r.get("is_officer")
                else "Director" if r.get("is_director") else "Insider")
        rows.append({
            "ticker": r.get("ticker"),
            "date": str(r.get("transaction_date") or r.get("filing_date") or "")[:10],
            "filed": str(r.get("filing_date") or "")[:10],
            "name": r.get("owner_name"),
            "code": code,
            "type": _TXN_LABELS.get(code, code or "--"),
            "direction": "buy" if is_buy else "sell" if is_sell else "neutral",
            "shares": shares,
            "price": round(price, 2),
            "value": round(shares * price, 2),
            "role": role,
            "sector": r.get("sector"),
            "planned": bool(r.get("is_10b5_1")),
        })

    rows.sort(key=lambda x: x["value"], reverse=True)
    rows = rows[:max(1, min(limit, 300))]
    return {
        "status": "OK" if rows else "NO_DATA",
        "rows": rows,
        "count": len(rows),
        "detail": ("The largest insider transactions filed across the market, "
                   "by dollar value. Purchases are the signal; sales run on "
                   "schedules and taxes as often as conviction."),
        "source": SOURCE,
    }
