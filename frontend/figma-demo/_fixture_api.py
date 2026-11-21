"""A DEV-ONLY stand-in backend, so the redesigned pages can be built against populated screens.

WHY THIS EXISTS. The redesign shipped half-built because every panel rendered its empty state on my
machine: with no backend there is nothing to lay out, so "it looks fine" meant "it is blank, and the
blankness looks tidy". This serves the same JSON SHAPES the real API serves, with obviously-fake
values, so tables, charts and badges render and can be compared with the Figma frames.

IT MUST NEVER SHIP. It lives in figma-demo/, which no kit stages (see stage_kitm.py), it binds to
localhost only, and nothing imports it. Same rule as the demo folder: sample data is a tool for
looking at layout, never something a server returns to a person.

Run:  python _fixture_api.py      (serves 127.0.0.1:8000, which vite's dev proxy already targets)
"""
from __future__ import annotations

import io
import json
import random
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

random.seed(7)                                   # same screen every run, so a diff means something

SYMS = ["NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA", "GOOGL", "AMD", "AVGO", "NFLX",
        "JPM", "BAC", "XOM", "CVX", "KO", "PEP", "WMT", "COST", "LLY", "MRK"]
NAMES = {"NVDA": "NVIDIA Corp", "AAPL": "Apple Inc.", "MSFT": "Microsoft Corp", "AMZN": "Amazon.com",
         "META": "Meta Platforms", "TSLA": "Tesla, Inc.", "GOOGL": "Alphabet Inc.",
         "AMD": "Advanced Micro", "AVGO": "Broadcom Inc.", "NFLX": "Netflix, Inc.",
         "JPM": "JPMorgan Chase", "BAC": "Bank of America", "XOM": "Exxon Mobil",
         "CVX": "Chevron Corp.", "KO": "Coca-Cola Co.", "PEP": "PepsiCo, Inc.",
         "WMT": "Walmart Inc.", "COST": "Costco Wholesale", "LLY": "Eli Lilly", "MRK": "Merck & Co."}


def price(sym):
    return round(40 + (abs(hash(sym)) % 900) + random.random() * 9, 2)


def series(n, start, vol):
    out, v = [], start
    for _ in range(n):
        v += (random.random() - 0.48) * vol
        out.append(round(v, 2))
    return out


# --------------------------------------------------------------------------- IV crush (tab B)
def tab_b_scan():
    rows = []
    for i, s in enumerate(SYMS):
        pend = i % 7 == 6
        then = 38 + (i * 3.1) % 34
        now = then + (6 + (i * 2.7) % 26) * (1 if i % 5 else -1)
        rows.append({
            "symbol": s, "company": NAMES[s],
            "date": "2026-11-%02d" % (20 + i % 9),
            "date_label": "Nov %d %s" % (20 + i % 9, "BMO" if i % 2 else "AMC"),
            "expiry": "2026-12-19", "event_expiry": "2026-11-21",
            "status": "PENDING" if pend else "OK",
            # A leg is {"rate": mid, "iv": pct} - see earnings_options_service.now_leg/then_leg.
            # Numbers here instead of legs is what made every IV column read "-".
            "call_then": None if pend else {"rate": round(9.4 + i * 0.3, 2), "iv": round(then, 1)},
            "put_then": None if pend else {"rate": round(8.9 + i * 0.3, 2), "iv": round(then - 1.4, 1)},
            "call_now": None if pend else {"rate": round(12.7 + i * 0.3, 2), "iv": round(now, 1)},
            "put_now": None if pend else {"rate": round(11.9 + i * 0.3, 2), "iv": round(now - 1.1, 1)},
            "strike": 100 + i * 5, "spot": round(101.4 + i * 5.2, 2), "date_10d": "2026-11-05",
            "straddle": None if pend else round(4 + i * 0.37, 2),
            "atm_oi": 1200 + i * 317, "atm_spread_pct": round(0.4 + i * 0.07, 2),
            "atm_call_oi": 700 + i * 160, "atm_put_oi": 500 + i * 157,
            "result_code": ["UP", "DOWN", "FLAT", None][i % 4], "result_date": None,
            "s": s, "rd": None, "updated_at": "2026-11-19T12:00:00Z",
        })
    return {"status": "OK", "source": "FIXTURE", "rows": rows, "universe": len(rows),
            "missing": [], "refetch": []}


def option_chain(sym):
    spot = price(sym)
    atm = round(spot / 5) * 5
    rows = []
    for k in [atm + d * 5 for d in (-2, -1, 0, 1, 2, 3, 4)]:
        d = abs(k - atm) / max(atm, 1) * 100
        civ = round(68.4 - d * 2.1 + random.random() * 2, 1)
        piv = round(civ + 1.3, 1)
        rows.append({
            "strike": k, "atm": k == atm,
            "call": {"iv": civ, "iv_100": round(civ - 23.2, 1), "bid": round(12.6 - d, 2),
                     "ask": round(12.9 - d, 2), "last": round(12.7 - d, 2),
                     "volume": 900 - int(d * 30), "open_interest": 4100 - int(d * 90),
                     "delta": round(0.52 - d / 100, 2)},
            "put": {"iv": piv, "iv_100": round(piv - 21.8, 1), "bid": round(10.5 + d, 2),
                    "ask": round(10.9 + d, 2), "last": round(10.7 + d, 2),
                    "volume": 700 + int(d * 20), "open_interest": 3800 + int(d * 70),
                    "delta": round(-0.48 + d / 100, 2)},
        })
    # KEY IS "strikes", and iv is already a PERCENT - both copied from get_chain_table, not guessed.
    return {"status": "OK", "symbol": sym.upper(), "spot": spot, "expiry": "2026-12-19",
            "expiry_label": "19 Dec 2026", "dte": 30,
            "expiries": ["2026-11-21", "2026-12-19", "2027-01-16"],
            "atm_strike": atm, "strikes": rows}


def iv_history(sym):
    """Shape copied from live_options_service._provider_iv_history."""
    hist = series(30, 52.0, 3.4)
    return {"symbol": sym.upper(), "status": "OK", "source": "FIXTURE",
            "as_of": "2026-11-19", "iv": hist[-1],
            "iv_low": min(hist), "iv_high": max(hist), "iv_rank": 77, "iv_percentile": 63,
            "hv": round(hist[-1] - 11.2, 1), "samples": len(hist),
            "series": [{"date": "2026-10-%02d" % (d + 1), "iv": v} for d, v in enumerate(hist)]}


def option_metrics(sym):
    hist = series(30, 52.0, 3.4)
    return {"status": "OK", "symbol": sym.upper(), "iv_current": hist[-1],
            "iv_rank": 77, "iv_percentile": 63,
            "iv_history": [{"date": "2026-10-%02d" % (d + 1), "iv": v} for d, v in enumerate(hist)],
            "hv_30": round(hist[-1] - 11.2, 1),
            "past_earnings": [
                {"date": "2026-08-28", "iv_before": 72.1, "iv_after": 38.7, "crush": -33.4},
                {"date": "2026-05-22", "iv_before": 69.5, "iv_after": 34.9, "crush": -34.6},
                {"date": "2026-02-26", "iv_before": 63.5, "iv_after": 31.2, "crush": -32.3},
                {"date": "2025-11-20", "iv_before": 61.9, "iv_after": 30.2, "crush": -31.7}]}


# --------------------------------------------------------------------------- signal history
def signal_history(n=8):
    rows = []
    for i, sym in enumerate(SYMS):
        cells = []
        for k in range(n):
            mv = round((random.random() - 0.45) * 13, 2)
            cells.append({"date": "2026-%02d-%02d" % (1 + (k * 3) % 11, 5 + k),
                          "label": "Q%d" % (1 + k % 4), "move_pct": mv,
                          "signal": "BUY" if mv > 0.5 else "SELL" if mv < -0.5 else "NEUTRAL"})
        rows.append({"symbol": sym, "cells": cells, "events": n,
                     "now": ["BUY", "SELL", "NEUTRAL"][i % 3]})
    return {"status": "OK", "n": n, "source": "FIXTURE", "rows": rows}


# --------------------------------------------------------------------------- forward scoreboard
def _agg(n, wr, avg, med, pf, tot):
    return {"n": n, "win_rate": wr, "avg_pct": avg, "median_pct": med,
            "profit_factor": pf, "total_pct": tot}


def _pair(n, wr):
    return {"cost": _agg(n, wr, -0.22, -0.33, 0.39, round(-0.22 * n, 2)),
            "zero_cost": _agg(n, wr + 1.4, -0.08, -0.27, 0.46, round(-0.08 * n, 2))}


def forward_scoreboard():
    models = ["agent_a", "agent_b", "agent_c", "agent_d"]
    trades = []
    for i, sym in enumerate(SYMS[:14]):
        trades.append({
            "model": models[i % 4], "symbol": sym, "side": "BUY" if i % 3 else "SELL",
            "sector": ["Manufacturing", "Finance & Real Estate", "Services", "Mining & Energy"][i % 4],
            "earnings_date": "2026-10-%02d" % (10 + i), "t_minus": 1 + i % 4,
            "entry": round(100 + i * 3.4, 2), "exit": round(98 + i * 3.4, 2),
            "gross_pct": round(-0.4 - i * 0.11, 2), "cost_pct": -0.11,
            "net_pct": round(-0.51 - i * 0.11, 2), "score": 60 + i,
        })
    return {
        "status": "OK", "source": "FIXTURE", "as_of": "2026-11-19", "model_filter": None,
        "provenance": {"predictions": "RECORDED before each announcement (fixture).",
                       "executions": "SIMULATED on historical daily bars (fixture).",
                       "live_trades": 0, "independence": "Fixture copy."},
        "cost_model": {"commission_bps": 0.5, "slippage_bps": 3},
        "counts": {"eligible_predictions": 635, "priced_trades": 259, "valid_prices": 259,
                   "distinct_events": 39, "trades_per_event": 6.5, "symbols_priced": 37,
                   "symbols_capped_out": 0},
        "excluded": {"not_yet_reported": 282, "no_price": 12},
        "publication": {"verified": 0, "assumed": 39,
                        "note": "TIMESTAMP_UNVERIFIED (all events) - fixture."},
        "consensus": {"events": 39, "buy": 39, "sell": 0, "wait": 0,
                      "note": "BUY 39 - SELL 0 - WAIT 0 (fixture)."},
        "overall": _pair(259, 37.1),
        "by_entry_timing": {"T-1": _pair(231, 36.8), "T-2": _pair(94, 44.4),
                            "T-3": _pair(10, 30.0), "T-4+": _pair(44, 44.4)},
        "by_side": {"BUY": _pair(252, 36.9), "SELL": _pair(7, 42.9)},
        "by_model": {m: _pair(60 + i * 5, 44.0 + i) for i, m in enumerate(models)},
        # No curve exists in the real payload - only these four numbers, twice over.
        "portfolio": {
            "all_events_consensus": {
                "net": {"positions": 39, "trading_days": 60, "total_return_pct": -0.42,
                        "max_drawdown_pct": -2.56},
                "zero_cost": {"positions": 39, "trading_days": 60, "total_return_pct": -0.11,
                              "max_drawdown_pct": -2.18}},
            "t1_events_consensus": {
                "net": {"positions": 37, "trading_days": 40, "total_return_pct": -1.43,
                        "max_drawdown_pct": -2.14},
                "zero_cost": {"positions": 37, "trading_days": 40, "total_return_pct": -0.9,
                              "max_drawdown_pct": -1.76}}},
        "attribution": {
            "by_side": [{"key": "BUY", "n": 252, "total_net_pct": 411.35, "avg_net_pct": 1.63},
                        {"key": "SELL", "n": 7, "total_net_pct": 3.02, "avg_net_pct": 0.43}],
            "by_timing": [{"key": "T-1", "n": 231, "total_net_pct": -118.0, "avg_net_pct": -0.51},
                          {"key": "T-2", "n": 94, "total_net_pct": -42.3, "avg_net_pct": -0.45},
                          {"key": "T-3", "n": 10, "total_net_pct": -6.1, "avg_net_pct": -0.61}],
            "by_sector": [{"key": "Manufacturing", "n": 78, "total_net_pct": -226.05, "avg_net_pct": -2.9},
                          {"key": "Finance & Real Estate", "n": 44, "total_net_pct": -113.1, "avg_net_pct": -2.18},
                          {"key": "Services", "n": 52, "total_net_pct": -59.17, "avg_net_pct": -1.14},
                          {"key": "Mining & Energy", "n": 11, "total_net_pct": -44.27, "avg_net_pct": -4.02},
                          {"key": "Transport & Utilities", "n": 55, "total_net_pct": 12.48, "avg_net_pct": 0.23},
                          {"key": "Wholesale Trade", "n": 9, "total_net_pct": 15.75, "avg_net_pct": 1.75}],
            "by_model": [{"key": m, "n": 60 + i * 5, "total_net_pct": -20.0 - i, "avg_net_pct": -0.3}
                         for i, m in enumerate(models)],
        },
        "trades": trades,
        "missing_data": {
            "extended_hours_exit": "NOT AVAILABLE - no historical pre/after-market series (fixture).",
            "factor_history": "LIVE-ONLY for ~60/100 points; not reconstructable (fixture).",
            "parameter_attribution": "Per-parameter loss attribution is NOT available (fixture).",
            "spreads_costs": "Spreads/slippage/commission are assumptions, not observed fills.",
        },
        "metrics_note": "Prediction-level stats are per recorded prediction, not a portfolio (fixture).",
        "note": "Genuine forward record (fixture copy). Nothing deployed; no live trades.",
    }


# --------------------------------------------------------------------------- historical backtest
def historical_backtest():
    models = ["agent_a", "agent_b", "agent_c", "agent_d"]

    def book(tot, dd, pos):
        return {"net": {"positions": pos, "trading_days": 380, "total_return_pct": tot,
                        "max_drawdown_pct": dd},
                "zero_cost": {"positions": pos, "trading_days": 380,
                              "total_return_pct": round(tot + 4.2, 2),
                              "max_drawdown_pct": round(dd + 2.1, 2)}}

    def real(tot, dd, taken, skipped, conc):
        return {"positions_taken": taken, "positions_skipped_capacity": skipped,
                "frac_per_trade": 0.1, "max_concurrent": conc,
                "total_return_pct": tot, "max_drawdown_pct": dd}

    return {
        "status": "OK", "source": "FIXTURE", "as_of": "2026-11-19", "months": 18,
        "symbols_tested": 25, "events_tested": 101,
        "model_predictions_count": 1262, "consensus_trades": 252,
        "consensus_side_distribution": {"BUY": 242, "SELL": 10},
        "overall": _pair(1262, 47.3),
        "by_side": {"BUY": _pair(1229, 47.1), "SELL": _pair(33, 54.5)},
        "by_entry_timing": {"T-1": _pair(400, 42.0), "T-2": _pair(439, 47.8),
                            "T-3": _pair(423, 51.8)},
        "by_model": {m: _pair(160 + i * 15, 46.5 + i * 0.9) for i, m in enumerate(models)},
        "portfolio": {"T-1": book(-3.39, -11.07, 84), "T-2": book(-6.49, -9.04, 84),
                      "T-3": book(-2.47, -7.41, 84), "all_timings": book(-11.89, -25.1, 252)},
        "portfolio_realistic": {
            "T-1": real(-3.39, -11.07, 84, 6, 9), "T-2": real(-6.49, -9.04, 84, 4, 8),
            "T-3": real(-2.47, -7.41, 84, 3, 7),
            "all_timings": real(-11.89, -25.1, 252, 13, 14)},
        "excluded": {"no_price": 9, "no_prediction": 21},
        # A user gets counts with the lists emptied; an admin would get the names (A.29).
        "present_factor_count": 4, "missing_factor_count": 13, "factor_total": 17,
        "present_factors": [], "missing_factors": [],
        "publication": {"classification": "TIMESTAMP_UNVERIFIED (all events)",
                        "reason": "Assumed AMC 16:00 / BMO 08:00 ET; the feed has no actual "
                                  "announcement timestamp (fixture)."},
        "disclaimer": ("PARTIAL-FACTOR SIMULATION. Only price action and prior-earnings-beat history "
                       "are reconstructable point-in-time (~30/100 of the model weight); options flow, "
                       "IV, unusual activity, dark pool, short interest, guidance and estimate "
                       "revisions are NOT retained historically and are ABSENT (fixture)."),
        "metrics_note": ("Prediction-level = per model x event x timing (correlated). Portfolio = "
                         "event-level consensus, daily-marked, equal-weight (fixture)."),
    }


# --------------------------------------------------------------------------- strategy blotter
def strategy_trades():
    rows = []
    # 30, not 16: the frame's table is 1034 tall and its pager is drawn, and neither appears with
    # a handful of rows. The table has no fixed height by design - it follows the row count - so a
    # short fixture made the geometry harness report a 296px "difference" that was only missing
    # data. 30 rows is a realistic day and fills the frame's own height.
    for i, sym in enumerate((SYMS * 3)[:30]):
        side = "BUY" if i % 4 else "SELL"
        entry = round(60 + i * 11.3, 2)
        stop = round(entry * (0.975 if side == "BUY" else 1.025), 2)
        target = round(entry * (1.018 if side == "BUY" else 0.982), 2)
        last = round(entry * (1 + (0.6 - (i % 5) * 0.35) / 100), 2)
        shares = 10
        profit = round((last - entry) * shares * (1 if side == "BUY" else -1), 2)
        status = ["OPEN", "TARGET", "STOP", "SCORE_EXIT", "EOD"][i % 5]
        rows.append({
            "id": 1000 + i, "strategy": "agent_%d" % (1 + i % 4),
            "strategy_label": "Agent %d" % (1 + i % 4),
            "symbol": sym, "side": side, "shares": shares,
            "notional": round(entry * shares, 2),
            "entry_time": "10:%02d:00 AM" % (5 + i * 3),
            "entry_price": entry, "last_price": last,
            "chg": round(last - entry, 2),
            "stop": stop, "target": target, "be_moved": i % 3 == 0,
            "profit": profit,
            "profit_pct": round(profit / (entry * shares) * 100, 2),
            "status": status,
            "exit_reason_label": {"OPEN": "", "TARGET": "Target hit", "STOP": "Stop hit",
                                  "SCORE_EXIT": "Score fell", "EOD": "Closed at the bell"}[status],
            "score_at_entry": 60 + i, "exit_price": None if status == "OPEN" else last,
            "exit_basis": None if status == "OPEN" else "LEVEL", "gap_effect": None,
            "exit_date": None if status == "OPEN" else "2026-11-19",
        })
    total = round(sum(r["profit"] for r in rows), 2)
    notional = round(sum(r["notional"] for r in rows), 2)
    closed = sum(1 for r in rows if r["status"] != "OPEN")
    return {
        "status": "OK", "source": "FIXTURE", "date": "2026-11-19",
        "strategies": [{"key": "agent_%d" % k, "label": "Agent %d" % k} for k in (1, 2, 3, 4)],
        "rows": rows, "total_profit": total, "total_notional": notional,
        "total_pct": round(total / notional * 100, 2), "count": len(rows),
        "carried_open": [], "carried_open_count": 0, "gap_effect_total": 0.0,
        "realized_on_date": round(total * 0.6, 2), "closed_on_date": closed,
        "realized_note": "realized_on_date = P&L of positions CLOSED on this date (fixture).",
        "open": len(rows) - closed, "closed": closed,
        "wins": sum(1 for r in rows if r["profit"] > 0),
        "rules": {"notional_usd": 10000, "max_stop_pct": 2.5, "stop_cap_hits": 7,
                  "entry_window_et": ["09:40", "15:30"], "eod_close_et": "15:50",
                  "quote_max_age_s": 60, "breakeven_at": 0.4, "target_atr_mult": 0.5,
                  "short_market_filter": True, "breakeven_atr": 0.4, "score_drop_exit": 15,
                  "notional_usd_label": 10000, "short_market_proxies": ["SPY", "QQQ"],
                  "fill_model": "next-tick"},
        "rth": True, "after_close": False, "entries_open": True,
    }


# --------------------------------------------------------------------------- pre-earnings direction
SECTORS = ["Technology", "Healthcare", "Financials", "Consumer Cyclical", "Industrials", "Energy"]


def ped_scan(days=15):
    rows = []
    for i, sym in enumerate(SYMS):
        score = 30 + (i * 7) % 62
        signal = "BUY" if score >= 65 else "SELL" if score <= 35 else "NEUTRAL"
        rows.append({
            "symbol": sym, "company": NAMES[sym],
            "date": "2026-11-%02d" % (20 + i % 9),
            "date_label": "Nov %d" % (20 + i % 9),
            "time_label": "AMC" if i % 2 else "BMO",
            "days_to": 1 + i % 9,
            "score": score, "signal": signal,
            "tier": "A" if score >= 70 else "B" if score >= 50 else "C",
            "coverage": 70 + (i * 3) % 30,
            "raw_total": round(score * 0.9, 1), "max_total": 100,
            "params": [],                      # emptied for a user (A.29 / EMPTIED_KEYS)
            "sector": SECTORS[i % len(SECTORS)],
            "market_cap": 8_000_000_000 + i * 31_000_000_000,
            "prior_close": price(sym),
        })
    scored = [r for r in rows if r["score"] is not None]
    return {"status": "OK", "source": "FIXTURE", "rows": rows,
            "universe": len(rows), "scored": len(scored), "missing": []}


# --------------------------------------------------------------------------- unusual activity
def unusual_activity():
    part_a = []
    for i, sym in enumerate(SYMS[:14]):
        right = "P" if i % 3 == 0 else "C"      # the service emits a single letter: C / P
        vol = 1200 + i * 430
        avg = max(120, vol // (2 + i % 4))
        part_a.append({
            "symbol": sym, "expiry": "2026-12-%02d" % (5 + i),
            "strike": 100 + i * 25, "right": right,
            "volume": vol, "avg_volume": avg,
            "vol_mult": round(vol / avg, 1),
            "vol_oi": round(1.1 + i * 0.3, 1),
            "premium": 900000 + i * 410000,
            "side": ["BUY", "MIXED", "SELL"][i % 3],
            "bid_pct": 20 + i % 30, "ask_pct": 50 + i % 30,
        })
    part_b = []
    for i, sym in enumerate(SYMS):
        ratio = round(0.4 + (i * 0.37) % 4.2, 2)
        normal = round(0.9 + (i % 5) * 0.12, 2)
        disp = round(ratio / normal, 2)
        part_b.append({
            "symbol": sym, "call_premium": 1_200_000 + i * 310_000,
            "put_premium": 800_000 + i * 170_000,
            "ratio": ratio, "normal_ratio": normal, "disparity": disp,
            "leaning": "CALL HEAVY" if disp >= 2 else "PUT HEAVY" if disp <= 0.5 else "NORMAL",
            "hist_n": 20,
        })
    return {"status": "OK", "source": "FIXTURE", "asof": "2026-11-19",
            "universe": len(SYMS), "part_a": part_a, "part_b": part_b,
            "warm_occs": [], "warm_syms": [],
            "refresh": {"paused": False, "in_session": True, "standing_aside": False,
                        "hourly_used": 120, "hourly_cap": 1000, "essential": True,
                        "note": "Fixture: the tab may refresh."}}


# --------------------------------------------------------------------------- stocks
def stocks_all(limit=500):
    rows = []
    for i in range(min(limit, 120)):
        sym = SYMS[i % len(SYMS)] + ("" if i < len(SYMS) else str(i // len(SYMS)))
        rows.append({
            "symbol": sym, "name": NAMES.get(sym[:4], "Example Corp"),
            "price": price(sym), "change_percent": round((random.random() - 0.5) * 4, 2),
            "market_cap": 900_000_000 + i * 41_000_000_000,
            "volume": 1_900_000 + i * 2_100_000,
            "iv_rank": 10 + (i * 7) % 90,
        })
    return {"status": "OK", "rows": rows, "count": len(rows), "limit": limit}


# --------------------------------------------------------------------------- news + sentiment
HEADLINES = [
    ("Chipmaker lifts quarterly outlook on data-centre demand", "positive"),
    ("Regulator opens review of the proposed acquisition", "negative"),
    ("Company names a new chief financial officer", "neutral"),
    ("Quarterly revenue beats the consensus estimate", "positive"),
    ("Supplier flags a delay to the next hardware cycle", "negative"),
    ("Board approves an expanded buyback programme", "positive"),
    ("Analyst day set for the second week of December", "neutral"),
    ("Margins narrow as component costs rise", "negative"),
]


# 40, not 20: the live feed returns 40 and the frame pages at 20, so a 20-item fixture
# could never show the pager it is supposed to prove.
def news_for(sym="NVDA", n=40):
    items = []
    for i in range(n):
        head, tone = HEADLINES[i % len(HEADLINES)]
        items.append({
            "id": "fx-%d" % i, "headline": head, "summary": head + " (fixture).",
            "url": "https://example.invalid/%d" % i,
            "provider": ["Blockchain News", "Tradex", "Marketwire"][i % 3],
            "symbols": [sym], "time": "Nov 18 %02d:%02d" % (6 + i % 12, (i * 7) % 60),
            "time_label": "%dh ago" % (1 + i), "published_at": "2026-11-18T%02d:00:00Z" % (6 + i % 12),
            "sentiment": tone, "score": {"positive": 0.8, "negative": -0.7, "neutral": 0.0}[tone],
        })
    return {"status": "OK", "source": "FIXTURE", "symbol": sym,
            "items": items, "articles": items, "count": len(items)}


def sentiment_for(sym="NVDA"):
    return {"status": "OK", "source": "FIXTURE", "symbol": sym,
            "score": 81, "label": "Bullish", "confidence": "High",
            "positive": 28, "negative": 4, "neutral": 8, "scored": 8, "total": 28,
            "series": [{"date": "2026-11-%02d" % (10 + d), "score": 60 + d * 3} for d in range(8)]}


# --------------------------------------------------------------------------- market pulse
def market_pulse():
    sectors = [("Technology", 2.08), ("Consumer Cyclical", 0.26), ("Financials", 0.23),
               ("Industrials", 0.0), ("Communication Services", -0.06), ("Healthcare", -0.36),
               ("Utilities", None), ("Real Estate", None), ("Consumer Defensive", None),
               ("Energy", None), ("Materials", None)]
    gainers = [("BAC", 49.58, 0.42), ("COP", 139.44, 1.20), ("COST", 1399.00, 0.10),
               ("OXY", 56.41, 0.19), ("PG", 170.75, 0.56)]
    losers = [("KO", 98.62, -0.78), ("PEP", 140.96, -0.75), ("WMT", 126.76, -0.59),
              ("PG", 170.75, -0.56), ("OXY", 56.41, -1.95)]
    return {
        "status": "OK", "source": "FIXTURE",
        "market": {"is_open": False, "label": "Closed"},
        # IndexCard reads price/change/change_percent (not "value"), plus the proxy label.
        "indices": [{"label": l, "price": v, "change": c, "change_percent": pc,
                     "instrument": inst, "is_proxy": True, "index_scaled": True}
                    for l, v, c, pc, inst in [
                        ("S&P 500", 7376.0, 51.30, 0.70, "SPY"),
                        ("Nasdaq 100", 27303.0, 368.18, 1.37, "QQQ"),
                        ("Dow Jones", 50846.0, 115.00, 0.23, "DIA"),
                        ("Russell 2000", 2702.0, 11.00, 0.41, "IWM"),
                        ("VIX", 20.16, -0.85, -4.03, "VIXY")]],
        # SectorBars reads label + symbol, not "name".
        "sectors": [{"label": n, "symbol": "XL" + n[:1], "change_percent": v}
                    for n, v in sectors],
        # Heatmap wants {sector, average, members:[{symbol, change_percent}]} - read off the
        # component, not guessed. "names"/"change_percent" made it crash on undefined.map.
        "heatmap": [{"sector": n, "average": v or 0.0,
                     "members": [{"symbol": SYMS[(j * 3 + i) % len(SYMS)],
                                  "change_percent": round((random.random() - 0.5) * 2, 2)}
                                 for i in range(6)]} for j, (n, v) in enumerate(sectors[:6])],
        "breadth": {"advancing": 1, "declining": 6, "total": 53},
        "indicators": [{"label": "10Y Treasury", "value": 90.44, "change_percent": None},
                       {"label": "US Dollar", "value": 29.64, "change_percent": None},
                       {"label": "Gold", "value": 349.64, "change_percent": None},
                       {"label": "Crude Oil", "value": 140.19, "change_percent": None},
                       {"label": "Bitcoin", "value": 36.63, "change_percent": None}],
        "gainers": [{"symbol": s, "price": p2, "change_percent": c} for s, p2, c in gainers],
        "losers": [{"symbol": s, "price": p2, "change_percent": c} for s, p2, c in losers],
        "global": [{"label": "FTSE 100", "value": 46.94}, {"label": "DAX", "value": 41.66},
                   {"label": "Nikkei 225", "value": 90.35}, {"label": "Hang Seng", "value": 22.50},
                   {"label": "China A50", "value": 33.02}],
        # "value", not "score": market_pulse_service._sentiment returns {status, value, label,
        # components}. The fixture said "score", so the gauge read "no sentiment reading" locally
        # while the real server had a figure - a fixture that disagrees with the server hides the
        # very defect it exists to catch.
        "sentiment": {"status": "OK", "value": 38, "label": "Fear", "advancing_share": 1.9,
                      "volatility": 59.2, "options_tape": 52.3},
        "put_call": {"ratio": 0.986, "label": "BEARISH",
                     "call_premium": 23.81e9, "put_premium": 36.16e9},
        # The tide is its OWN endpoint (/api/market/tide), not part of the pulse payload.
    }


# --------------------------------------------------------------------------- quote
def quote_for(sym):
    """Shape from the Quote interface in client.ts."""
    pr = price(sym)
    ch = round((random.random() - 0.4) * 6, 2)
    return {"symbol": sym.upper(), "name": NAMES.get(sym.upper(), "Example Corp"),
            "exchange": "NASDAQ", "tags": [], "price": pr, "change": ch,
            "change_percent": round(ch / pr * 100, 2), "previous_close": round(pr - ch, 2),
            "bid": round(pr - 0.02, 2), "ask": round(pr + 0.02, 2),
            "open": round(pr - 1.1, 2), "high": round(pr + 2.3, 2),
            "low": round(pr - 2.6, 2), "close": pr,
            "volume": 28_980_000, "status": "OK", "source": "FIXTURE",
            "spark": series(24, pr, pr * 0.004)}


def market_tide():
    """MarketTide reads {date, series:[{time, net_call_premium, net_put_premium, spy}]}."""
    pts = []
    call = put = 0.0
    for i in range(80):
        call += (random.random() - 0.45) * 900_000
        put += (random.random() - 0.5) * 850_000
        pts.append({"time": "%02d:%02d" % (7 + i // 12, (i * 5) % 60),
                    "net_call_premium": round(call, 2),
                    "net_put_premium": round(-abs(put), 2),
                    "spy": round(7300 + i * 0.9, 2)})
    return {"status": "OK", "source": "FIXTURE", "date": "2026-11-17", "series": pts}


# --------------------------------------------------------------------------- smart move LIVE
SM_CODES = ["SELL", "NO_VALID_PREDICTION", "NO_TRADE", "BUY", "PENDING_REFRESH"]


def smart_move_live():
    rows = []
    for i, sym in enumerate(SYMS[:12]):
        code = SM_CODES[i % len(SM_CODES)]
        pend = code == "PENDING_REFRESH"
        rows.append({
            "symbol": sym, "company": NAMES[sym],
            "earnings_date": "2026-11-%02d" % (19 + i % 5),
            "session": "After Close" if i % 2 else "Before Open",
            "label": "LIVE", "signal_code": code,
            "bull": 0 if pend else 3 + i, "bear": 0 if pend else 36 - i,
            "coverage": "40/40" if i % 3 else "30/40",
            "adv": 49_000_000 + i * 9_000_000,
            "liquidity": "PASS" if i % 3 else "FAIL",
            "missing": [] if i % 3 else ["F7: NOT_CAPTURED_LIVE_IN_WINDOW"],
            "reason": "Fixture row - evidence agreed on the day.",
            "category_text": "Fixture category text.",
            "evidence_age_h": round(2.3 + i * 0.4, 1),
            "evidence_latest_et": "2026-11-17 23:32 EST",
            "window_cutoff_et": "2026-11-18 16:00 EST",
            "hours_to_cutoff": 6 + i,
            "generated_at_et": "2026-11-18 05:36 EST",
            "record_sha": "fixture%02d" % i,
            "mode": "LIVE", "smart": True,
            "evidence_warning": None,
            "snapshot_label": "LIVE pass",
        })
    return {
        "status": "OK", "source": "FIXTURE", "configured": True,
        "state": "LIVE", "kill_switch": False,
        "state_text": ("The last successful live scan is 133 minutes old. NO TRADE means every gate "
                       "passed and the margin was too small; NO VALID PREDICTION means data was "
                       "missing and is never a neutral view (fixture)."),
        "now_et": "2026-11-18 07:48 EST",
        "scan": {"last_successful_scan_et": "2026-11-18 05:36 EST", "minutes_since_success": 133,
                 "stale_after_min": 390, "mode": "LIVE"},
        "requests": {"used_today": 23, "daily_limit": 700, "remaining_today": 677, "run_limit": 500},
        "events": {"open_scanned": 25},
        "calendar": {"through": "2026-12-18", "age_h": 1.65},
        "ledger": {"ok": True, "problem": None},
        "rows": rows,
    }


def flow_summary():
    """Fields from market_flow_service.get_summary."""
    return {"status": "OK", "source": "FIXTURE",
            "call_premium": 23.81e9, "put_premium": 36.16e9,
            "total_premium": 59.97e9, "net_premium": -12.35e9,
            "call_premium_share": 39.7, "sentiment": "Bearish", "ratio_now": 0.986}


def flow_tape(limit=12):
    """Shape from /api/flow/market/tape as the live server returns it."""
    rows = []
    for i in range(limit):
        sym = SYMS[i % len(SYMS)]
        right = "P" if i % 3 == 0 else "C"
        rows.append({
            "symbol": sym, "option_symbol": "%s2611%02d%s" % (sym, 20 + i, right),
            "right": right, "type": "PUT" if right == "P" else "CALL",
            "strike": 100 + i * 25, "expiry": "2026-12-%02d" % (4 + i),
            "expiry_label": "2026-12-%02d" % (4 + i), "dte": 14 + i,
            "premium": 900_000 + i * 410_000, "notional": 900_000 + i * 410_000,
            "size": 200 + i * 40, "contracts": 200 + i * 40,
            "side": ["ask", "bid", "mid"][i % 3], "sweep": i % 4 == 0,
            "sentiment": "BULLISH" if right == "C" else "BEARISH",
            "volume": 1200 + i * 90, "open_interest": 4100 - i * 50,
        })
    return {"status": "OK", "source": "FIXTURE", "rows": rows, "count": len(rows)}


def earnings_upcoming(days=7):
    """Shape from /api/earnings/upcoming - eps_estimate present, eps_actual null until reported."""
    import datetime as _dt
    today = _dt.date.today()
    rows = []
    for i, sym in enumerate(SYMS):
        d = today + _dt.timedelta(days=i % days)
        rows.append({
            "symbol": sym, "company": NAMES[sym], "sector": SECTORS[i % len(SECTORS)],
            "date": d.isoformat(), "date_label": d.strftime("%a %b %d"),
            "reporting_time": "BMO" if i % 2 else "AMC",
            "timing_label": "Before Open" if i % 2 else "After Close",
            "short_label": "BMO" if i % 2 else "AMC",
            "eps_estimate": round(0.2 + i * 0.37, 2), "eps_actual": None,
            "revenue_estimate": None, "market_cap": 9_000_000_000 + i * 31_000_000_000,
            "importance": 3, "importance_label": "Listed", "has_options": True,
            "expected_move_percent": round(3.2 + i * 0.4, 2), "prior_close": price(sym),
            "data_status": "OK", "source": "FIXTURE",
        })
    return {"status": "OK", "rows": rows, "count": len(rows)}


# --------------------------------------------------------------------------- shell + misc
def indices():
    """Copied from what the LIVE server returns, not from what would look nice.

    The live payload is PARTIAL_DATA: three PROXY rows carrying the ETF's share price under an index
    label (SPY 710.99 for "S&P 500", whose real level is near 6,800), a null VIX, and NO price
    series at all. Fixturing a tidy "OK with sparklines" is exactly how the page shipped printing a
    proxy price as an index level.
    """
    return {"status": "PARTIAL_DATA", "indices": [
        {"label": "S&P 500", "value": 710.99, "change_percent": -0.68,
         "instrument": "SPY", "is_proxy": True},
        {"label": "NASDAQ", "value": 635.12, "change_percent": -1.54,
         "instrument": "QQQ", "is_proxy": True},
        {"label": "DOW", "value": 490.03, "change_percent": -0.15,
         "instrument": "DIA", "is_proxy": True},
        {"label": "VIX", "value": None, "change_percent": None,
         "instrument": None, "is_proxy": False},
    ]}


def movers():
    """No spark (the live feed has none) and a market cap, which the route now joins in."""
    return {"status": "OK", "count": len(SYMS), "source": "FIXTURE",
            "floor": {"min_price": 5.0, "min_market_cap": 2_000_000_000.0,
                      "dropped_below_price": 11, "dropped_below_market_cap": 7, "caps_known": 500},
            "rows": [
                {"symbol": s, "name": NAMES[s], "price": price(s),
                 "change_percent": round((random.random() - 0.45) * 5, 2),
                 "volume": 1000000 + i * 2370000, "sector": SECTORS[i % len(SECTORS)],
                 "market_cap": 9_000_000_000 + i * 180_000_000_000}
                for i, s in enumerate(SYMS)]}


def _bars(symbol, n=78, start_hour=9):
    """A plausible intraday series in the SERVER's shape: a list under "bars", each {time, close}.

    "bars" is the real key - live_market_service.get_chart returns it. The fixture used to say
    "points", which no page could read.
    """
    import math
    base = (quote_for(symbol) or {}).get("price") or 100.0
    out = []
    for i in range(n):
        drift = math.sin(i / 7.0) * (base * 0.004) + (i - n / 2) * (base * 0.00012)
        mins = start_hour * 60 + i * 5
        out.append({"time": f"{mins // 60:02d}:{mins % 60:02d}",
                    "close": round(base + drift, 2),
                    "open": round(base + drift, 2),
                    "high": round(base + drift + base * 0.001, 2),
                    "low": round(base + drift - base * 0.001, 2),
                    "volume": 100000 + i * 137})
    return out


def landing_featured():
    q = quote_for("NVDA")
    return {"status": "OK", "symbol": "NVDA", "quote": q,
            "chart": {"status": "OK", "bars": _bars("NVDA")},
            "technicals": {"rsi_14": 54.3, "atr_14": 7.54, "trend": "BULLISH"},
            "names_tracked": 500,
            "picked": "largest market cap among today's movers, else NVDA"}


ROUTES = {
    # NEWUI=0 in the environment serves new_ui False, so the CLASSIC shell can be checked for
    # regressions - the owner's rule is that it must look exactly as it does now.
    "/auth/me": {"authenticated": True, "username": "fixture", "is_admin": False,
                 "full_access": True, "role": "user",
                 "new_ui": str(__import__("os").getenv("NEWUI", "1")) != "0",
                 "mt5_operator": False},
    "/auth/status": {"status": "OK"},
    "/api/health": {"status": "OK", "database": "connected"},
    "/api/status": {"status": "OK", "market": {"is_open": False, "label": "Closed"}},
    "/api/signals": {"status": "OK", "rows": []},
}


# --------------------------------------------------------------------------- real payloads
# Responses captured from the PRODUCTION server through the owner's own signed-in browser (see
# _capture.md). When the file is present these are served verbatim, so the pages render against
# the figures production actually returned instead of invented ones. Absent, everything below
# behaves exactly as it did.

LIVE = {}
try:
    with io.open("_live_payloads.json", encoding="utf-8") as _f:
        LIVE = json.load(_f)
except Exception:  # noqa: BLE001 - no capture yet is the normal case
    LIVE = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):                                        # noqa: N802
        p = urlparse(self.path).path
        if p in ROUTES:
            body = ROUTES[p]
        elif p == "/api/tab-b/scan":
            body = tab_b_scan()
        elif p.startswith("/api/options/chain/"):
            body = option_chain(p.rsplit("/", 1)[-1])
        elif p.startswith("/api/options/iv-history/"):
            body = iv_history(p.rsplit("/", 1)[-1])
        elif p.startswith("/api/options/metrics/"):
            body = option_metrics(p.rsplit("/", 1)[-1])
        elif p.startswith("/api/quote/"):
            body = quote_for(p.rsplit("/", 1)[-1].split("?")[0])
        elif p == "/api/stocks/all":
            body = stocks_all()
        elif p == "/api/news/desk":
            body = news_for()
        elif p.startswith("/api/news/"):
            body = news_for(p.rsplit("/", 1)[-1].split("?")[0])
        elif p.startswith("/api/sentiment/"):
            body = sentiment_for(p.rsplit("/", 1)[-1])
        elif p == "/api/flow/market/summary":
            body = flow_summary()
        elif p == "/api/market/tide":
            body = market_tide()
        elif p == "/api/market/pulse":
            body = market_pulse()
        elif p == "/api/unusual-activity/scan":
            body = unusual_activity()
        elif p == "/api/pre-earnings-direction/scan":
            body = ped_scan()
        elif p == "/api/earnings-monitor/smart-move/live":
            body = smart_move_live()
        elif p == "/api/earnings-monitor/smart-move/alerts":
            body = {"status": "OK", "alerts": []}
        elif p == "/api/strategy-trades":
            body = strategy_trades()
        elif p == "/api/earnings/signal-history":
            body = signal_history()
        elif p == "/api/earnings/historical-backtest":
            body = historical_backtest()
        elif p == "/api/earnings/forward-scoreboard":
            body = forward_scoreboard()
        elif p == "/api/indices":
            body = indices()
        elif p.startswith("/api/flow/market/tape"):
            body = flow_tape()
        elif p.startswith("/api/earnings/upcoming"):
            body = earnings_upcoming()
        elif p == "/api/landing/featured":
            body = landing_featured()
        elif p.split("?")[0] in LIVE:
            # A captured production response wins over anything invented here.
            body = LIVE[p.split("?")[0]]
        elif p.startswith("/api/watchlist/search"):
            from urllib.parse import parse_qs
            # `p` is the PATH - do_GET strips the query - so read it from self.path.
            q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0].upper()
            pool = [("AAPL", "Apple Inc.", "Common Stock"), ("AMZN", "Amazon.com, Inc.", "Common Stock"),
                    ("ABNB", "Airbnb, Inc.", "Common Stock"), ("BABA", "Alibaba Group Holding", "ADR"),
                    ("TSM", "Taiwan Semiconductor", "ADR"), ("SHOP", "Shopify Inc.", "Common Stock"),
                    ("NVDA", "NVIDIA Corporation", "Common Stock"), ("ASML", "ASML Holding N.V.", "ADR")]
            m = [{"symbol": t, "name": n, "issue_type": i} for t, n, i in pool
                 if q and (t.startswith(q) or q in n.upper())]
            body = {"query": q, "matches": m[:10], "count": len(m[:10]), "status": "OK",
                    "issue_types": ["Common Stock", "ADR"], "universe_size": 5831,
                    "source": "SEC ticker registry + US common stock and ADRs (fixture)"}
        elif p == "/api/landing/sparklines":
            # Mirrors the server's two states so BOTH can be judged locally: SPARK=1 in the
            # environment serves series, anything else answers DISABLED exactly as the real
            # endpoint does until LANDING_SPARKLINES is set.
            import os
            if str(os.getenv("SPARK", "1")) != "0":
                # The union of the panel's three sorts, exactly as the server builds it. Seeding
                # from rows[:6] matched none of the tabs and every line came back empty.
                rows = [r for r in movers()["rows"] if r.get("symbol")]
                syms = ["SPY", "QQQ", "DIA", "VIXY"]
                for order in (sorted(rows, key=lambda r: r.get("volume") or 0, reverse=True),
                              sorted(rows, key=lambda r: r.get("change_percent") or 0, reverse=True),
                              sorted(rows, key=lambda r: r.get("change_percent") or 0)):
                    for r in order[:6]:
                        if r["symbol"] not in syms:
                            syms.append(r["symbol"])
                body = {"status": "OK",
                        "series": {s2: [b["close"] for b in _bars(s2)] for s2 in syms}}
            else:
                body = {"status": "DISABLED", "series": {},
                        "note": "LANDING_SPARKLINES is not set."}
        elif p.startswith("/api/chart/"):
            sym = p.rsplit("/", 1)[-1].split("?")[0].upper()
            body = {"status": "OK", "symbol": sym, "bars": _bars(sym)}
        elif p == "/api/movers":
            body = movers()
        elif p == "/api/tickers/strip":
            body = {"status": "OK", "rows": movers()["rows"][:12]}
        else:
            # Anything not modelled yet answers EMPTY-but-valid, never a 500: an unmodelled panel
            # should look unfinished, not crash the page and hide every panel below it.
            body = {"status": "OK", "rows": [], "items": [], "articles": [], "fixture": "unmodelled"}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_POST = do_GET


if __name__ == "__main__":
    print("FIXTURE API (dev only, fake values) on http://127.0.0.1:8000")
    HTTPServer(("127.0.0.1", 8000), H).serve_forever()
