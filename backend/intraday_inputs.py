"""
The three intraday-model parameters the single directional model does not
compute: absolute market direction (SPY/QQQ), sector strength, and today's
analyst action. Each is returned as a model signal (name, bias in [-1, 1],
available) to append to a name's signal list before the dual score is taken.

Market direction is market-wide, so it is the same for every name on a pass;
sector strength reads the name's SPDR sector ETF; analyst action reads today's
rating changes. All lean on already-cached quotes / fetches, so running them per
board name stays cheap.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# SPDR sector ETF by a keyword in the company's sector label.
_SECTOR_ETF = [
    ("XLK", ("technolog", "information tech", "software", "semiconduc", "hardware")),
    ("XLF", ("financ", "bank", "insurance", "capital market")),
    ("XLE", ("energy", "oil", "gas", "petroleum")),
    ("XLV", ("health", "pharma", "biotech", "medical", "life science")),
    ("XLY", ("discretion", "cyclical", "retail", "auto", "apparel", "restaurant", "travel")),
    ("XLP", ("staple", "defensive", "food", "beverage", "household", "tobacco")),
    ("XLI", ("industrial", "aerospace", "machinery", "transport", "airline", "defense")),
    ("XLB", ("material", "chemical", "metal", "mining", "steel", "paper")),
    ("XLU", ("utilit",)),
    ("XLRE", ("real estate", "reit")),
    ("XLC", ("communication", "telecom", "media", "entertainment", "interactive")),
]


# Direct ticker -> SPDR sector ETF for the names the board tracks and other
# common large caps. SIC "sector" labels (Manufacturing, Services, ...) are too
# coarse to map, so this table is the reliable source; the keyword fallback
# below covers anything not listed (e.g. an arbitrary Tradgo Call ticker).
_TICKER_ETF = {
    # Technology
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK", "AMD": "XLK",
    "INTC": "XLK", "MU": "XLK", "QCOM": "XLK", "CRM": "XLK", "ORCL": "XLK",
    "ADBE": "XLK", "SMCI": "XLK", "TXN": "XLK", "MRVL": "XLK", "DELL": "XLK",
    # Communication services
    "GOOGL": "XLC", "GOOG": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "CMCSA": "XLC", "T": "XLC", "VZ": "XLC",
    # Consumer discretionary
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "NKE": "XLY", "MCD": "XLY",
    "SBUX": "XLY", "LOW": "XLY",
    # Financials
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "V": "XLF", "MA": "XLF",
    "WFC": "XLF", "MS": "XLF", "C": "XLF",
    # Energy
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE", "OXY": "XLE",
    # Health care
    "UNH": "XLV", "LLY": "XLV", "JNJ": "XLV", "PFE": "XLV", "ABBV": "XLV",
    "MRK": "XLV",
    # Consumer staples
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP",
    # Industrials
    "CAT": "XLI", "BA": "XLI", "GE": "XLI", "HON": "XLI", "UPS": "XLI",
}


def _sector_etf(sector: Optional[str]) -> Optional[str]:
    if not sector:
        return None
    s = sector.lower()
    for etf, keys in _SECTOR_ETF:
        if any(k in s for k in keys):
            return etf
    return None


def _change_bias(symbol: str, scale: float = 0.6) -> Optional[float]:
    import live_market_service as market
    pct = _f((market.get_quote(symbol) or {}).get("change_percent"))
    return None if pct is None else _clamp(pct / scale)


def market_direction() -> dict:
    """SPY and QQQ day direction -- the whole market's tailwind or headwind."""
    import live_market_service as market
    spy = _f((market.get_quote("SPY") or {}).get("change_percent"))
    qqq = _f((market.get_quote("QQQ") or {}).get("change_percent"))
    vals = [v for v in (spy, qqq) if v is not None]
    if not vals:
        return {"name": "market_direction", "bias": None, "available": False,
                "label": "Market (SPY/QQQ)"}
    bias = _clamp((sum(vals) / len(vals)) / 0.5)
    detail = []
    if spy is not None:
        detail.append(f"SPY {spy:+.2f}%")
    if qqq is not None:
        detail.append(f"QQQ {qqq:+.2f}%")
    return {"name": "market_direction", "bias": round(bias, 3), "available": True,
            "label": "Market (SPY/QQQ)", "detail": " / ".join(detail)}


def sector_strength(symbol: str, sector: Optional[str] = None) -> dict:
    name, label = "sector_strength", "Sector Strength"
    try:
        etf = _TICKER_ETF.get((symbol or "").upper())
        if not etf:
            if sector is None:
                import company_profile_service as cp
                prof = (cp.get_profiles([symbol]) or {}).get(symbol) or {}
                # Prefer the finer 'industry' label; fall back to the coarse sector.
                sector = prof.get("industry") or prof.get("sector")
            etf = _sector_etf(sector)
        if not etf:
            return {"name": name, "bias": None, "available": False, "label": label}
        bias = _change_bias(etf)
        if bias is None:
            return {"name": name, "bias": None, "available": False, "label": label}
        detail = f"{etf} sector ETF" + (f" ({sector})" if sector else "")
        return {"name": name, "bias": round(bias, 3), "available": True,
                "label": label, "detail": detail}
    except Exception:  # noqa: BLE001
        return {"name": name, "bias": None, "available": False, "label": label}


def analyst_action(symbol: str) -> dict:
    """Today's rating changes: an upgrade/buy reads bullish, a cut bearish."""
    name, label = "analyst_action", "Analyst Action"
    import live_market_service as market
    key = f"im:analyst:{symbol}"
    cached = market.cache.get(key, 1800.0)
    if cached is not None:
        return cached
    sig = {"name": name, "bias": 0.0, "available": True, "label": label,
           "detail": "No action today"}
    try:
        import analyst_consensus_service as ac
        rows = ac._fetch(symbol, days=2) or []
        today = _dt.date.today().isoformat()
        todays = [r for r in rows if str(r.get("date") or "")[:10] == today]
        if todays:
            score = 0.0
            for r in todays:
                rec = (r.get("rating_current") or "").lower()
                if any(k in rec for k in ("buy", "outperform", "overweight", "strong")):
                    score += 1
                elif any(k in rec for k in ("sell", "underperform", "underweight", "reduce")):
                    score -= 1
            sig = {"name": name, "bias": round(_clamp(score), 3), "available": True,
                   "label": label, "detail": f"{len(todays)} action(s) today"}
    except Exception:  # noqa: BLE001
        sig = {"name": name, "bias": None, "available": False, "label": label}
    market.cache.put(key, sig)
    return sig


def extra_signals(symbol: str, sector: Optional[str] = None) -> list[dict]:
    return [market_direction(), sector_strength(symbol, sector), analyst_action(symbol)]


# Representative display weight per extra parameter (mid-way between the model's
# BUY and SELL weights), for showing them in the Analysis parameter list.
_DISPLAY_WEIGHT = {"market_direction": 8, "sector_strength": 6, "analyst_action": 4}


def analysis_signals(symbol: str, sector: Optional[str] = None) -> list[dict]:
    """The three extra parameters, shaped like the model's own signals so they
    render in the Analysis parameter list. Display-only: they do not change the
    Analysis score, which is already computed from the base model."""
    out = []
    for sig in extra_signals(symbol, sector):
        w = _DISPLAY_WEIGHT.get(sig.get("name"), 4)
        bias = sig.get("bias")
        pts = round(bias * w, 2) if (sig.get("available") and bias is not None) else None
        out.append({
            "name": sig.get("name"),
            "label": sig.get("label"),
            "weight": w,
            "available": bool(sig.get("available")),
            "bias": bias,
            "points": pts,
            "points_label": (f"{pts:+.1f} of {w}" if pts is not None else f"-- of {w}"),
            "directional": True,
            "detail": sig.get("detail") or "",
            "source": "Intraday model",
        })
    return out
