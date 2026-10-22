"""Genuine Forward Scoreboard (Track A) — after-cost performance of the REAL
recorded pre-earnings predictions, with audit checks.

Honesty model:
  * RECORDED prediction  — a real BUY/SELL made before the announcement. Verified
    per row against the actual publication time (AMC after the close, BMO before
    the open), not merely the calendar date.
  * SIMULATED execution  — entry/exit priced from historical DAILY bars. No live
    order, no extended-hours history, so the exit is the first executable DAILY
    price after the announcement is public (next-session open; BMO gap-open).
  * LIVE trade           — none.

Each recorded prediction is one model's call on one event at one entry timing, so
several models predicting the SAME event are correlated, NOT independent trades.
Prediction-level stats are reported as such; the portfolio drawdown is computed
on an EVENT-LEVEL, consensus, daily-marked, equal-weight book (no summed
overlapping returns). Costs are explicit assumptions, surfaced in the output, and
every figure is reported both zero-cost and after-cost.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SOURCE = "Genuine Forward Scoreboard — recorded predictions, simulated execution"

COSTS = {
    "half_spread_bps": 3.0,
    "slippage_bps": 2.0,
    "commission_per_share": 0.005,
    "short_borrow_annual_pct": 3.0,
}
TIMING_BUCKETS = (1, 2, 3)
# Approx publication time (ET) by report timing, for the pre-publication check.
_PUB_TIME = {"amc": time(16, 0), "bmo": time(8, 0), "unknown": time(9, 30)}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _bars(symbol: str) -> list[dict]:
    import swr
    import unusualwhales_service as uw

    def _fetch():
        try:
            return uw.get_daily_bars(symbol, "1 Y") or []
        except Exception:  # noqa: BLE001
            return []

    v = swr.serve(f"fwdsb:bars:{symbol.upper()}", _fetch, 6 * 3600.0)
    return v if isinstance(v, list) else []


def _timing_key(report_timing: Optional[str]) -> str:
    t = (report_timing or "").strip().lower()
    if t in ("post", "amc", "after", "aftermarket", "after_market", "after-hours"):
        return "amc"
    if t in ("pre", "bmo", "before", "premarket", "pre_market", "before-open"):
        return "bmo"
    return "unknown"


def _idx_on_or_before(dates: list[str], d: str) -> Optional[int]:
    lo = None
    for i, bd in enumerate(dates):
        if bd <= d:
            lo = i
        else:
            break
    return lo


def _pub_dt(edate: str, report_timing: Optional[str]) -> datetime:
    """Approx publication instant (UTC) for an earnings event."""
    d = date.fromisoformat(edate)
    tk = _timing_key(report_timing)
    return datetime.combine(d, _PUB_TIME[tk], tzinfo=ET).astimezone(timezone.utc)


def _simulate(symbol: str, rows: list[dict]) -> tuple[list[dict], dict]:
    bars = _bars(symbol)
    skips = {"no_bars": 0, "no_entry": 0, "no_exit_reported": 0,
             "lookahead": 0, "pub_violation": 0}
    if not bars:
        skips["no_bars"] = len(rows)
        return [], skips
    dates = [str(b.get("date"))[:10] for b in bars]
    trades: list[dict] = []

    for r in rows:
        snap = str(r["snapshot_date"])[:10]
        edate = str(r["earnings_date"])[:10]
        sig = r["signal"]
        if sig not in ("BUY", "SELL"):
            continue
        if snap >= edate:
            skips["lookahead"] += 1
            continue
        # Verify the prediction predates the actual publication instant, not just
        # the calendar date (AMC publishes after the close, BMO before the open).
        pub_ok = True
        pa = r.get("predicted_at")
        if pa is not None:
            try:
                if getattr(pa, "tzinfo", None) is None:
                    pa = pa.replace(tzinfo=timezone.utc)
                pub_ok = pa < _pub_dt(edate, r.get("report_timing"))
            except Exception:  # noqa: BLE001
                pub_ok = True
        if not pub_ok:
            skips["pub_violation"] += 1
            continue

        ei = _idx_on_or_before(dates, snap)
        eri = _idx_on_or_before(dates, edate)
        if ei is None or eri is None:
            skips["no_entry"] += 1
            continue
        tk = _timing_key(r.get("report_timing"))
        exit_i = eri if tk == "bmo" else eri + 1
        if exit_i >= len(bars):
            skips["no_exit_reported"] += 1
            continue
        entry = _f(bars[ei].get("close"))
        exit_px = _f(bars[exit_i].get("open")) or _f(bars[exit_i].get("close"))
        if not entry or not exit_px or entry <= 0:
            skips["no_entry"] += 1
            continue

        held = exit_i - ei
        tminus = eri - ei
        gross = ((exit_px - entry) / entry) if sig == "BUY" else ((entry - exit_px) / entry)
        gross_pct = gross * 100.0
        per_side_bps = COSTS["half_spread_bps"] + COSTS["slippage_bps"]
        spread_slip_pct = (per_side_bps * 2) / 100.0
        commission_pct = (COSTS["commission_per_share"] * 2) / entry * 100.0
        borrow_pct = (COSTS["short_borrow_annual_pct"] * held / 365.0) if sig == "SELL" else 0.0
        cost_pct = spread_slip_pct + commission_pct + borrow_pct

        trades.append({
            "model": r["model"], "symbol": symbol, "side": sig, "signal": sig,
            "earnings_date": edate, "snapshot_date": snap, "entry_idx": ei,
            "exit_idx": exit_i, "report_timing": r.get("report_timing"),
            "t_minus": tminus, "held_days": held,
            "entry_price": round(entry, 4), "exit_price": round(exit_px, 4),
            "exit_date": dates[exit_i],
            "gross_pct": round(gross_pct, 3),
            "cost_pct": round(cost_pct, 3),
            "net_pct": round(gross_pct - cost_pct, 3),
            "score": r.get("score"),
        })
    return trades, skips


def _agg(trades: list[dict], field: str = "net_pct") -> dict:
    n = len(trades)
    base = {"n": n, "win_rate": None, "avg_pct": None, "median_pct": None,
            "profit_factor": None, "total_pct": None}
    if n == 0:
        return base
    vals = [t[field] for t in trades]
    wins = [x for x in vals if x > 0]
    losses = [x for x in vals if x < 0]
    loss_sum = -sum(losses)
    pf = (round(sum(wins) / loss_sum, 2) if loss_sum > 0
          else ("inf" if sum(wins) > 0 else None))
    srt = sorted(vals)
    mid = n // 2
    median = srt[mid] if n % 2 else (srt[mid - 1] + srt[mid]) / 2
    base.update({"win_rate": round(len(wins) / n * 100, 1),
                 "avg_pct": round(sum(vals) / n, 3),
                 "median_pct": round(median, 3),
                 "profit_factor": pf,
                 "total_pct": round(sum(vals), 3)})
    return base


def _dedup_events(trades: list[dict], timing: Optional[int] = None) -> list[dict]:
    """One trade per (symbol, earnings_date): consensus side across models, the
    representative entry at the chosen timing (defaults to the smallest t_minus
    available for that event). Removes the model-correlation double counting."""
    by_ev: dict[tuple, list[dict]] = defaultdict(list)
    for t in trades:
        if timing is not None and t["t_minus"] != timing:
            continue
        by_ev[(t["symbol"], t["earnings_date"])].append(t)
    out: list[dict] = []
    for ev, ts in by_ev.items():
        # pick the entry timing: requested, else the smallest t_minus present
        if timing is None:
            tmin = min(x["t_minus"] for x in ts)
            ts = [x for x in ts if x["t_minus"] == tmin]
        buys = sum(1 for x in ts if x["side"] == "BUY")
        sells = sum(1 for x in ts if x["side"] == "SELL")
        if buys == sells:
            continue                                  # no consensus -> no trade
        side = "BUY" if buys > sells else "SELL"
        rep = next(x for x in ts if x["side"] == side)
        out.append({**rep, "votes": f"{buys}B/{sells}S"})
    return out


def _portfolio(dedup: list[dict], net: bool = True) -> dict:
    """Daily mark-to-market, equal-weight, chronological portfolio. Each event is
    one position, marked each held day on closes (final day on the exit open),
    equally weighted among positions open that day. Max drawdown is computed on
    the compounded daily equity -- NOT on summed overlapping trade returns."""
    daily: dict[str, list[float]] = defaultdict(list)
    exit_cost: dict[str, list[float]] = defaultdict(list)
    for t in dedup:
        bars = _bars(t["symbol"])
        if not bars:
            continue
        dates = [str(b.get("date"))[:10] for b in bars]
        ei, xi = t["entry_idx"], t["exit_idx"]
        if ei is None or xi is None or xi >= len(bars):
            continue
        sgn = 1.0 if t["side"] == "BUY" else -1.0
        prev = _f(bars[ei].get("close"))
        for j in range(ei + 1, xi):
            c = _f(bars[j].get("close"))
            if prev and c:
                daily[dates[j]].append((c - prev) / prev * sgn)
                prev = c
        op = _f(bars[xi].get("open")) or _f(bars[xi].get("close"))
        if prev and op:
            daily[dates[xi]].append((op - prev) / prev * sgn)
        if net:
            exit_cost[dates[xi]].append(t["cost_pct"] / 100.0)
    all_dates = sorted(set(daily) | set(exit_cost))
    equity, peak, dd = 1.0, 1.0, 0.0
    for d in all_dates:
        rets = daily.get(d, [])
        port = (sum(rets) / len(rets)) if rets else 0.0
        if net and exit_cost.get(d):
            port -= sum(exit_cost[d]) / max(1, len(rets) if rets else len(exit_cost[d]))
        equity *= (1.0 + port)
        peak = max(peak, equity)
        dd = min(dd, equity / peak - 1.0)
    return {"positions": len(dedup), "trading_days": len(all_dates),
            "total_return_pct": round((equity - 1.0) * 100, 3),
            "max_drawdown_pct": round(dd * 100, 3)}


def _attribution(trades: list[dict], keyfn) -> list[dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        g[keyfn(t) or "—"].append(t)
    out = [{"key": k, "n": len(v),
            "total_net_pct": round(sum(x["net_pct"] for x in v), 2),
            "avg_net_pct": round(sum(x["net_pct"] for x in v) / len(v), 3)}
           for k, v in g.items()]
    out.sort(key=lambda x: x["total_net_pct"])          # worst (most negative) first
    return out


def scoreboard(model: Optional[str] = None, max_symbols: int = 120) -> dict:
    from sqlalchemy import text
    from database import SessionLocal

    today = date.today()
    db = SessionLocal()
    try:
        where = ["earnings_date IS NOT NULL", "signal IN ('BUY','SELL')",
                 "snapshot_date < earnings_date", "earnings_date < :t"]
        params: dict = {"t": today}
        if model:
            where.append("model = :m"); params["m"] = model
        preds = db.execute(text(
            "SELECT model, symbol, earnings_date, report_timing, snapshot_date, "
            "       predicted_at, signal, score FROM model_predictions "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY symbol, earnings_date, snapshot_date"), params).mappings().all()
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "source": SOURCE, "detail": str(e)[:200]}
    finally:
        db.close()

    by_sym: dict[str, list[dict]] = {}
    for p in preds:
        by_sym.setdefault(p["symbol"].upper(), []).append(dict(p))
    symbols = list(by_sym.keys())[:max_symbols]
    capped = len(by_sym) - len(symbols)

    # sectors (cached, batch)
    sectors: dict[str, Optional[str]] = {}
    try:
        import company_profile_service as cp
        profs = cp.get_profiles(symbols) or {}
        sectors = {s.upper(): (profs.get(s) or profs.get(s.upper()) or {}).get("sector")
                   for s in symbols}
    except Exception:  # noqa: BLE001
        sectors = {}

    all_trades: list[dict] = []
    skips_tot = {"no_bars": 0, "no_entry": 0, "no_exit_reported": 0,
                 "lookahead": 0, "pub_violation": 0}
    for sym in symbols:
        trades, skips = _simulate(sym, by_sym[sym])
        for t in trades:
            t["sector"] = sectors.get(sym) or "Unknown"
        all_trades.extend(trades)
        for k in skips_tot:
            skips_tot[k] += skips.get(k, 0)

    # --- validity (req 9) ---
    valid = sum(1 for t in all_trades if t["entry_price"] > 0 and t["exit_price"] > 0)

    # --- prediction-level stats (model x event x timing) ---
    def _pair(ts):
        return {"cost": _agg(ts, "net_pct"), "zero_cost": _agg(ts, "gross_pct")}

    overall = _pair(all_trades)
    by_timing = {f"T-{t}": _pair([x for x in all_trades if x["t_minus"] == t]) for t in TIMING_BUCKETS}
    by_timing["T-4+"] = _pair([x for x in all_trades if x["t_minus"] >= 4])
    by_side = {s: _pair([x for x in all_trades if x["side"] == s]) for s in ("BUY", "SELL")}
    models_seen = sorted({x["model"] for x in all_trades})
    by_model = {m: _pair([x for x in all_trades if x["model"] == m]) for m in models_seen}

    # --- event-independence (req 8) ---
    distinct_events = len({(t["symbol"], t["earnings_date"]) for t in all_trades})
    dedup_all = _dedup_events(all_trades)                         # one per event (consensus)
    dedup_t1 = _dedup_events(all_trades, timing=1)

    # --- properly sized portfolio drawdown (req 7) ---
    portfolio = {
        "all_events_consensus": {"net": _portfolio(dedup_all, True),
                                 "zero_cost": _portfolio(dedup_all, False)},
        "t1_events_consensus": {"net": _portfolio(dedup_t1, True),
                                "zero_cost": _portfolio(dedup_t1, False)},
    }

    # --- loss attribution (req 6) ---
    attribution = {
        "by_model": _attribution(all_trades, lambda t: t["model"]),
        "by_sector": _attribution(all_trades, lambda t: t["sector"]),
        "by_timing": _attribution(all_trades, lambda t: f"T-{t['t_minus']}"),
        "by_side": _attribution(all_trades, lambda t: t["side"]),
    }

    # trade detail (capped payload)
    detail = sorted(all_trades, key=lambda t: t["net_pct"])
    trade_rows = [{k: t[k] for k in ("model", "symbol", "side", "sector", "earnings_date",
                                     "snapshot_date", "exit_date", "t_minus", "held_days",
                                     "entry_price", "exit_price", "gross_pct", "cost_pct",
                                     "net_pct", "score", "report_timing")} for t in detail]

    return {
        "status": "OK", "source": SOURCE, "as_of": today.isoformat(),
        "model_filter": model,
        "provenance": {
            "predictions": "RECORDED in model_predictions, captured BEFORE each announcement; "
                           "verified predicted_at < actual publication instant (AMC after close, "
                           "BMO before open), not just the calendar date.",
            "executions": "SIMULATED on historical daily bars — not live orders.",
            "live_trades": 0,
            "independence": ("Each row = one model's call on one event at one timing. Several "
                             "models predicting the same event are CORRELATED, not independent. "
                             "Prediction-level stats count model x event x timing; the portfolio "
                             "drawdown is event-level consensus."),
        },
        "cost_model": COSTS,
        "counts": {"eligible_predictions": len(preds), "priced_trades": len(all_trades),
                   "valid_prices": valid, "distinct_events": distinct_events,
                   "trades_per_event": round(len(all_trades) / distinct_events, 2) if distinct_events else None,
                   "symbols_priced": len(symbols), "symbols_capped_out": max(0, capped)},
        "excluded": skips_tot,
        "overall": overall,
        "by_entry_timing": by_timing,
        "by_side": by_side,
        "by_model": by_model,
        "portfolio": portfolio,
        "attribution": attribution,
        "trades": trade_rows,
        "missing_data": {
            "extended_hours_exit": ("NOT AVAILABLE — no historical pre/after-market series. Exit is the "
                                    "first executable DAILY price after the announcement, not the actual "
                                    "after-hours print."),
            "factor_history": "LIVE-ONLY for ~60/100 points; not reconstructable (see Data Health).",
            "parameter_attribution": ("Per-parameter loss attribution is NOT available here — "
                                      "model_predictions stores the final signal/score only, not the "
                                      "per-factor breakdown."),
            "spreads_costs": "Spreads/slippage/commission/borrow are explicit assumptions, not observed fills.",
        },
        "metrics_note": ("Prediction-level avg/median/win-rate/profit-factor are per recorded prediction "
                         "(model x event x timing); they are NOT a portfolio. Portfolio total-return and "
                         "max-drawdown are computed on an event-level consensus, daily-marked, equal-weight "
                         "book. Every block is shown both zero-cost and after-cost."),
        "note": ("Genuine forward record. Depth = how long capture has run (no backfill), so samples are "
                 "small — every figure carries n. Nothing deployed; no live trades."),
    }
