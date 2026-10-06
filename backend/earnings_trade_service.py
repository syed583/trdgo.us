"""
The combined Earnings Trade summary -- one payload for the unified screen.

Bundles the two scored engines (equity + options) down to just their headline
results, the expected move, the earnings date and countdown, the recommended
straddle construction with breakevens, the last several post-earnings price
moves, and a few plain-English takeaways. The per-parameter breakdown stays in
the engines; this screen shows only the summary cards.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

SOURCE = "Trdgo earnings model"


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _historical_moves(symbol: str, limit: int = 12) -> list[dict]:
    """The 1-day price move around each of the last few earnings reports."""
    try:
        import unusualwhales_service as uw
        dates = sorted({r.get("report_date") for r in uw._rows(uw.earnings_history(symbol))
                        if r.get("report_date") and r.get("reported_eps") not in (None, "")})
        if not dates:
            return []
        bars = uw._rows(uw.candles(symbol, "1d", limit=1500))
        bars = [b for b in bars if b.get("date") and _f(b.get("close")) is not None]
        bars.sort(key=lambda b: b["date"])
        closes = [(b["date"], _f(b["close"])) for b in bars]
        out: list[dict] = []
        for d in dates:
            # First trading day on/after the report; move vs the prior close.
            idx = next((i for i, (bd, _) in enumerate(closes) if bd >= d), None)
            if idx is None or idx == 0:
                continue
            prev, cur = closes[idx - 1][1], closes[idx][1]
            if prev:
                out.append({"date": d, "label": d[:7],
                            "move_pct": round((cur - prev) / prev * 100, 2)})
        return out[-limit:]
    except Exception:  # noqa: BLE001
        return []


def get_summary(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    import earnings_equity_service as eq
    import earnings_options_service as op
    from concurrent.futures import ThreadPoolExecutor

    def _quote():
        try:
            import live_market_service as market
            q = market.get_quote(symbol) or {}
            chart = market.get_chart(symbol, "3M")
            return {"q": q, "spark": [b["close"] for b in (chart.get("bars") or [])][-60:]}
        except Exception:  # noqa: BLE001
            return {"q": {}, "spark": []}

    def _preview():
        try:
            import uw_earnings_calendar as uwcal
            return uwcal.preview(symbol) or {}
        except Exception:  # noqa: BLE001
            return {}

    # The two engines and the auxiliary reads are independent -- run them at once
    # so the screen is not the sum of every provider round-trip in series.
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_equity = pool.submit(eq.get_analysis, symbol)
        f_options = pool.submit(op.get_analysis, symbol)
        f_quote = pool.submit(_quote)
        f_preview = pool.submit(_preview)
        f_hist = pool.submit(_historical_moves, symbol)
        equity = f_equity.result()
        options = f_options.result()
        qd = f_quote.result()
        pv = f_preview.result()
        hm = f_hist.result()

    q = qd["q"]
    spark = qd["spark"]
    price, change, change_pct = _f(q.get("price")), _f(q.get("change")), _f(q.get("change_percent"))
    name = q.get("name")
    exchange = q.get("exchange")
    tags = q.get("tags") or []
    sector = tags[0] if tags else None

    report_date = pv.get("next_report")
    report_label = pv.get("next_report_label")
    report_time = pv.get("next_report_time")
    em_pct = _f(pv.get("expected_move_percent"))
    sector = pv.get("sector") or sector

    con = options.get("construction") or {}
    spot = _f(con.get("spot")) or price
    if em_pct is None:
        em_pct = _f(con.get("expected_move_percent"))
    lower = upper = None
    if spot is not None and em_pct is not None:
        lower = round(spot * (1 - em_pct / 100), 2)
        upper = round(spot * (1 + em_pct / 100), 2)

    days_to = None
    if report_date:
        try:
            days_to = (datetime.fromisoformat(report_date).date() - date.today()).days
        except Exception:  # noqa: BLE001
            days_to = None

    # Recommended straddle with breakevens.
    cs, ps = _f(con.get("call_strike")), _f(con.get("put_strike"))
    prem = _f(con.get("combined_premium"))
    strategy = {
        "type": options.get("decision") if options.get("decision") in ("STRADDLE", "STRANGLE")
        else "STRADDLE",
        "expiry": con.get("expiry"),
        "call_strike": cs, "put_strike": ps, "total_premium": prem,
        "breakeven_upper": round((cs + prem), 2) if (cs is not None and prem) else None,
        "breakeven_lower": round((ps - prem), 2) if (ps is not None and prem) else None,
    }

    # Key takeaways, plain English from the headline readings.
    takeaways: list[str] = []
    ed, od = equity.get("decision"), options.get("decision")
    if ed in ("BUY", "SELL"):
        takeaways.append(f"Favorable {ed.lower()} setup for the equity earnings trade.")
    else:
        takeaways.append("No decisive directional edge for the stock into earnings.")
    if hm:
        avg_abs = sum(abs(m["move_pct"]) for m in hm) / len(hm)
        takeaways.append(f"Historical post-earnings move averages {avg_abs:.1f}%.")
    if em_pct is not None:
        takeaways.append(f"Options price in a ±{em_pct:.1f}% expected move.")
    takeaways.append("Review market and sector conditions and the risk alerts.")

    # Overall headline decision: the equity call, or the straddle when equity is flat.
    overall = ed if ed in ("BUY", "SELL") else (od if od in ("STRADDLE", "STRANGLE") else "NO TRADE")

    # The biggest reasons behind each gauge, so the screen can explain *why* the
    # score is what it is -- the parameters that moved it most, with their own
    # plain-English note.
    def _drivers(params, k: int = 5) -> list:
        avail = [p for p in (params or [])
                 if p.get("available") and p.get("points") is not None]
        avail.sort(key=lambda p: -abs(float(p.get("points") or 0)))
        return [{"label": p.get("label") or p.get("name"),
                 "leaning": p.get("leaning"),
                 "points_label": p.get("points_label"),
                 "detail": p.get("detail")}
                for p in avail[:k]]

    equity_why = _drivers(equity.get("params"))
    options_why = _drivers(options.get("params"))

    # Forward-looking "forecast": analysts' EPS and revenue estimates plus the
    # next-quarter guidance, with the recent post-earnings reaction. Taken from
    # the equity engine already computed above, so the detail page shows the
    # outlook without paying for a second analysis call.
    _FC_ORDER = ("eps_estimates", "revenue_estimates", "guidance", "historical_reaction")
    _by_name = {p.get("name"): p for p in (equity.get("params") or [])}
    forecast = [
        {"name": n, "label": p.get("label"), "leaning": p.get("leaning"),
         "points_label": p.get("points_label"), "available": p.get("available"),
         "detail": p.get("detail")}
        for n in _FC_ORDER if (p := _by_name.get(n))
    ]

    return {
        "status": "OK", "symbol": symbol, "source": SOURCE,
        "name": name, "exchange": exchange, "sector": sector,
        "price": price, "change": change, "change_percent": change_pct, "spark": spark,
        "earnings": {"date": report_date, "label": report_label, "time": report_time,
                     "days_to": days_to},
        "overall_decision": overall,
        "equity": {"score": equity.get("score"), "decision": equity.get("decision"),
                   "coverage": equity.get("coverage_pct"), "why": equity_why},
        "options": {"score": options.get("score"), "decision": options.get("decision"),
                    "coverage": options.get("coverage_pct"), "why": options_why},
        "expected_move": {"percent": em_pct, "lower": lower, "upper": upper,
                          "current": spot, "dollars": (round(spot * em_pct / 100, 2)
                                                       if spot and em_pct else None)},
        "strategy": strategy,
        "historical_moves": hm,
        "takeaways": takeaways,
        "forecast": forecast,
    }
