"""
Engine 3 -- Corporate News & Risk Alerts (shared, unscored).

These alerts are shown on both the Equity and Options earnings screens and never
change either 100-point score. Each is classified RED (material/critical), AMBER
(relevant, assess) or GREY (informational); a positive development is flagged as
an event without implying risk. Everything is built from publicly disclosed
information -- insider/ownership filings, corporate financial events, and news
headlines classified by keyword into the structure's categories A-E.

Caveat carried from the structure: do not infer bad earnings from insider
selling, dark-pool prints or institutional position changes alone; planned
trades, delayed filings and non-directional activity are flagged as such.
"""

from __future__ import annotations

import re
from typing import Optional

SOURCE = "Unusual Whales / public filings & news"

# Keyword -> (category, severity). First match wins; order matters.
# Patterns use word boundaries so "software" can't trip "war", etc.
_RULES: list[tuple[str, str, str]] = [
    (r"\b(bankrupt\w*|chapter 11|default|going concern|insolven\w*)\b", "FINANCIAL", "RED"),
    (r"\b(fraud|investigation|probe|subpoena|lawsuit|litigation)\b", "FINANCIAL", "RED"),
    (r"\b(downgrade[sd]?|delist\w*|trading halt|recall[s]?|data breach|hacked?)\b", "INDUSTRY", "RED"),
    (r"\b(guidance cut|profit warning|warns|shortfall|slashe?[sd]?)\b", "FINANCIAL", "RED"),
    (r"\b(war|sanction[s]?|tariff[s]?|embargo|export ban|geopolitical|invasion)\b", "GEOPOLITICAL", "AMBER"),
    (r"\b(offering|dilution|secondary offering|convertible|new debt|bond sale|credit downgrade)\b", "FINANCIAL", "AMBER"),
    (r"\b(restructur\w*|layoffs?|job cuts|plant closure|supplier|supply chain|shortage)\b", "SUPPLY", "AMBER"),
    (r"\b(antitrust|regulat\w*|price war|market share loss|ftc|doj probe)\b", "INDUSTRY", "AMBER"),
    (r"\b(insider (buying|selling)|13d|13g|stake)\b", "INSIDER", "GREY"),
    (r"\b(buyback|repurchase|dividend)\b", "FINANCIAL", "GREY"),
    (r"\b(upgrade[sd]?|beats|record (revenue|quarter)|contract win|partnership|approval|launches?)\b", "INDUSTRY", "GREY"),
]


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _classify(text: str) -> Optional[tuple[str, str]]:
    t = (text or "").lower()
    for pattern, category, severity in _RULES:
        if re.search(pattern, t):
            return category, severity
    return None


def _news_alerts(symbol: str, limit: int = 20) -> list[dict]:
    out: list[dict] = []
    try:
        import uw_news_adapter as news
        items = (news.get_news(symbol, limit) or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return out
    seen: set[str] = set()
    for it in items:
        headline = (it.get("headline") or "").strip()
        if not headline or headline.lower() in seen:
            continue
        seen.add(headline.lower())
        hit = _classify(headline)
        if not hit:
            continue
        category, severity = hit
        # A positive-toned headline is an event, not a risk.
        tone = (it.get("sentiment") or "").upper()
        if severity == "RED" and tone == "BULLISH":
            severity = "AMBER"
        out.append({
            "category": category, "severity": severity,
            "title": headline,
            "detail": "News headline (public). Direction and materiality to be assessed.",
            "date": it.get("time_label") or it.get("published_at") or it.get("time"),
            "source": it.get("source") or "News",
            "url": it.get("url"),
            "confidence": "headline-classified",
        })
    return out


def _insider_alert(symbol: str) -> list[dict]:
    try:
        import uw_ownership_service as own
        d = own.insider_transactions_preferred(symbol, days=120) or {}
        rows = d.get("transactions") or d.get("rows") or d.get("items") or []
        if not rows:
            return []
        buys = sum(1 for r in rows if str(r.get("side") or r.get("type") or "").lower().startswith("b")
                   or (r.get("transaction_code") in ("P",)))
        sells = sum(1 for r in rows if str(r.get("side") or r.get("type") or "").lower().startswith("s")
                    or (r.get("transaction_code") in ("S",)))
        if buys == 0 and sells == 0:
            return []
        lean = "buying" if buys > sells else "selling" if sells > buys else "mixed"
        return [{
            "category": "INSIDER", "severity": "GREY",
            "title": f"Insider activity (120d): {buys} buys, {sells} sells — net {lean}",
            "detail": ("Public Form 4 filings. Not an earnings signal on its own: "
                       "planned/10b5-1 sales and delayed filings are common."),
            "date": None, "source": "Form 4 (public)", "confidence": "filing-based",
        }]
    except Exception:  # noqa: BLE001
        return []


def _institutional_alert(symbol: str) -> list[dict]:
    try:
        import institutional_service as inst
        d = inst.get_institutional_activity(symbol) or {}
        if d.get("status") not in ("OK", None):
            return []
        net = _f(d.get("net_share_change_pct"))
        if net is None:
            return []
        sev = "AMBER" if abs(net) >= 10 else "GREY"
        direction = "increased" if net > 0 else "reduced" if net < 0 else "held"
        return [{
            "category": "INSIDER", "severity": sev,
            "title": f"Institutions {direction} holdings {net:+.1f}% (latest 13F)",
            "detail": ("13F filings lag by up to a quarter; this is a dated, "
                       "non-directional ownership change, not an earnings call."),
            "date": d.get("latest_quarter"), "source": "13F (public)",
            "confidence": "filing-based (lagged)",
        }]
    except Exception:  # noqa: BLE001
        return []


def _event_alerts(symbol: str) -> list[dict]:
    try:
        import corporate_events_service as ev
        d = ev.get_events(symbol) if hasattr(ev, "get_events") \
            else ev.corporate_events(symbol) if hasattr(ev, "corporate_events") else {}
        rows = (d or {}).get("events") or (d or {}).get("rows") or []
        out = []
        for r in rows[:12]:
            title = r.get("title") or r.get("type") or r.get("headline")
            if not title:
                continue
            hit = _classify(title) or ("FINANCIAL", "GREY")
            out.append({
                "category": hit[0], "severity": hit[1], "title": title,
                "detail": r.get("detail") or "Corporate event (public filing).",
                "date": r.get("date") or r.get("filed_at"),
                "source": r.get("source") or "Filing", "confidence": "filing-based",
            })
        return out
    except Exception:  # noqa: BLE001
        return []


_SEV_RANK = {"RED": 0, "AMBER": 1, "GREY": 2}


def get_alerts(symbol: str) -> dict:
    """All corporate alerts for a ticker, most severe first."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "alerts": [], "source": SOURCE}

    alerts: list[dict] = []
    alerts += _insider_alert(symbol)
    alerts += _institutional_alert(symbol)
    alerts += _event_alerts(symbol)
    alerts += _news_alerts(symbol)

    alerts.sort(key=lambda a: _SEV_RANK.get(a.get("severity"), 3))
    counts = {s: sum(1 for a in alerts if a.get("severity") == s)
              for s in ("RED", "AMBER", "GREY")}

    return {
        "status": "OK", "symbol": symbol, "alerts": alerts[:40],
        "counts": counts,
        "note": ("Shared alerts -- they do not change either earnings score. "
                 "Public information only; assess materiality, direction and "
                 "data freshness before acting."),
        "source": SOURCE,
    }
