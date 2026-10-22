"""Genuine Forward Scoreboard (Track A) — after-cost performance of the REAL
recorded pre-earnings predictions.

This reads the genuine recorded predictions in `model_predictions` (captured
BEFORE each earnings announcement, never back-dated) and simulates the execution
of each directional call on actual historical daily prices, net of costs. It is
honest about what it is:

  * RECORDED prediction  — a real BUY/SELL made before the announcement
    (`snapshot_date < earnings_date`, verified per row; look-ahead rows excluded).
  * SIMULATED execution  — entry/exit priced from historical DAILY bars. There is
    NO live order here and NO extended-hours history, so the exit is the first
    realistically executable DAILY price AFTER the announcement is public (the
    next regular-session open; the gap-open for a BMO report). The exact
    post-announcement after-hours print cannot be modelled and is disclosed.
  * LIVE trade           — none. This module never trades.

Nothing after the prediction timestamp enters the entry decision. Costs
(spread, slippage, commission, short-borrow) are explicit assumptions, surfaced
in the output so every net number is auditable. Results are reported overall and
separately for T-3 / T-2 / T-1 entries.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

SOURCE = "Genuine Forward Scoreboard — recorded predictions, simulated execution"

# --- Cost model (assumptions, not fills). Surfaced in the output. ------------
COSTS = {
    "half_spread_bps": 3.0,        # half the bid/ask spread, each side
    "slippage_bps": 2.0,           # execution slippage, each side
    "commission_per_share": 0.005, # $ per share, each side
    "assumed_share_price_note": "commission is converted to % using the actual entry price",
    "short_borrow_annual_pct": 3.0,  # annualised borrow cost applied to SELLs for the holding period
}
# Entry-timing buckets we report separately (trading days before the earnings date).
TIMING_BUCKETS = (1, 2, 3)


def _bars(symbol: str) -> list[dict]:
    """Historical daily bars, oldest-first, lightly cached. Live fetch from UW."""
    import swr
    import unusualwhales_service as uw

    def _f():
        try:
            return uw.get_daily_bars(symbol, "1 Y") or []
        except Exception:  # noqa: BLE001
            return []

    v = swr.serve(f"fwdsb:bars:{symbol.upper()}", _f, 6 * 3600.0)
    return v if isinstance(v, list) else []


def _is_amc(report_timing: Optional[str]) -> Optional[bool]:
    """True = after market close (AMC), False = before open (BMO), None = unknown."""
    t = (report_timing or "").strip().lower()
    if not t:
        return None
    if t in ("post", "amc", "after", "aftermarket", "after_market", "after-hours"):
        return True
    if t in ("pre", "bmo", "before", "premarket", "pre_market", "before-open"):
        return False
    return None


def _idx_on_or_before(dates: list[str], d: str) -> Optional[int]:
    """Index of the last bar whose date <= d."""
    lo = None
    for i, bd in enumerate(dates):
        if bd <= d:
            lo = i
        else:
            break
    return lo


def _simulate(symbol: str, rows: list[dict]) -> tuple[list[dict], dict]:
    """Price every recorded directional prediction for one symbol. Returns
    (trades, skips) where trades carry the net-of-cost return."""
    bars = _bars(symbol)
    skips = {"no_bars": 0, "no_entry": 0, "no_exit_reported": 0, "lookahead": 0}
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
        if snap >= edate:                       # verify prediction predates the event
            skips["lookahead"] += 1
            continue
        ei = _idx_on_or_before(dates, snap)     # entry = close on the prediction day
        eri = _idx_on_or_before(dates, edate)   # the earnings session
        if ei is None or eri is None:
            skips["no_entry"] += 1
            continue
        amc = _is_amc(r.get("report_timing"))
        # First realistically executable exit AFTER the announcement is public:
        #   AMC  -> report lands after the earnings-day close -> next session open
        #   BMO  -> report lands at the earnings-day open     -> that session open
        #   unknown -> next session open (conservative; guarantees it's public)
        exit_i = eri if amc is False else eri + 1
        if exit_i >= len(bars):
            skips["no_exit_reported"] += 1       # reaction session hasn't traded yet
            continue
        entry = _f(bars[ei].get("close"))
        exit_px = _f(bars[exit_i].get("open")) or _f(bars[exit_i].get("close"))
        if not entry or not exit_px or entry <= 0:
            skips["no_entry"] += 1
            continue

        held = exit_i - ei                       # trading days held
        tminus = eri - ei                        # trading days before earnings (entry timing)
        gross = ((exit_px - entry) / entry) if sig == "BUY" else ((entry - exit_px) / entry)
        gross_pct = gross * 100.0

        # --- costs, all in % of notional ---
        per_side_bps = COSTS["half_spread_bps"] + COSTS["slippage_bps"]
        spread_slip_pct = (per_side_bps * 2) / 100.0            # bps -> %, both sides
        commission_pct = (COSTS["commission_per_share"] * 2) / entry * 100.0
        borrow_pct = (COSTS["short_borrow_annual_pct"] * held / 365.0) if sig == "SELL" else 0.0
        cost_pct = spread_slip_pct + commission_pct + borrow_pct
        net_pct = gross_pct - cost_pct

        trades.append({
            "model": r["model"], "symbol": symbol, "signal": sig,
            "earnings_date": edate, "snapshot_date": snap,
            "report_timing": r.get("report_timing"),
            "t_minus": tminus, "held_days": held,
            "entry_price": round(entry, 4), "exit_price": round(exit_px, 4),
            "exit_date": dates[exit_i],
            "gross_pct": round(gross_pct, 3),
            "cost_pct": round(cost_pct, 3),
            "net_pct": round(net_pct, 3),
            "score": r.get("score"),
            "provenance": {"prediction": "RECORDED (pre-announcement)",
                           "execution": "SIMULATED (historical daily bars)",
                           "live": False},
        })
    return trades, skips


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _agg(trades: list[dict]) -> dict:
    """Net-of-cost performance metrics for a set of simulated trades."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_net_pct": None, "median_net_pct": None,
                "profit_factor": None, "max_drawdown_pct": None, "total_net_pct": None}
    nets = [t["net_pct"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gains_sum = sum(wins)
    loss_sum = -sum(losses)
    pf = (gains_sum / loss_sum) if loss_sum > 0 else (None if gains_sum == 0 else float("inf"))
    srt = sorted(nets)
    mid = n // 2
    median = srt[mid] if n % 2 else (srt[mid - 1] + srt[mid]) / 2
    # equity curve ordered by exit date -> max drawdown of cumulative net %
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["exit_date"]):
        eq += t["net_pct"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "n": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_net_pct": round(sum(nets) / n, 3),
        "median_net_pct": round(median, 3),
        "profit_factor": (round(pf, 2) if pf not in (None, float("inf")) else ("inf" if pf == float("inf") else None)),
        "max_drawdown_pct": round(dd, 3),
        "total_net_pct": round(sum(nets), 3),
    }


def scoreboard(model: Optional[str] = None, max_symbols: int = 80) -> dict:
    """After-cost forward scoreboard over the genuine recorded predictions.

    `model` filters to one model key (else every model that has history). Prices
    are fetched once per distinct reported symbol (capped)."""
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
            "       signal, score FROM model_predictions "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY symbol, earnings_date, snapshot_date"), params).mappings().all()
    except Exception as e:  # noqa: BLE001
        db.close()
        return {"status": "ERROR", "source": SOURCE, "detail": str(e)[:200]}
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    # group by symbol, cap the number of distinct symbols we price
    by_sym: dict[str, list[dict]] = {}
    for p in preds:
        by_sym.setdefault(p["symbol"].upper(), []).append(dict(p))
    symbols = list(by_sym.keys())[:max_symbols]
    capped = len(by_sym) - len(symbols)

    all_trades: list[dict] = []
    skips_tot = {"no_bars": 0, "no_entry": 0, "no_exit_reported": 0, "lookahead": 0}
    for sym in symbols:
        trades, skips = _simulate(sym, by_sym[sym])
        all_trades.extend(trades)
        for k in skips_tot:
            skips_tot[k] += skips.get(k, 0)

    # overall + per entry-timing bucket + per model
    overall = _agg(all_trades)
    by_timing = {}
    for t in TIMING_BUCKETS:
        by_timing[f"T-{t}"] = _agg([x for x in all_trades if x["t_minus"] == t])
    by_timing["T-4+"] = _agg([x for x in all_trades if x["t_minus"] >= 4])
    models_seen = sorted({x["model"] for x in all_trades})
    by_model = {m: _agg([x for x in all_trades if x["model"] == m]) for m in models_seen}

    return {
        "status": "OK", "source": SOURCE, "as_of": today.isoformat(),
        "model_filter": model,
        "provenance": {
            "predictions": "RECORDED in model_predictions, captured BEFORE each "
                           "announcement (snapshot_date < earnings_date verified per row).",
            "executions": "SIMULATED on historical daily bars — not live orders.",
            "live_trades": 0,
        },
        "cost_model": COSTS,
        "overall": overall,
        "by_entry_timing": by_timing,
        "by_model": by_model,
        "eligible_predictions": len(preds),
        "priced_trades": len(all_trades),
        "symbols_priced": len(symbols),
        "symbols_capped_out": max(0, capped),
        "excluded": skips_tot,
        "missing_data": {
            "extended_hours_exit": ("NOT AVAILABLE — no historical pre/after-market series. "
                                    "Exit is the first executable DAILY price after the "
                                    "announcement (next-session open; BMO gap-open), NOT the "
                                    "actual post-announcement after-hours print."),
            "factor_history": ("LIVE-ONLY for ~60/100 points (IV, options flow, unusual, "
                               "disparity, dark pool, short interest, guidance, estimates). "
                               "Not reconstructable; see Data Health."),
            "spreads_costs": "Spreads/slippage/commission/borrow are explicit assumptions (cost_model), not observed fills.",
        },
        "trades": all_trades,
        "metrics_note": ("avg/median/win-rate are per-trade, net of costs. total_net_pct is the "
                         "SUM of per-trade net % (equal-weight, trades may overlap in time — not a "
                         "position-sized portfolio return); max_drawdown_pct is the drawdown of that "
                         "cumulative sum ordered by exit date. profit_factor = gross gains ÷ gross losses."),
        "note": ("Genuine forward record: real recorded predictions, executions SIMULATED "
                 "on daily bars net of assumed costs. Depth equals how long capture has "
                 "run (no backfill), so small samples are expected — every figure carries n."),
    }
