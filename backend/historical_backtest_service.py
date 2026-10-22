"""Track B — 1-2 year PARTIAL-FACTOR historical earnings backtest (SIMULATED).

What it is, honestly:
  * For each past earnings event it reconstructs, point-in-time (only data dated
    on/before the entry day T-k), the ONLY factors Unusual Whales retains
    historically: daily price action (EMA/RSI/trend) and the company's prior
    earnings-beat history. Options flow / IV / unusual activity / disparity /
    dark pool / short interest are NOT retained beyond ~15 days, so they are
    ABSENT (never back-filled) — this is why it is PARTIAL-FACTOR.
  * It runs the EXISTING model scorers UNMODIFIED on those partial inputs, forms
    an event-level consensus, and SIMULATES execution on historical daily bars:
    entry at the T-k close, exit at the first executable daily price after the
    announcement (AMC -> next-session open, BMO -> gap-open). Net of the same
    assumed costs as Track A.
  * Entries T-3 / T-2 / T-1 are evaluated separately; BUY and SELL independently.
    No look-ahead (only data < the entry/announcement), no duplicate-event
    counting (portfolio is event-level consensus). Publication timestamps are
    UNVERIFIED (assumed AMC 16:00 / BMO 08:00 ET).

No scoring weight is changed; nothing is deployed; no live trading. Results are
SIMULATED and never mixed with the genuine recorded predictions (Track A).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

import claude_last_service as cl
import earnings_backtest_service as ebt       # reuse reconstruction helpers + scorers
import forward_scoreboard_service as fsb       # reuse cost model, _agg, _timing_key

SOURCE = "Track B — PARTIAL-FACTOR historical earnings backtest (SIMULATED)"

TIMINGS = (1, 2, 3)
PRESENT_FACTORS = ["price_action", "ema_trend", "rsi", "prior_beats"]
MISSING_FACTORS = ["options_flow", "unusual_activity", "oi_positioning", "implied_volatility",
                   "expected_move", "disparity", "key_levels", "gamma/OI", "peer_readthrough",
                   "guidance (current-only)", "dark_pool", "eps/revenue revisions", "short_interest"]


def _bars2y(symbol: str) -> list[dict]:
    import swr
    import unusualwhales_service as uw

    def _f():
        try:
            return uw.get_daily_bars(symbol, "2 Y") or []
        except Exception:  # noqa: BLE001
            return []

    v = swr.serve(f"htbt:bars:{symbol.upper()}", _f, 24 * 3600.0)
    return v if isinstance(v, list) else []


def _eh(symbol: str) -> list[dict]:
    import swr
    import unusualwhales_service as uw

    def _f():
        try:
            return uw._rows(uw.earnings_history(symbol)) or []
        except Exception:  # noqa: BLE001
            return []

    v = swr.serve(f"htbt:eh:{symbol.upper()}", _f, 24 * 3600.0)
    return v if isinstance(v, list) else []


def _beats_bias(eh: list[dict], before_date: str) -> Optional[float]:
    prior = [r for r in eh if str(r.get("report_date"))[:10] < before_date
             and r.get("reported_eps") not in (None, "")]
    beats = tot = 0
    for r in prior[:8]:
        est = cl._f(r.get("estimated_eps"))
        act = cl._f(r.get("reported_eps"))
        if est is not None and act is not None:
            tot += 1
            beats += 1 if act >= est else 0
    return cl._clamp((beats / tot - 0.5) * 2) if tot else None


def _reconstruct(symbol: str, closes_upto: list[float], er_bias: Optional[float]):
    """Point-in-time eq/trade adapter from PRICE + PRIOR-BEATS only (partial)."""
    px = ebt._price_biases(closes_upto)
    signals = {"price_action": px["price_action"], "ema_trend": px["ema_trend"],
               "rsi": px["rsi"], "options_flow": None, "unusual_activity": None,
               "oi_positioning": None, "earnings_results": er_bias}
    params = [
        {"name": "price_action", "bias": px["price_action"]},
        {"name": "options_positioning", "bias": None},
        {"name": "unusual_options", "bias": None},
        {"name": "eps_estimates", "bias": None},
        {"name": "revenue_estimates", "bias": None},
        {"name": "guidance", "bias": er_bias},
        {"name": "peer_readthrough", "bias": None},
        {"name": "dark_pool", "bias": None},
        {"name": "disparity", "bias": None},
    ]
    eq_hist = {"params": params, "signals": signals, "short_interest": None,
               "score": None, "decision": None}
    trade_hist = {"historical_moves": [], "spark": None, "expected_move": {"percent": None}}
    return eq_hist, trade_hist


def _cost_pct(side: str, entry: float, held: int) -> float:
    c = fsb.COSTS
    per_side = c["half_spread_bps"] + c["slippage_bps"]
    spread_slip = (per_side * 2) / 100.0
    commission = (c["commission_per_share"] * 2) / entry * 100.0
    borrow = (c["short_borrow_annual_pct"] * held / 365.0) if side == "SELL" else 0.0
    return spread_slip + commission + borrow


def run(symbols: Optional[list[str]] = None, months: int = 18,
        max_symbols: int = 25, max_events_per_symbol: int = 10,
        detail: bool = False) -> dict:
    import ai_trade_service as ats

    if not symbols:
        symbols = list(getattr(ats, "UNIVERSE", []))
    syms = [s.upper() for s in symbols if s][:max_symbols]
    cutoff = (date.today() - timedelta(days=int(months * 30.4))).isoformat()
    today_s = date.today().isoformat()

    sectors: dict[str, Optional[str]] = {}
    try:
        import company_profile_service as cps
        profs = cps.get_profiles(syms) or {}
        sectors = {s: (profs.get(s) or {}).get("sector") or "Unknown" for s in syms}
    except Exception:  # noqa: BLE001
        sectors = {}

    model_trades: list[dict] = []        # one per (model, event, timing)
    cons_trades: list[dict] = []         # one per (event, timing) — consensus
    events_seen: set = set()
    skips = {"no_bars": 0, "no_outcome": 0, "no_entry": 0, "no_exit": 0, "no_signal": 0}

    for sym in syms:
        bars = _bars2y(sym)
        if len(bars) < 60:
            skips["no_bars"] += 1
            continue
        dates = [str(b.get("date"))[:10] for b in bars]
        closes = [cl._f(b.get("close")) for b in bars]
        opens = [cl._f(b.get("open")) for b in bars]
        eh = _eh(sym)
        timing_map = {str(r.get("report_date"))[:10]: (r.get("report_time") or "")
                      for r in eh if r.get("report_date")}
        edates = sorted({d for d in timing_map if cutoff <= d < today_s})[-max_events_per_symbol:]

        for ed in edates:
            # earnings session index = last bar on/before the report date
            eidx = fsb._idx_on_or_before(dates, ed)
            if eidx is None or eidx < 3:
                continue
            tk_amc = fsb._timing_key(timing_map.get(ed))
            exit_i = eidx if tk_amc == "bmo" else eidx + 1
            if exit_i >= len(bars):
                skips["no_exit"] += 1
                continue
            exit_px = opens[exit_i] or closes[exit_i]
            if not exit_px:
                skips["no_exit"] += 1
                continue
            er_bias = _beats_bias(eh, ed)
            events_seen.add((sym, ed))

            for k in TIMINGS:
                tki = eidx - k
                if tki < 20:
                    continue
                entry = closes[tki]
                if not entry or entry <= 0:
                    skips["no_entry"] += 1
                    continue
                # reconstruct the signal using ONLY closes up to the entry day
                eq_hist, trade_hist = _reconstruct(sym, [c for c in closes[:tki + 1] if c], er_bias)
                votes = {"BUY": 0, "SELL": 0}
                held = exit_i - tki
                for model, scorer in ebt._SCORERS.items():
                    try:
                        res = scorer(sym, eq_hist, trade_hist)
                        sig = res.get("raw_signal") or res.get("signal")
                    except Exception:  # noqa: BLE001
                        sig = None
                    if sig not in ("BUY", "SELL"):
                        continue
                    votes[sig] += 1
                    gross = ((exit_px - entry) / entry) if sig == "BUY" else ((entry - exit_px) / entry)
                    gp = gross * 100.0
                    cp = _cost_pct(sig, entry, held)
                    model_trades.append({
                        "model": model, "symbol": sym, "side": sig, "signal": sig,
                        "sector": sectors.get(sym, "Unknown"),
                        "earnings_date": ed, "t_minus": k, "held_days": held,
                        "entry_price": round(entry, 4), "exit_price": round(exit_px, 4),
                        "entry_date": dates[tki], "exit_date": dates[exit_i],
                        "gross_pct": round(gp, 3), "cost_pct": round(cp, 3),
                        "net_pct": round(gp - cp, 3),
                        "daily": _daily_rets(closes, opens, tki, exit_i, sig),
                    })
                # event-level consensus for this timing
                if votes["BUY"] == votes["SELL"]:
                    continue                                   # tie / no vote -> WAIT
                side = "BUY" if votes["BUY"] > votes["SELL"] else "SELL"
                gross = ((exit_px - entry) / entry) if side == "BUY" else ((entry - exit_px) / entry)
                gp = gross * 100.0
                cp = _cost_pct(side, entry, held)
                cons_trades.append({
                    "model": "consensus", "symbol": sym, "side": side,
                    "sector": sectors.get(sym, "Unknown"),
                    "earnings_date": ed, "t_minus": k, "held_days": held,
                    "entry_price": round(entry, 4), "exit_price": round(exit_px, 4),
                    "exit_date": dates[exit_i], "gross_pct": round(gp, 3),
                    "cost_pct": round(cp, 3), "net_pct": round(gp - cp, 3),
                    "votes": f"{votes['BUY']}B/{votes['SELL']}S",
                    "daily": _daily_rets(closes, opens, tki, exit_i, side),
                })

    return _assemble(model_trades, cons_trades, events_seen, syms, months, skips, detail)


def _daily_rets(closes, opens, ei, xi, side) -> list[tuple]:
    sgn = 1.0 if side == "BUY" else -1.0
    out = []
    prev = closes[ei]
    for j in range(ei + 1, xi):
        c = closes[j]
        if prev and c:
            out.append((j, (c - prev) / prev * sgn)); prev = c
    op = opens[xi] or closes[xi]
    if prev and op:
        out.append((xi, (op - prev) / prev * sgn))
    return out


def _portfolio(trades: list[dict], net: bool = True) -> dict:
    daily: dict[int, list[float]] = defaultdict(list)
    cost_on: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        for (idx, r) in t.get("daily", []):
            daily[idx].append(r)
        if net and t.get("daily"):
            cost_on[t["daily"][-1][0]].append(t["cost_pct"] / 100.0)
    eq, peak, dd = 1.0, 1.0, 0.0
    for idx in sorted(daily):
        rets = daily[idx]
        port = (sum(rets) / len(rets)) if rets else 0.0
        if net and cost_on.get(idx):
            port -= sum(cost_on[idx]) / max(1, len(rets))
        eq *= (1.0 + port)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1.0)
    return {"positions": len(trades), "trading_days": len(daily),
            "total_return_pct": round((eq - 1.0) * 100, 3),
            "max_drawdown_pct": round(dd * 100, 3)}


def _assemble(model_trades, cons_trades, events_seen, syms, months, skips, detail: bool = False) -> dict:
    def pair(ts):
        return {"cost": fsb._agg(ts, "net_pct"), "zero_cost": fsb._agg(ts, "gross_pct")}

    by_timing = {f"T-{k}": pair([t for t in model_trades if t["t_minus"] == k]) for k in TIMINGS}
    by_side = {s: pair([t for t in model_trades if t["side"] == s]) for s in ("BUY", "SELL")}
    models = sorted({t["model"] for t in model_trades})
    by_model = {m: pair([t for t in model_trades if t["model"] == m]) for m in models}
    # consensus distribution (the BUY bias check, reconstructed)
    cons_sides = {"BUY": sum(1 for t in cons_trades if t["side"] == "BUY"),
                  "SELL": sum(1 for t in cons_trades if t["side"] == "SELL")}
    port = {f"T-{k}": {"net": _portfolio([t for t in cons_trades if t["t_minus"] == k], True),
                       "zero_cost": _portfolio([t for t in cons_trades if t["t_minus"] == k], False)}
            for k in TIMINGS}
    port["all_timings"] = {"net": _portfolio(cons_trades, True),
                           "zero_cost": _portfolio(cons_trades, False)}
    # Realistic fixed-fractional portfolio (fixed % of equity per position, capped
    # concurrency, cash otherwise) -- the honest sizing, vs the fully-invested
    # equal-weight book above which amplifies when few names are open.
    port_realistic = {f"T-{k}": _portfolio_fixed_frac([t for t in cons_trades if t["t_minus"] == k])
                      for k in TIMINGS}
    port_realistic["all_timings"] = _portfolio_fixed_frac(cons_trades)

    out = {
        "status": "OK", "source": SOURCE, "label": "SIMULATED · PARTIAL-FACTOR",
        "as_of": date.today().isoformat(), "months": months, "symbols_tested": len(syms),
        "events_tested": len(events_seen),
        "model_predictions_count": len(model_trades),
        "consensus_trades": len(cons_trades),
        "consensus_side_distribution": cons_sides,
        "overall": pair(model_trades),
        "by_entry_timing": by_timing,
        "by_side": by_side,
        "by_model": by_model,
        "portfolio": port,
        "excluded": skips,
        "present_factors": PRESENT_FACTORS,
        "missing_factors": MISSING_FACTORS,
        "publication": {"classification": "TIMESTAMP_UNVERIFIED (all events)",
                        "reason": "Assumed AMC 16:00 / BMO 08:00 ET; the feed has no actual "
                                  "announcement timestamp. Entry uses only data on/before T-k."},
        "disclaimer": ("PARTIAL-FACTOR SIMULATION. Only price-action and prior-earnings-beat "
                       "history are reconstructable point-in-time (~30/100 of the model weight); "
                       "options flow, IV, unusual activity, disparity, dark pool, short interest, "
                       "guidance and estimate revisions are NOT retained historically and are "
                       "ABSENT. This is NOT the models' faithful historical accuracy, is never "
                       "mixed with the genuine recorded predictions, and is not live performance."),
        "metrics_note": ("Prediction-level = per model×event×timing (correlated). Portfolio = "
                         "event-level consensus, daily-marked, equal-weight. Costs: same as Track A."),
    }
    # Consensus-only prediction average per timing (reconciles vs the all-model avg).
    out["consensus_avg_net_by_timing"] = {
        f"T-{k}": fsb._agg([t for t in cons_trades if t["t_minus"] == k], "net_pct")
        for k in TIMINGS}
    out["portfolio_realistic"] = port_realistic
    # Loss attribution by sector (model-level trades).
    sect: dict[str, list] = defaultdict(list)
    for t in model_trades:
        sect[t.get("sector", "Unknown")].append(t["net_pct"])
    out["by_sector"] = sorted(
        [{"sector": k, "n": len(v), "avg_net_pct": round(sum(v) / len(v), 3),
          "total_net_pct": round(sum(v), 2)} for k, v in sect.items()],
        key=lambda x: x["total_net_pct"])
    if detail:
        out["_model_trades"] = model_trades
        out["_cons_trades"] = cons_trades
    return out


def _portfolio_fixed_frac(trades: list[dict], frac: float = 0.1, start: float = 1.0) -> dict:
    """Realistic sizing: each position risks a FIXED fraction of current equity,
    realized at exit, chronological by exit date; at most 1/frac concurrent
    (extra signals that would exceed full investment are skipped, not leveraged).
    Max drawdown is on the realized equity curve. This avoids the fully-invested
    equal-weight book's amplification when only a few names are open."""
    ts = sorted([t for t in trades if t.get("exit_date")], key=lambda t: t["exit_date"])
    max_concurrent = max(1, int(round(1.0 / frac)))
    equity, peak, dd = start, start, 0.0
    taken = skipped = 0
    # Approximate concurrency with a simple cap on open count by entry/exit window.
    open_until: list[str] = []
    for t in ts:
        ed_in = t.get("entry_date") or t["exit_date"]
        open_until = [x for x in open_until if x > ed_in]     # free up closed slots
        if len(open_until) >= max_concurrent:
            skipped += 1
            continue
        open_until.append(t["exit_date"])
        equity *= (1.0 + frac * (t["net_pct"] / 100.0))
        peak = max(peak, equity)
        dd = min(dd, equity / peak - 1.0)
        taken += 1
    return {"positions_taken": taken, "positions_skipped_capacity": skipped,
            "frac_per_trade": frac, "max_concurrent": max_concurrent,
            "total_return_pct": round((equity / start - 1.0) * 100, 3),
            "max_drawdown_pct": round(dd * 100, 3)}
