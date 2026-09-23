"""
Market scanner built on IBKR's server-side scan engine.

The important design point: scanning is done by TWS, not by us. A naive
scanner subscribes to a market-data line per candidate and instantly blows
through the ~100-line limit and the historical pacing budget. reqScannerData
returns a ranked contract list in one request, and only the rows the user
actually sees are then enriched with a quote, in one batch.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any, Optional

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB, ScannerSubscription, Stock

from ibkr_client import IBKRUnavailable, ibkr
from live_market_service import cache, num

SCAN_TTL = 60.0
PARAMS_TTL = 24 * 3600.0

# Enrichment is capped so a scan can never turn into a market-data flood.
MAX_ENRICH = 25

# Curated scans, chosen because each maps onto something the dashboard shows.
PRESETS: list[dict] = [
    {"key": "TOP_PERC_GAIN", "label": "Top % Gainers", "scan": "TOP_PERC_GAIN"},
    {"key": "TOP_PERC_LOSE", "label": "Top % Losers", "scan": "TOP_PERC_LOSE"},
    {"key": "MOST_ACTIVE", "label": "Most Active", "scan": "MOST_ACTIVE"},
    {"key": "HOT_BY_VOLUME", "label": "Hot by Volume", "scan": "HOT_BY_VOLUME"},
    {"key": "HIGH_OPT_VOLUME", "label": "High Option Volume",
     "scan": "OPT_VOLUME_MOST_ACTIVE"},
    {"key": "ABOVE_EMA20", "label": "Above EMA 20", "scan": "BULLISH_LAST_VS_EMA20"},
    {"key": "ABOVE_EMA50", "label": "Above EMA 50", "scan": "BULLISH_LAST_VS_EMA50"},
    {"key": "ABOVE_EMA200", "label": "Above EMA 200", "scan": "BULLISH_LAST_VS_EMA200"},
    {"key": "BELOW_EMA20", "label": "Below EMA 20", "scan": "BEARISH_LAST_VS_EMA20"},
    {"key": "HIGH_VS_52W", "label": "Near 52-Week High", "scan": "HIGH_VS_52W_HL"},
    {"key": "LOW_VS_52W", "label": "Near 52-Week Low", "scan": "LOW_VS_52W_HL"},
    {"key": "TOP_TRADE_COUNT", "label": "Most Trades", "scan": "TOP_TRADE_COUNT"},
]

PRESET_BY_KEY = {p["key"]: p for p in PRESETS}


def scanner_status() -> dict:
    """Health entry: can TWS serve scans for this account?"""
    try:
        codes = available_scan_codes()
    except Exception as exc:  # noqa: BLE001
        return {"status": "PROVIDER_OFFLINE", "detail": str(exc)}
    if not codes:
        return {"status": "DATA_UNAVAILABLE",
                "detail": "TWS returned no scanner parameters"}
    return {"status": "OK",
            "detail": f"{len(codes)} scan codes available from TWS",
            "presets": len(PRESETS)}


def available_scan_codes() -> list[str]:
    cached = cache.get("scanner:codes", PARAMS_TTL)
    if cached is not None:
        return cached

    async def job(ib: IB):
        return await ib.reqScannerParametersAsync()

    try:
        xml = ibkr.run(job, timeout=60)
    except IBKRUnavailable:
        return []

    try:
        root = ET.fromstring(xml)
        codes = sorted({e.text for e in root.iter("scanCode") if e.text})
    except ET.ParseError:
        codes = []

    cache.put("scanner:codes", codes)
    return codes


def list_presets() -> dict:
    codes = set(available_scan_codes())
    return {
        "presets": [
            {**p, "available": (not codes) or p["scan"] in codes}
            for p in PRESETS
        ],
        "scan_code_count": len(codes),
        "status": "OK" if codes else "PROVIDER_OFFLINE",
        "source": "IBKR",
    }


def enrich_technicals(rows: list[dict]) -> list[dict]:
    """
    Attach RSI, EMA posture and a trend label to scan rows.

    Uses the same batched fetch the watchlist uses (one market-data pass plus
    parallel history), capped at MAX_ENRICH symbols. Opt-in, because it costs a
    history request per symbol and a scan is meant to stay cheap.
    """
    import market_overview_service as overview

    symbols = [r["symbol"] for r in rows][:MAX_ENRICH]
    if not symbols:
        return rows

    from live_market_service import get_batch

    batch = get_batch(symbols)
    data = batch.get("symbols", {})

    for row in rows:
        hit = data.get(row["symbol"])
        if not hit:
            row["technicals_status"] = "NOT_SAMPLED"
            continue
        bars = hit.get("bars") or []
        row.update(overview._trend(bars))
        row["technicals_status"] = (
            "OK" if len(bars) >= 60 else "INSUFFICIENT_HISTORY"
        )
        # Relative volume against the trailing 20-session average.
        vols = [b["volume"] for b in bars[-21:-1] if b.get("volume")]
        if vols and bars and bars[-1].get("volume"):
            avg = sum(vols) / len(vols)
            row["relative_volume"] = (
                round(bars[-1]["volume"] / avg, 2) if avg else None
            )
        else:
            row["relative_volume"] = None
    return rows


def run_scan(
    preset: str = "MOST_ACTIVE",
    limit: int = 25,
    location: str = "STK.US.MAJOR",
    above_price: Optional[float] = None,
    below_price: Optional[float] = None,
    above_volume: Optional[int] = None,
    enrich: bool = True,
    technicals: bool = False,
) -> dict:
    """
    Run one scan and optionally attach a live quote to each row.

    ``above_price`` / ``above_volume`` are pushed into the subscription so TWS
    filters server-side; filtering after the fact would waste the scan slots.
    """
    spec = PRESET_BY_KEY.get(preset)
    if not spec:
        return {"rows": [], "status": "UNKNOWN_PRESET",
                "detail": f"No preset named {preset}", "source": "IBKR"}

    limit = max(1, min(int(limit), 50))
    key = (f"scan:{preset}:{limit}:{location}:{above_price}:{below_price}:"
           f"{above_volume}:{enrich}:{technicals}")
    cached = cache.get(key, SCAN_TTL)
    if cached:
        return cached

    async def job(ib: IB) -> dict:
        sub = ScannerSubscription(
            instrument="STK",
            locationCode=location,
            scanCode=spec["scan"],
            numberOfRows=limit,
        )
        if above_price is not None:
            sub.abovePrice = float(above_price)
        if below_price is not None:
            sub.belowPrice = float(below_price)
        if above_volume is not None:
            sub.aboveVolume = int(above_volume)

        try:
            scan = await ib.reqScannerDataAsync(sub)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

        rows = []
        contracts = []
        for item in scan:
            contract = item.contractDetails.contract
            rows.append({
                "rank": item.rank,
                "symbol": contract.symbol,
                "exchange": contract.primaryExchange or contract.exchange,
                "currency": contract.currency,
                "con_id": contract.conId,
            })
            contracts.append(contract)

        if not enrich or not rows:
            return {"rows": rows}

        # One batched market-data pass over just the visible rows.
        head = contracts[:MAX_ENRICH]
        tickers = [ib.reqMktData(c, "", False, False) for c in head]
        await asyncio.sleep(4.0)

        quotes: dict[int, dict] = {}
        for contract, ticker in zip(head, tickers):
            last = None
            for candidate in (ticker.last, ticker.close, ticker.markPrice):
                v = num(candidate)
                if v:
                    last = v
                    break
            prev = num(ticker.close)
            quotes[contract.conId] = {
                "price": round(last, 2) if last else None,
                "bid": num(ticker.bid),
                "ask": num(ticker.ask),
                "volume": num(ticker.volume),
                "change": (round(last - prev, 2)
                           if last is not None and prev else None),
                "change_percent": (round((last - prev) / prev * 100, 2)
                                   if last is not None and prev else None),
            }
            ib.cancelMktData(contract)

        for row in rows:
            row.update(quotes.get(row["con_id"], {}))
        return {"rows": rows}

    try:
        out = ibkr.run(job, timeout=120)
    except IBKRUnavailable as exc:
        return {"rows": [], "status": "PROVIDER_OFFLINE",
                "detail": str(exc), "source": "IBKR"}

    if out.get("error"):
        return {"rows": [], "status": "DATA_UNAVAILABLE",
                "detail": out["error"], "source": "IBKR"}

    rows = out["rows"]
    if technicals and rows:
        try:
            rows = enrich_technicals(rows)
        except Exception:  # noqa: BLE001 - a scan is still useful without them
            for r in rows:
                r["technicals_status"] = "UNAVAILABLE"

    result = {
        "preset": preset,
        "label": spec["label"],
        "scan_code": spec["scan"],
        "location": location,
        "rows": rows,
        "count": len(rows),
        "enriched": min(len(rows), MAX_ENRICH) if enrich else 0,
        "technicals": bool(technicals),
        "status": "OK" if rows else "DATA_UNAVAILABLE",
        "note": (
            f"Scan executed by TWS. Quotes attached to the first "
            f"{MAX_ENRICH} rows only, to stay inside the market-data line limit."
        ),
        "source": "IBKR",
    }
    cache.put(key, result)
    return result
