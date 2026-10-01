"""
Ticker Overview: one page of everything on a stock, Unusual-Whales style.

Aggregates the figures the overview screen shows from the sources the app
already pulls -- the quote, the options-volume summary, the company profile,
analyst actions and SEC insider filings -- into a single payload, gathered
concurrently so the page is one request. Phase 1 covers Key Stats, Performance,
Analysts and Insiders; the intraday options-volume chart, the historical table
and the daily GEX chart follow.

Every section is best-effort: a provider that does not answer leaves its section
marked unavailable rather than failing the whole page.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

PERIODS = [
    ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252),
]
PEERS = ["SPY", "QQQ", "IWM"]


def _f(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _returns(bars: list[dict]) -> dict:
    """Trailing % returns over each period from a daily-close series."""
    closes = [b.get("close") for b in bars if b.get("close") is not None]
    out: dict[str, Optional[float]] = {}
    if len(closes) < 2:
        return {k: None for k, _ in PERIODS}
    last = closes[-1]
    for label, n in PERIODS:
        if len(closes) > n:
            base = closes[-1 - n]
            out[label] = round((last - base) / base * 100, 2) if base else None
        else:
            first = closes[0]
            out[label] = round((last - first) / first * 100, 2) if first else None
    return out


def _key_stats(symbol: str) -> dict:
    import live_market_service as market
    q = {}
    try:
        q = market.get_quote(symbol) or {}
    except Exception:  # noqa: BLE001
        q = {}

    # 52-week range + average volume from a year of bars.
    hi = lo = avgvol = None
    try:
        bars = (market.get_chart(symbol, "1Y") or {}).get("bars") or []
        highs = [b.get("high") for b in bars if b.get("high") is not None]
        lows = [b.get("low") for b in bars if b.get("low") is not None]
        vols = [b.get("volume") for b in bars if b.get("volume")]
        hi = max(highs) if highs else None
        lo = min(lows) if lows else None
        avgvol = round(sum(vols) / len(vols)) if vols else None
    except Exception:  # noqa: BLE001
        pass

    # Market cap from the SEC profile (shares * price).
    mktcap = pe = sector = name = None
    try:
        import company_profile_service as cp
        prof = (cp.get_profiles([symbol]) or {}).get(symbol) or {}
        name = prof.get("company_name")
        sector = prof.get("sector")
        shares = _f(prof.get("shares_outstanding"))
        price = _f(q.get("price"))
        if shares and price:
            mktcap = round(shares * price)
    except Exception:  # noqa: BLE001
        pass

    # Options volume / premium / OI split, from the day's options-volume feed
    # (the overview tiles carry only a single total notional, not the split).
    ov_row: dict = {}
    try:
        import unusualwhales_service as uw
        resp = uw.options_volume(symbol) or {}
        rows = uw._rows(resp)
        data = resp.get("data")
        ov_row = rows[0] if rows else (data if isinstance(data, dict) else {})
    except Exception:  # noqa: BLE001
        ov_row = {}
    cvol = _f(ov_row.get("call_volume"))
    pvol = _f(ov_row.get("put_volume"))
    cprem = _f(ov_row.get("call_premium"))
    pprem = _f(ov_row.get("put_premium"))
    ncp = _f(ov_row.get("net_call_premium"))
    npp = _f(ov_row.get("net_put_premium"))
    coi = _f(ov_row.get("call_open_interest"))
    poi = _f(ov_row.get("put_open_interest"))
    net_prem = (ncp or 0) + (npp or 0) if (ncp is not None or npp is not None) else None
    tiles = {
        "call_volume": cvol, "put_volume": pvol,
        "put_call_volume": round(pvol / cvol, 3) if cvol and pvol else None,
        "call_premium": cprem, "put_premium": pprem, "net_premium": net_prem,
        "call_oi": coi, "put_oi": poi,
        "total_oi": (coi or 0) + (poi or 0) if (coi is not None or poi is not None) else None,
    }

    return {
        "symbol": symbol,
        "name": name or symbol,
        "sector": sector,
        "price": q.get("price"),
        "change": q.get("change"),
        "change_percent": q.get("change_percent"),
        "previous_close": q.get("previous_close"),
        "open": q.get("open"),
        "day_high": q.get("high"),
        "day_low": q.get("low"),
        "week52_high": round(hi, 2) if hi else None,
        "week52_low": round(lo, 2) if lo else None,
        "volume": q.get("volume"),
        "avg_volume": avgvol,
        "market_cap": mktcap,
        "pe_ratio": pe,
        "call_volume": tiles.get("call_volume"),
        "put_volume": tiles.get("put_volume"),
        "put_call_volume": tiles.get("put_call_volume"),
        "call_premium": tiles.get("call_premium") or tiles.get("call_notional"),
        "put_premium": tiles.get("put_premium") or tiles.get("put_notional"),
        "net_premium": tiles.get("net_premium"),
        "call_oi": tiles.get("call_oi"),
        "put_oi": tiles.get("put_oi"),
        "total_oi": tiles.get("total_oi"),
        "status": "OK" if q.get("price") is not None else "NO_DATA",
    }


def _performance(symbol: str) -> dict:
    import live_market_service as market

    def series(sym: str) -> dict:
        try:
            bars = (market.get_chart(sym, "1Y") or {}).get("bars") or []
            return {"symbol": sym, "returns": _returns(bars)}
        except Exception:  # noqa: BLE001
            return {"symbol": sym, "returns": {k: None for k, _ in PERIODS}}

    syms = [symbol] + [p for p in PEERS if p != symbol]
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ov-perf") as pool:
        rows = list(pool.map(series, syms))
    return {"periods": [p for p, _ in PERIODS], "rows": rows, "status": "OK"}


def _analysts(symbol: str) -> dict:
    try:
        import uw_company_service as uwc
        out = uwc.analyst_actions(symbol, limit=25)
        return {"status": out.get("status", "NO_DATA"),
                "rows": out.get("rows", [])[:25],
                "upgrades": out.get("upgrades"), "downgrades": out.get("downgrades")}
    except Exception:  # noqa: BLE001
        return {"status": "DATA_UNAVAILABLE", "rows": []}


def _insiders(symbol: str) -> dict:
    try:
        import uw_ownership_service as own
        out = own.insider_transactions_preferred(symbol, days=180) or {}
        return {
            "status": out.get("status", "NO_DATA"),
            "transactions": (out.get("transactions") or [])[:30],
            "buy_count": out.get("buy_count"),
            "sell_count": out.get("sell_count"),
            "buy_value": out.get("buy_value"),
            "sell_value": out.get("sell_value"),
            "net_value": out.get("net_value"),
        }
    except Exception:  # noqa: BLE001
        return {"status": "DATA_UNAVAILABLE", "transactions": []}


def _intraday(symbol: str) -> dict:
    """Minute-by-minute call/put volume and cumulative net premium."""
    try:
        import unusualwhales_service as uw
        rows = uw._rows(uw.net_premium_ticks(symbol))
    except Exception:  # noqa: BLE001
        rows = []
    out = []
    cum = 0.0
    for r in rows:
        ncp = _f(r.get("net_call_premium")) or 0.0
        npp = _f(r.get("net_put_premium")) or 0.0
        cum += ncp + npp
        t = str(r.get("tape_time") or "")
        hm = t[11:16] if len(t) >= 16 else t
        out.append({
            "time": hm,
            "call_volume": _f(r.get("call_volume")),
            "put_volume": _f(r.get("put_volume")),
            "net_premium": round(cum),
        })
    return {"status": "OK" if out else "NO_DATA", "series": out}


def _history(symbol: str, days: int = 20) -> dict:
    """Daily OHLC, % change and volume, with IV rank merged in by date."""
    import unusualwhales_service as uw
    try:
        bars = uw._rows(uw.candles(symbol, size="1d", limit=days + 5))
    except Exception:  # noqa: BLE001
        bars = []
    ivr_by_date: dict[str, float] = {}
    try:
        for r in uw._rows(uw.iv_rank(symbol)):
            d = str(r.get("date"))[:10]
            v = _f(r.get("iv_rank_1y"))
            if d and v is not None:
                ivr_by_date[d] = round(v, 1)
    except Exception:  # noqa: BLE001
        pass

    # The feed can return more than one row per date (regular + extended). Keep
    # one per date: the regular session, else the row with the most volume.
    best: dict[str, dict] = {}
    for b in bars:
        d = str(b.get("date"))[:10]
        if not d:
            continue
        cur = best.get(d)
        if cur is None:
            best[d] = b
            continue
        if str(b.get("market_time")) == "r" and str(cur.get("market_time")) != "r":
            best[d] = b
        elif (_f(b.get("volume")) or 0) > (_f(cur.get("volume")) or 0) \
                and str(cur.get("market_time")) != "r":
            best[d] = b
    bars = list(best.values())

    rows = []
    prev_close: Optional[float] = None
    # Oldest first so % change reads against the prior session, then newest first.
    bars = sorted(bars, key=lambda b: str(b.get("date")))
    for b in bars:
        close = _f(b.get("close"))
        date = str(b.get("date"))[:10]
        chg = ((close - prev_close) / prev_close * 100) if (close and prev_close) else None
        rows.append({
            "date": date,
            "open": _f(b.get("open")), "high": _f(b.get("high")),
            "low": _f(b.get("low")), "close": close,
            "change_pct": round(chg, 2) if chg is not None else None,
            "volume": _f(b.get("volume")) or _f(b.get("total_volume")),
            "ivr": ivr_by_date.get(date),
        })
        prev_close = close
    rows = list(reversed(rows))[:days]
    return {"status": "OK" if rows else "NO_DATA", "rows": rows}


def _gex(symbol: str, days: int = 120) -> dict:
    """Daily net gamma exposure (call + put) with the closing price by date."""
    import unusualwhales_service as uw
    try:
        rows = uw._rows(uw.get(f"/api/stock/{symbol}/greek-exposure", {}))
    except Exception:  # noqa: BLE001
        rows = []
    price_by_date: dict[str, float] = {}
    try:
        for b in uw._rows(uw.candles(symbol, size="1d", limit=days + 10)):
            d = str(b.get("date"))[:10]
            c = _f(b.get("close"))
            if d and c is not None:
                price_by_date[d] = c
    except Exception:  # noqa: BLE001
        pass

    out = []
    for r in rows:
        d = str(r.get("date"))[:10]
        cg = _f(r.get("call_gamma"))
        pg = _f(r.get("put_gamma"))
        if d is None or (cg is None and pg is None):
            continue
        out.append({
            "date": d,
            "call_gamma": round(cg) if cg is not None else None,
            "put_gamma": round(pg) if pg is not None else None,
            "net_gamma": round((cg or 0) + (pg or 0)),
            "price": price_by_date.get(d),
        })
    out.sort(key=lambda x: x["date"])
    out = out[-days:]
    return {"status": "OK" if out else "NO_DATA", "series": out}


def get_overview(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL"}

    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="overview") as pool:
        f_stats = pool.submit(_key_stats, symbol)
        f_perf = pool.submit(_performance, symbol)
        f_an = pool.submit(_analysts, symbol)
        f_ins = pool.submit(_insiders, symbol)
        f_intra = pool.submit(_intraday, symbol)
        f_hist = pool.submit(_history, symbol)
        f_gex = pool.submit(_gex, symbol)
        stats = f_stats.result()
        perf = f_perf.result()
        analysts = f_an.result()
        insiders = f_ins.result()
        intraday = f_intra.result()
        history = f_hist.result()
        gex = f_gex.result()

    return {
        "symbol": symbol,
        "status": "OK",
        "key_stats": stats,
        "performance": perf,
        "analysts": analysts,
        "insiders": insiders,
        "intraday": intraday,
        "history": history,
        "gex": gex,
        "source": "UNUSUAL_WHALES + SEC",
    }
