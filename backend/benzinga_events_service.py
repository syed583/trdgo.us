"""
Benzinga's corporate-event calendars: the particulars EDGAR's tags omit.

The SEC tells you a company completed an acquisition; Benzinga tells you who
bought whom, for how much, and whether it closed. Same for a share offering
(how many shares, what they raised) and a dividend (the amount and the yield).
So these are merged into the same events feed as enrichment, never as a
replacement: EDGAR remains the record of what was filed.

Only the calendars this account's key actually returns are used -- mergers,
offerings and dividends. Earnings has its own service already.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import provider_config as cfg
from live_market_service import cache

SOURCE = "BENZINGA"
TTL = 3 * 3600.0
WINDOW_DAYS = 365


def configured() -> bool:
    return cfg.BENZINGA.configured


def _num(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _money(value: Optional[float]) -> str:
    if not value:
        return ""
    for unit, size in (("bn", 1e9), ("m", 1e6), ("k", 1e3)):
        if abs(value) >= size:
            return f"${value / size:,.1f}{unit}"
    return f"${value:,.0f}"


def _facts(pairs: list) -> list[dict]:
    """
    The particulars behind a headline, as label/value rows.

    A line that only says "Dividend declared" and cannot be opened is a dead
    end on the screen; these are the figures the calendar already gave us,
    kept as fields rather than flattened into the sentence, so the row can
    show its own working.
    """
    return [{"label": label, "value": str(value)}
            for label, value in pairs if value not in (None, "", 0)]


def _call(path: str, symbol: str, days: int) -> list[dict]:
    start = (date.today() - timedelta(days=days)).isoformat()
    payload = cfg.fetch_json(f"{cfg.BENZINGA.base_url}/v2.1/calendar/{path}", {
        "token": cfg.BENZINGA.api_key,
        "parameters[tickers]": symbol,
        "parameters[date_from]": start,
        "parameters[date_to]": (date.today() + timedelta(days=30)).isoformat(),
        "pagesize": 50,
    })
    if isinstance(payload, dict):
        rows = payload.get(path) or []
        return rows if isinstance(rows, list) else []
    return payload if isinstance(payload, list) else []


def _merger_events(symbol: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        acquirer = r.get("acquirer_ticker") or r.get("acquirer_name") or "?"
        target = r.get("target_ticker") or r.get("target_name") or "?"
        side = "buying" if str(r.get("acquirer_ticker") or "").upper() == symbol else "being bought"
        value = _money(_num(r.get("deal_value")) or _num(r.get("deal_price_per_share")))
        status = (r.get("deal_status") or "").title() or "Announced"
        completed = r.get("date_completed")
        bits = [f"{acquirer} to acquire {target}"]
        if value:
            bits.append(value)
        bits.append(f"status: {status}")
        if completed:
            bits.append(f"completed {completed}")
        out.append({
            "filed": r.get("date") or r.get("announced_date"),
            "category": "deal", "headline": f"Merger -- {symbol} is {side}",
            "detail": ". ".join(bits) + ".", "impact": "high",
            "form": "M&A", "item": None, "url": None, "source": SOURCE,
            "facts": _facts([
                ("Acquirer", r.get("acquirer_name") or r.get("acquirer_ticker")),
                ("Target", r.get("target_name") or r.get("target_ticker")),
                ("Deal value", value),
                ("Price per share", _money(_num(r.get("deal_price_per_share")))),
                ("Payment", (r.get("deal_payment_type") or "").title()),
                ("Type", (r.get("deal_type") or "").title()),
                ("Status", status),
                ("Announced", r.get("date") or r.get("announced_date")),
                ("Expected close", r.get("date_expected_to_close")),
                ("Completed", completed),
            ]),
        })
    return out


def _offering_events(symbol: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        raised = _money(_num(r.get("dollar_shares")))
        shares = _num(r.get("shares_offered"))
        bits = ["Share offering"]
        if shares:
            bits.append(f"{shares:,.0f} shares")
        if raised:
            bits.append(raised)
        if r.get("offering_type"):
            bits.append(str(r.get("offering_type")))
        out.append({
            "filed": r.get("date"), "category": "funding",
            "headline": "Share offering", "detail": ", ".join(bits) + ".",
            "impact": "medium", "form": "Offering", "item": None,
            "url": None, "source": SOURCE,
            "facts": _facts([
                ("Raised", raised),
                ("Shares offered", f"{shares:,.0f}" if shares else None),
                ("Offering type", r.get("offering_type")),
                ("Price", _money(_num(r.get("offering_price")))),
                ("Date", r.get("date")),
            ]),
        })
    return out


def _dividend_events(symbol: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        amount = _num(r.get("dividend"))
        prior = _num(r.get("dividend_prior"))
        yld = _num(r.get("dividend_yield"))
        move = ""
        if amount is not None and prior is not None and amount != prior:
            move = " raised from" if amount > prior else " cut from"
            move += f" {prior:g}"
        bits = [f"Dividend {amount:g}" if amount is not None else "Dividend"]
        if move:
            bits[0] += move
        if yld:
            bits.append(f"yield {yld * 100:.2f}%")
        if r.get("payable_date"):
            bits.append(f"payable {r['payable_date']}")
        out.append({
            "filed": r.get("ex_dividend_date") or r.get("date"),
            "category": "dividend", "headline": "Dividend declared",
            "detail": ", ".join(bits) + ".", "impact": "low",
            "form": "Dividend", "item": None, "url": None, "source": SOURCE,
            "facts": _facts([
                ("Amount", f"{amount:g}" if amount is not None else None),
                ("Previous", f"{prior:g}" if prior is not None else None),
                ("Yield", f"{yld * 100:.2f}%" if yld else None),
                ("Frequency", r.get("frequency")),
                ("Ex-dividend", r.get("ex_dividend_date")),
                ("Record date", r.get("record_date")),
                ("Payable", r.get("payable_date")),
                ("Declared", r.get("date_declaration") or r.get("date")),
            ]),
        })
    return out


def get_events(symbol: str, days: int = WINDOW_DAYS) -> list[dict]:
    """Merger, offering and dividend events for one symbol. Never raises."""
    symbol = (symbol or "").upper().strip()
    if not configured():
        return []
    key = f"bz:events:{symbol}:{days}"
    hit = cache.get(key, TTL)
    if hit is not None:
        return hit

    events: list[dict] = []
    for path, shape in (("ma", _merger_events), ("offerings", _offering_events),
                        ("dividends", _dividend_events)):
        try:
            events += shape(symbol, _call(path, symbol, days))
        except Exception:  # noqa: BLE001 - enrichment must never break the panel
            continue

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    events = [e for e in events if e.get("filed") and str(e["filed"]) >= cutoff]
    for e in events:
        e["symbol"] = symbol
        try:
            e["days_ago"] = (date.today() - datetime.fromisoformat(
                str(e["filed"])[:10]).date()).days
        except ValueError:
            e["days_ago"] = None
    cache.put(key, events)
    return events
