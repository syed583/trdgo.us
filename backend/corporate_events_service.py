"""
What the company itself told the SEC happened.

A price move often has a cause that is neither price nor options: the CEO
left, a division was sold, a merger agreement was signed, results came out.
Companies must report these on an 8-K within four business days, and EDGAR
tags each one with the item numbers it covers -- so "5.02" is the company
saying a director or officer came or went, and "2.01" is an acquisition or
disposal completing.

That tagging is the whole value here. It is the company's own classification,
free, and available for every US issuer, so the events on screen are neither
guessed from headlines nor bought from a vendor. What it does not give is the
detail: the item says a CEO changed, not who. Each event therefore links to
the filing, and nothing here is presented as more than the tag says.

Merger paperwork also arrives as its own forms -- S-4, 425, DEFM14A, SC 14D9 --
which are included for the same reason.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import sec_filings_service as sec
from live_market_service import cache

SOURCE = "SEC EDGAR"
TTL = 6 * 3600.0
DEFAULT_DAYS = 365

# EDGAR's 8-K item numbers, in the words a reader would use. Only the ones
# worth surfacing: the routine housekeeping items are left out rather than
# padding the feed.
ITEMS: dict[str, tuple[str, str, str]] = {
    # code: (category, headline, what it means)
    "5.02": ("leadership", "Management change",
             "A director or senior officer was appointed, departed or retired."),
    "5.01": ("leadership", "Change of control",
             "Control of the company changed hands."),
    "5.03": ("governance", "Charter or bylaws amended",
             "The company changed its charter, bylaws or fiscal year."),
    "5.07": ("governance", "Shareholder vote",
             "Results of a shareholder meeting."),
    "1.01": ("deal", "Material agreement signed",
             "A significant agreement was entered into -- often a merger, "
             "partnership or major contract."),
    "1.02": ("deal", "Material agreement ended",
             "A significant agreement was terminated."),
    "2.01": ("deal", "Acquisition or disposal completed",
             "The company completed a purchase or sale of a business or assets."),
    "1.03": ("distress", "Bankruptcy or receivership",
             "The company entered bankruptcy or receivership."),
    "2.02": ("earnings", "Results released",
             "Quarterly or annual results were published."),
    "2.03": ("funding", "New debt or obligation",
             "The company took on debt or another direct financial obligation."),
    "2.04": ("funding", "Debt acceleration",
             "An obligation became payable early, often a covenant breach."),
    "2.05": ("restructuring", "Restructuring costs",
             "The board committed to an exit or disposal plan -- restructuring, "
             "plant closures or layoffs."),
    "2.06": ("restructuring", "Asset write-down",
             "A material impairment of assets was recognised."),
    "3.01": ("listing", "Listing rule notice",
             "A listing standard was not met, or delisting was notified."),
    "3.02": ("funding", "Shares sold privately",
             "Equity was sold without registration, which dilutes holders."),
    "3.03": ("governance", "Holder rights changed",
             "The rights of security holders were modified."),
    "4.01": ("governance", "Auditor changed",
             "The company changed its accounting firm."),
    "4.02": ("distress", "Accounts cannot be relied on",
             "Previously issued financial statements were withdrawn."),
    "7.01": ("disclosure", "Company statement",
             "Information the company chose to furnish, often a presentation."),
    "8.01": ("disclosure", "Other event",
             "Something the company judged material that fits no other item."),
}

# Merger and tender paperwork, which arrives as its own form rather than an
# 8-K item.
DEAL_FORMS = {
    "S-4": "Merger registration filed",
    "425": "Merger communication",
    "DEFM14A": "Merger proxy sent to shareholders",
    "PREM14A": "Merger proxy (preliminary)",
    "SC 14D9": "Response to a takeover bid",
    "SC TO-T": "Takeover bid for this company",
    "SC TO-I": "The company is buying back stock by tender",
}

# Which categories move a share price most when they land.
WEIGHT = {"dividend": "low",
          "deal": "high", "leadership": "high", "distress": "high",
          "restructuring": "medium", "earnings": "medium", "funding": "medium",
          "listing": "medium", "governance": "low", "disclosure": "low"}

CATEGORY_LABEL = {
    "dividend": "Dividends",
    "deal": "Mergers & deals", "leadership": "Management",
    "distress": "Distress", "restructuring": "Restructuring",
    "earnings": "Earnings", "funding": "Funding & debt",
    "listing": "Listing", "governance": "Governance",
    "disclosure": "Company statements",
}


def _url(cik: Optional[str], accession: str, document: str) -> Optional[str]:
    if not cik or not accession:
        return None
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}"
    return f"{base}/{document}" if document else base


def _age(filed: str) -> Optional[int]:
    try:
        return (date.today() - datetime.fromisoformat(filed).date()).days
    except (TypeError, ValueError):
        return None


def _shipped(result: dict, limit: int) -> dict:
    """
    The payload trimmed to what is actually sent, counts included.

    The tab strip is built from these counts, so counting the whole history
    while sending a slice of it puts a number on a tab that nothing behind it
    can fill -- the tab opens on an empty list. The full-window total stays,
    named as what it is.
    """
    events = result["events"][:limit]
    counts: dict[str, int] = {}
    for e in events:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    return {**result, "events": events, "count": len(events),
            "counts_by_category": counts,
            "total_in_window": len(result["events"])}


def get_events(symbol: str, days: int = DEFAULT_DAYS, limit: int = 40) -> dict:
    """Corporate events this company reported to the SEC, newest first."""
    symbol = (symbol or "").upper().strip()
    key = f"corpevents:{symbol}:{days}"
    hit = cache.get(key, TTL)
    if hit:
        return _shipped(hit, limit)

    payload = sec._submissions(symbol)
    if not payload:
        return {"symbol": symbol, "status": "NO_DATA", "events": [],
                "detail": "No EDGAR submissions for this symbol.", "source": SOURCE}

    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    items = recent.get("items") or []
    filed_on = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    cik = sec._cik(symbol)
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    def at(seq: list, i: int) -> str:
        return seq[i] if i < len(seq) else ""

    events: list[dict] = []
    for i, form in enumerate(forms):
        filed = at(filed_on, i)
        if not filed or filed < cutoff:
            continue
        url = _url(cik, (at(accessions, i) or "").replace("-", ""), at(documents, i))

        if form.startswith("8-K"):
            codes = [c.strip() for c in (at(items, i) or "").split(",") if c.strip()]
            known = [c for c in codes if c in ITEMS]
            if not known:
                continue
            # One filing can report several things; each is its own line,
            # because "CEO left" and "results released" on the same day are
            # two different pieces of news.
            for code in known:
                category, headline, meaning = ITEMS[code]
                events.append({
                    "symbol": symbol, "filed": filed, "days_ago": _age(filed),
                    "form": form, "item": code, "category": category,
                    "category_label": CATEGORY_LABEL[category],
                    "headline": headline, "detail": meaning,
                    "impact": WEIGHT[category], "url": url, "source": SOURCE,
                    "facts": [
                        {"label": "Form", "value": form},
                        {"label": "Item", "value": f"{code} -- {headline}"},
                        {"label": "What the item covers", "value": meaning},
                        {"label": "Filed", "value": filed},
                        {"label": "Reported by", "value": "The company, to the SEC"},
                    ],
                })
            continue

        base_form = form.split("/")[0].strip()
        if base_form in DEAL_FORMS:
            events.append({
                "symbol": symbol, "filed": filed, "days_ago": _age(filed),
                "form": form, "item": None, "category": "deal",
                "category_label": CATEGORY_LABEL["deal"],
                "headline": DEAL_FORMS[base_form],
                "detail": "Merger or tender-offer paperwork filed with the SEC.",
                "facts": [
                    {"label": "Form", "value": form},
                    {"label": "What it is", "value": DEAL_FORMS[base_form]},
                    {"label": "Filed", "value": filed},
                ],
                "impact": "high", "url": url, "source": SOURCE,
            })

    # Dividends are not an 8-K item, so they are added from the dividend
    # feed rather than read off a filing.
    #
    # The deal and offering particulars that used to be merged here -- who
    # bought whom, how much an offering raised -- came from Benzinga and went
    # with it. The 8-K item code still says a merger agreement was signed and
    # still links the filing; what is lost is the sentence summarising it.
    try:
        import uw_company_service as uwc

        for row in (uwc.dividends(symbol, limit=6).get("payments") or []):
            if not row.get("declared"):
                continue
            events.append({
                "symbol": symbol, "filed": row["declared"],
                "days_ago": _age(row["declared"]),
                "form": "Dividend", "item": None, "category": "dividend",
                "category_label": CATEGORY_LABEL["dividend"],
                "headline": "Dividend declared",
                "detail": (f"Dividend {row['amount']:g}"
                           + (f", ex {row['ex_date']}" if row.get("ex_date") else "")
                           + (f", payable {row['pay_date']}" if row.get("pay_date") else "")
                           + "."),
                "impact": "low", "url": None, "source": "UNUSUAL_WHALES",
                "facts": [
                    {"label": "Amount", "value": f"{row['amount']:g}"},
                    {"label": "Declared", "value": row.get("declared") or "-"},
                    {"label": "Ex-dividend", "value": row.get("ex_date") or "-"},
                    {"label": "Payable", "value": row.get("pay_date") or "-"},
                ],
            })
    except Exception:  # noqa: BLE001
        pass

    # Filings that landed since the history was last read, from the live
    # watcher, so a filing minutes old is not missing from its own company.
    try:
        import edgar_live_service as live

        for row in live.recent(20, symbol)["events"]:
            filed = str(row.get("filed_at") or "")[:10]
            if filed and not any(e["filed"] == filed and e["form"] == row["form"]
                                 for e in events):
                events.append({
                    "symbol": symbol, "filed": filed, "days_ago": _age(filed),
                    "form": row["form"], "item": None, "category": "disclosure",
                    "category_label": CATEGORY_LABEL["disclosure"],
                    "headline": "Just filed",
                    "detail": "Filed minutes ago; EDGAR has not yet published "
                              "which items it reports.",
                    "impact": "medium", "url": row["url"], "source": SOURCE,
                    "live": True,
                })
    except Exception:  # noqa: BLE001
        pass

    events.sort(key=lambda e: e["filed"], reverse=True)

    counts: dict[str, int] = {}
    for e in events:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
        # Every row can be opened for its particulars, even one whose source
        # gave us nothing but a headline.
        e.setdefault("facts", [
            {"label": "Form", "value": e.get("form") or "-"},
            {"label": "Filed", "value": e.get("filed") or "-"},
            {"label": "Source", "value": e.get("source") or SOURCE},
        ])
    recent_90 = [e for e in events if (e["days_ago"] or 999) <= 90]

    result = {
        "symbol": symbol,
        "status": "OK" if events else "NO_DATA",
        "detail": (
            "Each line is an item the company itself tagged on its SEC filing. "
            "The tag says what kind of event it was, not the particulars -- "
            "open the filing for those."
            if events else f"No reportable events for {symbol} in {days} days."
        ),
        "events": events,
        "count": len(events),
        "window_days": days,
        "counts_by_category": counts,
        "last_90_days": len(recent_90),
        "leadership_changes": counts.get("leadership", 0),
        "deals": counts.get("deal", 0),
        "latest": events[0] if events else None,
        "source": SOURCE,
    }
    cache.put(key, result)
    return _shipped(result, limit)
