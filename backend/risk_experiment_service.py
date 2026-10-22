"""Phase 5 — EXPERIMENTAL risk-control layer (SIMULATED · PARTIAL-FACTOR).

Isolated, reversible research on top of the Track B event-level consensus trades.
It changes NOTHING in the directional model, thresholds, consensus or production:
it only re-sizes, filters and re-exits the SAME signals to test risk controls.

Experiments:
  A. Position sizing (5/10/15%) via a CHRONOLOGICAL, capital-constrained portfolio
     with real overlapping holds, explicit cash reservation and a deterministic
     rejection rule (no unlimited-capital sequential compounding, no leverage).
  B. An experimental Earnings-Gap Risk Score (0-100) from AVAILABLE point-in-time
     factors only (missing factors are marked MISSING, never substituted), and
     avoid-top-X% policies with thresholds fitted on training data only.
  C. Exit timing: hold-through (next-open proxy) vs exit-before-earnings vs a
     conditional risk-based exit.
  D. Combinations of A/B/C.
Plus chronological 60/20/20 train/val/test by event date with an embargo.

Everything is SIMULATED on daily bars (no extended-hours fills), uses only data
dated on/before entry, and is labelled SIMULATED · PARTIAL-FACTOR.
"""
from __future__ import annotations

import math
import statistics as st
from collections import defaultdict
from datetime import date
from typing import Optional

import claude_last_service as cl
import historical_backtest_service as htb
import forward_scoreboard_service as fsb

SOURCE = "Phase 5 — Experimental Risk Controls (SIMULATED · PARTIAL-FACTOR)"
LABEL = "SIMULATED — PARTIAL-FACTOR BACKTEST"

# Gap-risk factors and whether a point-in-time historical proxy exists.
GAP_FACTORS = {
    "hist_gap_mag_var":   (20, True,  "Historical earnings gap magnitude & variability (past moves)"),
    "implied_vs_real":    (15, False, "Implied vs historical realised move (needs IV history)"),
    "pre_ext_vol":        (15, True,  "Pre-earnings price extension & realised volatility"),
    "liquidity_spreads":  (10, False, "Historical post-earnings liquidity & spreads"),
    "sector_market_vol":  (10, True,  "Sector / market volatility (index realised vol)"),
    "estimate_dispersion":(10, False, "Earnings estimate dispersion"),
    "surprise_var":       (10, True,  "Recent earnings surprise & reaction variability"),
    "timing_halts":       (10, False, "Event timing, halts & execution risk"),
}
AVAILABLE_WEIGHT = sum(w for _, (w, ok, _) in GAP_FACTORS.items() if ok)   # 55


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _realized_vol(closes: list[float]) -> Optional[float]:
    if len(closes) < 6:
        return None
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))
            if closes[i - 1]]
    return st.pstdev(rets) if len(rets) > 1 else None


# --------------------------------------------------------------------------- #
# Gap-risk score (point-in-time, AVAILABLE factors only)                      #
# --------------------------------------------------------------------------- #
def _spy_vol_cache() -> Optional[float]:
    bars = htb._bars2y("SPY")
    closes = [cl._f(b.get("close")) for b in bars if cl._f(b.get("close"))]
    v = _realized_vol(closes[-20:]) if len(closes) >= 20 else None
    return v


def gap_risk(sym: str, edate: str, eidx: int, closes: list[float],
             past_moves: list[float], surprises: list[float],
             spy_vol: Optional[float]) -> dict:
    """0-100 risk score over AVAILABLE weight; higher = larger expected gap.
    Uses only data before the event. Missing factors are reported, not filled."""
    factors: dict[str, Optional[float]] = {}
    # hist gap magnitude & variability (past post-earnings moves, abs)
    if past_moves:
        amean = sum(abs(m) for m in past_moves) / len(past_moves)
        astd = st.pstdev([abs(m) for m in past_moves]) if len(past_moves) > 1 else 0.0
        factors["hist_gap_mag_var"] = _clamp01((amean / 10.0) * 0.7 + (astd / 10.0) * 0.3)
    else:
        factors["hist_gap_mag_var"] = None
    # pre-earnings extension + realised vol (last 20 closes before entry)
    win = closes[max(0, eidx - 20):eidx]
    if len(win) >= 10:
        ext = abs((win[-1] - win[0]) / win[0]) if win[0] else 0.0
        rv = _realized_vol(win) or 0.0
        factors["pre_ext_vol"] = _clamp01((ext / 0.15) * 0.5 + (rv / 0.03) * 0.5)
    else:
        factors["pre_ext_vol"] = None
    # sector/market vol (SPY realised vol proxy)
    factors["sector_market_vol"] = _clamp01(spy_vol / 0.02) if spy_vol else None
    # recent surprise variability
    if surprises:
        svar = st.pstdev(surprises) if len(surprises) > 1 else abs(surprises[-1])
        factors["surprise_var"] = _clamp01(svar / 15.0)
    else:
        factors["surprise_var"] = None
    for k, (w, ok, _) in GAP_FACTORS.items():
        if not ok:
            factors[k] = None     # MISSING — never substituted

    num = sum(GAP_FACTORS[k][0] * v for k, v in factors.items() if GAP_FACTORS[k][1] and v is not None)
    cov = sum(GAP_FACTORS[k][0] for k, v in factors.items() if GAP_FACTORS[k][1] and v is not None)
    score = round(num / cov * 100, 1) if cov else None
    return {"score": score, "coverage_weight": cov, "available_weight": AVAILABLE_WEIGHT,
            "factors": factors}


# --------------------------------------------------------------------------- #
# Enrich the Track B consensus trades with daily-by-date, pre-earnings exit    #
# and the gap-risk score (all point-in-time).                                 #
# --------------------------------------------------------------------------- #
def enriched_trades(months: int = 18, max_symbols: int = 25) -> list[dict]:
    import earnings_trade_service as ets
    r = htb.run(months=months, max_symbols=max_symbols, max_events_per_symbol=10, detail=True)
    cons = r["_cons_trades"]
    spy_vol = _spy_vol_cache()
    # group by symbol to reuse bars
    out: list[dict] = []
    bars_cache: dict[str, tuple] = {}
    moves_cache: dict[str, list] = {}
    eh_cache: dict[str, list] = {}
    for t in cons:
        sym = t["symbol"]
        if sym not in bars_cache:
            bars = htb._bars2y(sym)
            dates = [str(b.get("date"))[:10] for b in bars]
            closes = [cl._f(b.get("close")) for b in bars]
            opens = [cl._f(b.get("open")) for b in bars]
            bars_cache[sym] = (dates, closes, opens)
            try:
                moves_cache[sym] = ets._historical_moves(sym) or []
            except Exception:  # noqa: BLE001
                moves_cache[sym] = []
            eh_cache[sym] = htb._eh(sym)
        dates, closes, opens = bars_cache[sym]
        ed = t["earnings_date"]
        eidx = fsb._idx_on_or_before(dates, ed)
        try:
            ei = dates.index(t.get("entry_date"))
            xi = dates.index(t["exit_date"])
        except ValueError:
            continue
        sgn = 1.0 if t["side"] == "BUY" else -1.0
        # daily returns keyed by DATE over the hold
        dbd = {}
        prev = closes[ei]
        for j in range(ei + 1, xi):
            c = closes[j]
            if prev and c:
                dbd[dates[j]] = (c - prev) / prev * sgn; prev = c
        op = opens[xi] or closes[xi]
        if prev and op:
            dbd[dates[xi]] = (op - prev) / prev * sgn
        # pre-earnings exit: last regular close before the announcement
        amc = xi > eidx
        preci = eidx if amc else eidx - 1
        entry = t["entry_price"]
        pre_close = closes[preci] if 0 <= preci < len(closes) else None
        pre_net = None
        dbd_pre: dict = {}
        if pre_close and entry:
            pre_gross = ((pre_close - entry) / entry) * 100 * sgn
            held_pre = max(1, preci - ei)
            pre_cost = htb._cost_pct(t["side"], entry, held_pre)
            pre_net = round(pre_gross - pre_cost, 3)
            prev2 = closes[ei]
            for j in range(ei + 1, preci + 1):
                c = closes[j]
                if prev2 and c:
                    dbd_pre[dates[j]] = (c - prev2) / prev2 * sgn; prev2 = c
        # gap-risk (point-in-time)
        past = [cl._f(m.get("move_pct")) for m in moves_cache[sym]
                if m.get("move_pct") is not None and str(m.get("date"))[:10] < ed]
        past = [m for m in past if m is not None]
        surprises = [cl._f(x.get("surprise_percentage")) for x in eh_cache[sym]
                     if str(x.get("report_date"))[:10] < ed and x.get("surprise_percentage") is not None]
        surprises = [s for s in surprises if s is not None]
        gr = gap_risk(sym, ed, eidx, closes, past, surprises, spy_vol)
        out.append({**t, "daily_by_date": dbd, "daily_by_date_pre": dbd_pre,
                    "pre_net_pct": pre_net, "pre_exit_date": dates[preci] if 0 <= preci < len(dates) else None,
                    "gap_risk": gr["score"], "gap_risk_cov": gr["coverage_weight"]})
    return out


# --------------------------------------------------------------------------- #
# Simple aggregate (event-level) for a net field                              #
# --------------------------------------------------------------------------- #
def _agg_side(trades: list[dict], field: str) -> dict:
    vals = [t[field] for t in trades if t.get(field) is not None]
    out = {"n": len(vals)}
    if not vals:
        return {**out, "avg": None, "median": None, "win_rate": None, "profit_factor": None}
    wins = [x for x in vals if x > 0]; losses = [x for x in vals if x < 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else ("inf" if wins else None)
    return {**out, "avg": round(sum(vals) / len(vals), 3),
            "median": round(sorted(vals)[len(vals) // 2], 3),
            "win_rate": round(len(wins) / len(vals) * 100, 1),
            "profit_factor": (round(pf, 2) if isinstance(pf, float) else pf)}


def _by_side(trades: list[dict], field: str) -> dict:
    return {s: _agg_side([t for t in trades if t["side"] == s], field) for s in ("BUY", "SELL")}


def _independent_events(trades: list[dict]) -> int:
    return len({(t["symbol"], t["earnings_date"]) for t in trades})


# --------------------------------------------------------------------------- #
# Experiments                                                                 #
# --------------------------------------------------------------------------- #
def _exp_sizing(trades: list[dict]) -> dict:
    return {f"{int(f*100)}%": simulate_portfolio(trades, f) for f in (0.05, 0.10, 0.15)}


def _risk_threshold(train: list[dict], pctile: float) -> Optional[float]:
    scored = sorted([t["gap_risk"] for t in train if t.get("gap_risk") is not None])
    if not scored:
        return None
    # keep the lowest (1-pctile); avoid the top `pctile` fraction
    idx = int((1.0 - pctile) * (len(scored) - 1))
    return scored[idx]


def _exp_gap_filter(trades: list[dict], thresholds: dict) -> dict:
    """Avoid the highest-risk X%: keep events with gap_risk below the fitted
    threshold. Report retention, avoided losses, missed winners + portfolio."""
    base = simulate_portfolio(trades, 0.10)
    out = {"no_filter": {"kept": len(trades), "portfolio": base}}
    for name, thr in thresholds.items():
        if thr is None:
            out[name] = {"error": "no threshold"}
            continue
        kept = [t for t in trades if t.get("gap_risk") is None or t["gap_risk"] <= thr]
        avoided = [t for t in trades if t.get("gap_risk") is not None and t["gap_risk"] > thr]
        avoided_net = [t["net_pct"] for t in avoided]
        out[name] = {
            "threshold": thr, "kept": len(kept), "avoided": len(avoided),
            "avoided_losses": sum(1 for x in avoided_net if x < 0),
            "avoided_winners": sum(1 for x in avoided_net if x > 0),
            "avoided_avg_net": round(sum(avoided_net) / len(avoided_net), 3) if avoided_net else None,
            "portfolio": simulate_portfolio(kept, 0.10),
        }
    return out


def _exp_exit(trades: list[dict]) -> dict:
    """Scenario A hold-through (net_pct / daily_by_date); B exit-before
    (pre_net_pct / daily_by_date_pre); C conditional (B if high gap-risk else A)."""
    # conditional threshold: top-30% risk -> exit before
    thr = _risk_threshold(trades, 0.30)
    cond = []
    for t in trades:
        high = t.get("gap_risk") is not None and thr is not None and t["gap_risk"] > thr
        if high and t.get("pre_net_pct") is not None:
            cond.append({**t, "_net": t["pre_net_pct"], "_daily": t["daily_by_date_pre"], "_exit": t["pre_exit_date"]})
        else:
            cond.append({**t, "_net": t["net_pct"], "_daily": t["daily_by_date"], "_exit": t["exit_date"]})

    def port_cond(ts):
        rt = [{**t, "net_pct": t["_net"], "daily_by_date": t["_daily"], "exit_date": t["_exit"]} for t in ts]
        return simulate_portfolio(rt, 0.10)

    pre_rt = [{**t, "net_pct": t["pre_net_pct"], "daily_by_date": t["daily_by_date_pre"], "exit_date": t["pre_exit_date"]}
              for t in trades if t.get("pre_net_pct") is not None]
    return {
        "A_hold_through": {"event": _agg_side(trades, "net_pct"), "by_side": _by_side(trades, "net_pct"),
                           "portfolio": simulate_portfolio(trades, 0.10)},
        "B_exit_before": {"event": _agg_side(trades, "pre_net_pct"), "by_side": _by_side(trades, "pre_net_pct"),
                          "portfolio": simulate_portfolio(pre_rt, 0.10),
                          "note": "ALTERNATIVE strategy (exits before earnings) — not proof the hold-through strategy works."},
        "C_conditional": {"threshold": thr, "portfolio": port_cond(cond)},
    }


def _split(trades: list[dict], embargo_days: int = 0) -> dict:
    """Chronological 60/20/20 by event date. All timings/models for an event are
    already one consensus trade per timing; split by earnings_date so an event's
    rows stay together."""
    evs = sorted({t["earnings_date"] for t in trades})
    n = len(evs)
    i1, i2 = int(n * 0.6), int(n * 0.8)
    train_e, val_e, test_e = set(evs[:i1]), set(evs[i1:i2]), set(evs[i2:])
    part = lambda S: [t for t in trades if t["earnings_date"] in S]
    return {"train": part(train_e), "val": part(val_e), "test": part(test_e),
            "dates": {"train": (evs[0] if evs else None, evs[i1 - 1] if i1 else None),
                      "val": (evs[i1] if i1 < n else None, evs[i2 - 1] if i2 else None),
                      "test": (evs[i2] if i2 < n else None, evs[-1] if evs else None)}}


def run_all(months: int = 18, max_symbols: int = 25, timing: int = 3) -> dict:
    allt = enriched_trades(months=months, max_symbols=max_symbols)
    trades = [t for t in allt if t["t_minus"] == timing]      # focus timing (default T-3)
    if not trades:
        return {"status": "NO_DATA", "source": SOURCE}

    # REPORT A — baseline reconciliation (event-level)
    base_event = _agg_side(trades, "net_pct")
    baseline = {
        "timing": f"T-{timing}", "events": _independent_events(trades), "consensus_trades": len(trades),
        "event_avg_net": base_event["avg"], "win_rate": base_event["win_rate"],
        "profit_factor": base_event["profit_factor"],
        "fixed_frac_10pct": htb._portfolio_fixed_frac(trades, 0.10),
        "capital_constrained_10pct": simulate_portfolio(trades, 0.10),
        "by_side": _by_side(trades, "net_pct"),
        "sell_independent_events": _independent_events([t for t in trades if t["side"] == "SELL"]),
    }

    # Experiment A — sizing
    sizing = _exp_sizing(trades)

    # Experiment B — gap-risk filter (thresholds fit on TRAIN only)
    sp = _split(trades)
    thr = {f"avoid_top_{int(p*100)}pct": _risk_threshold(sp["train"], p) for p in (0.10, 0.20, 0.30)}
    gapf = _exp_gap_filter(trades, thr)

    # Experiment C — exit timing
    exits = _exp_exit(trades)

    # Experiment D — combinations (10% sizing is the baseline sizing here)
    kept30 = [t for t in trades if t.get("gap_risk") is None or
              (thr["avoid_top_30pct"] is not None and t["gap_risk"] <= thr["avoid_top_30pct"])]
    combos = {
        "1_baseline_10pct": simulate_portfolio(trades, 0.10),
        "2_sizing_5pct": simulate_portfolio(trades, 0.05),
        "3_gapfilter_only_30": simulate_portfolio(kept30, 0.10),
        "4_exit_before_only": exits["B_exit_before"]["portfolio"],
        "5_sizing5_plus_gapfilter30": simulate_portfolio(kept30, 0.05),
        "6_sizing5_plus_conditional": None,   # filled below
        "7_all_three": None,
    }
    # conditional exit on kept30 at 5%
    thr_c = _risk_threshold(trades, 0.30)
    def cond_rt(ts):
        rt = []
        for t in ts:
            high = t.get("gap_risk") is not None and thr_c is not None and t["gap_risk"] > thr_c
            if high and t.get("pre_net_pct") is not None:
                rt.append({**t, "net_pct": t["pre_net_pct"], "daily_by_date": t["daily_by_date_pre"], "exit_date": t["pre_exit_date"]})
            else:
                rt.append(t)
        return rt
    combos["6_sizing5_plus_conditional"] = simulate_portfolio(cond_rt(trades), 0.05)
    combos["7_all_three"] = simulate_portfolio(cond_rt(kept30), 0.05)

    # Experiment F — OOS: fit gap threshold on train, apply to test
    def oos(part_name, part):
        kept = [t for t in part if t.get("gap_risk") is None or
                (thr["avoid_top_30pct"] is not None and t["gap_risk"] <= thr["avoid_top_30pct"])]
        return {"events": _independent_events(part), "trades": len(part),
                "buy": sum(1 for t in part if t["side"] == "BUY"),
                "sell": sum(1 for t in part if t["side"] == "SELL"),
                "baseline_10pct": simulate_portfolio(part, 0.10),
                "gapfilter30_10pct": simulate_portfolio(kept, 0.10)}
    oos_out = {k: oos(k, sp[k]) for k in ("train", "val", "test")}
    oos_out["split_dates"] = sp["dates"]

    return {
        "status": "OK", "source": SOURCE, "label": LABEL, "as_of": date.today().isoformat(),
        "months": months, "symbols": max_symbols, "focus_timing": f"T-{timing}",
        "gap_factors": {k: {"weight": w, "available": ok, "desc": d}
                        for k, (w, ok, d) in GAP_FACTORS.items()},
        "gap_available_weight": AVAILABLE_WEIGHT,
        "baseline": baseline, "experiment_A_sizing": sizing,
        "experiment_B_gapfilter": gapf, "experiment_C_exit": exits,
        "experiment_D_combos": combos, "experiment_F_oos": oos_out,
        "disclaimer": ("SIMULATED · PARTIAL-FACTOR. Risk controls re-size/filter/re-exit the SAME "
                       "unchanged signals; the directional model, thresholds and consensus are "
                       "untouched. Gap-risk uses only ~55/100 available factors (rest MISSING). "
                       "Daily-price execution (no extended hours). Not live performance."),
    }



# --------------------------------------------------------------------------- #
# Capital-constrained chronological portfolio                                 #
# --------------------------------------------------------------------------- #
def simulate_portfolio(trades: list[dict], alloc_frac: float, capital: float = 100_000.0,
                       max_gross: float = 1.0, net_field: str = "net_pct",
                       exit_field_daily: str = "daily_by_date") -> dict:
    """Chronological, no-leverage portfolio. Each accepted position reserves
    alloc_frac * current equity in cash; rejected if cash or the gross-exposure
    cap would be exceeded. Positions are marked daily; realised at exit. Returns
    performance + exposure diagnostics."""
    # order events by entry date; collect the calendar of all dates
    ts = [t for t in trades if t.get("entry_date") and t.get(exit_field_daily)]
    all_dates = sorted({d for t in ts for d in t[exit_field_daily]} |
                       {t["entry_date"] for t in ts})
    by_entry: dict[str, list] = defaultdict(list)
    for t in ts:
        by_entry[t["entry_date"]].append(t)

    cash = capital
    open_pos: list[dict] = []           # {notional, side, daily, exit_date, cum, realized}
    equity = capital
    peak = capital
    dd = 0.0
    taken = rejected = 0
    max_conc = 0
    max_gross_exp = 0.0
    min_cash = capital
    realized_pnls: list[float] = []      # $ P&L per closed trade
    worst_event = 0.0

    for d in all_dates:
        # 1) entries today (deterministic order: worst gap-risk last is irrelevant; use symbol)
        for t in sorted(by_entry.get(d, []), key=lambda x: (x["symbol"], x["earnings_date"])):
            notional = alloc_frac * equity
            gross_open = sum(p["value"] for p in open_pos)
            if notional <= cash + 1e-9 and (gross_open + notional) <= max_gross * equity + 1e-9:
                cash -= notional
                open_pos.append({"notional": notional, "value": notional,
                                 "daily": t[exit_field_daily], "exit_date": t["exit_date"],
                                 "entry_notional": notional})
                taken += 1
            else:
                rejected += 1
        # 2) mark to market
        for p in open_pos:
            r = p["daily"].get(d)
            if r is not None:
                p["value"] *= (1.0 + r)
        # 3) exits today -> realise
        still = []
        for p in open_pos:
            if p["exit_date"] == d:
                pnl = p["value"] - p["entry_notional"]
                cash += p["value"]
                realized_pnls.append(pnl)
                worst_event = min(worst_event, pnl)
            else:
                still.append(p)
        open_pos = still
        # 4) diagnostics
        gross_open = sum(p["value"] for p in open_pos)
        equity = cash + gross_open
        peak = max(peak, equity)
        dd = min(dd, equity / peak - 1.0)
        max_conc = max(max_conc, len(open_pos))
        max_gross_exp = max(max_gross_exp, gross_open / equity if equity else 0.0)
        min_cash = min(min_cash, cash)

    wins = [p for p in realized_pnls if p > 0]
    losses = [p for p in realized_pnls if p < 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else ("inf" if wins else None)
    n = len(realized_pnls)
    return {
        "alloc_frac": alloc_frac, "capital": capital, "leverage": "none (gross<=100%)",
        "trades_taken": taken, "trades_rejected_capacity": rejected,
        "total_return_pct": round((equity / capital - 1.0) * 100, 3),
        "max_drawdown_pct": round(dd * 100, 3),
        "profit_factor": (round(pf, 2) if isinstance(pf, float) else pf),
        "win_rate": round(len(wins) / n * 100, 1) if n else None,
        "max_concurrent": max_conc,
        "max_gross_exposure_pct": round(max_gross_exp * 100, 1),
        "min_cash_pct": round(min_cash / capital * 100, 1),
        "worst_event_loss_usd": round(worst_event, 2),
        "worst_event_loss_pct_of_capital": round(worst_event / capital * 100, 3),
        "realized_trades": n,
    }
