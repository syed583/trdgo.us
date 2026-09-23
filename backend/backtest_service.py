"""
Point-in-time backtesting.

The hard rule here is no lookahead. On every simulated day the signal is
computed from bars up to and including that day only, and the trade is entered
at the *next* day's open. Nothing from later in the series can reach the
decision.

Scope note, stated plainly: this backtests the **technical composite**, not the
full six-component TRDGO score. Fundamentals, analyst estimates and option
chains are not stored point-in-time in this installation, so replaying them
historically would mean using today's values on a past date - the classic
lookahead bug. The engine reports exactly which components it could and could
not replay.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import live_market_service as market

# Relative strength needs something to be relative to. SPY is the broad-market
# reference the live scorer uses, so the backtest uses the same one.
BENCHMARK = "SPY"

MAX_SYMBOLS = 12

REPLAYABLE = ["technicals"]
NOT_REPLAYABLE = {
    "fundamentals": "SEC snapshots are not stored per historical date",
    "estimates": "EstimateSnapshot history is seed data only",
    "options": "No historical option chain is retained",
    "earnings_history": "Only current rows are stored, not as-of-date views",
    "market_environment": "Not snapshotted per historical date",
}


def _composite(bars: list[dict],
               benchmark: Optional[list[dict]] = None) -> Optional[float]:
    """
    The score under test, identical to the one the product shows.

    This was a separate formula with its own weights, so a backtest reporting
    "scores above 70 performed well" described something that existed nowhere
    else. Validating a number nobody sees is worse than not validating at all,
    because it reads as evidence.

    Given only bars up to the decision date, so it stays safe inside the
    walk-forward loop.
    """
    from composite_score_service import score_only

    return score_only(bars, benchmark)


def _align_benchmark(bars: list[dict],
                     benchmark: list[dict]) -> dict[str, int]:
    """
    Map each bar date to its position in the benchmark series.

    Needed because relative strength compares two windows that must end on the
    same day. Without it the backtest would either drop relative strength --
    quietly scoring a different formula than the live product -- or compare
    today's stock against the benchmark's whole history, which is worse.
    """
    return {str(b["date"])[:10]: i for i, b in enumerate(benchmark)}


def _run_symbol(
    symbol: str,
    bars: list[dict],
    min_score: float,
    direction: str,
    hold_days: int,
    stop_pct: float,
    target_pct: float,
    start: Optional[str],
    end: Optional[str],
    benchmark: Optional[list[dict]] = None,
) -> list[dict]:
    trades: list[dict] = []
    bench_index = _align_benchmark(bars, benchmark) if benchmark else {}
    i = 60
    n = len(bars)

    while i < n - 1:
        bar = bars[i]
        day = str(bar["date"])[:10]
        if start and day < start:
            i += 1
            continue
        if end and day > end:
            break

        # Signal uses bars[0..i] only, and the benchmark is sliced to the
        # same date so relative strength compares like with like.
        bench_slice = None
        if benchmark:
            cut = bench_index.get(day)
            if cut is not None:
                bench_slice = benchmark[: cut + 1]
        score = _composite(bars[: i + 1], bench_slice)
        if score is None:
            i += 1
            continue

        go_long = direction in ("LONG", "BOTH") and score >= min_score
        go_short = direction in ("SHORT", "BOTH") and score <= (100 - min_score)
        if not (go_long or go_short):
            i += 1
            continue

        side = "LONG" if go_long else "SHORT"
        sign = 1 if side == "LONG" else -1

        # Entry on the next bar's open - never the signal bar's close.
        entry_idx = i + 1
        entry = bars[entry_idx]["open"]
        if not entry:
            i += 1
            continue

        stop = entry * (1 - stop_pct / 100 * sign)
        target = entry * (1 + target_pct / 100 * sign)

        exit_idx = min(entry_idx + hold_days, n - 1)
        exit_price = bars[exit_idx]["close"]
        reason = "TIME"
        mfe = mae = 0.0

        for j in range(entry_idx, exit_idx + 1):
            hi, lo = bars[j]["high"], bars[j]["low"]
            best = (hi - entry) * sign / entry * 100
            worst = (lo - entry) * sign / entry * 100
            if side == "SHORT":
                best = (entry - lo) / entry * 100
                worst = (entry - hi) / entry * 100
            mfe = max(mfe, best)
            mae = min(mae, worst)

            hit_stop = lo <= stop if side == "LONG" else hi >= stop
            hit_target = hi >= target if side == "LONG" else lo <= target
            if hit_stop:
                exit_price, exit_idx, reason = stop, j, "STOP"
                break
            if hit_target:
                exit_price, exit_idx, reason = target, j, "TARGET"
                break

        ret = (exit_price - entry) * sign / entry * 100
        trades.append({
            "symbol": symbol,
            "side": side,
            "signal_date": day,
            "entry_date": str(bars[entry_idx]["date"])[:10],
            "exit_date": str(bars[exit_idx]["date"])[:10],
            "score": round(score, 1),
            "entry": round(entry, 2),
            "exit": round(exit_price, 2),
            "return_pct": round(ret, 2),
            "mfe_pct": round(mfe, 2),
            "mae_pct": round(mae, 2),
            "bars_held": exit_idx - entry_idx,
            "exit_reason": reason,
        })

        # No overlapping positions in the same symbol.
        i = exit_idx + 1

    return trades


def run_backtest(
    symbols: list[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
    min_score: float = 70,
    min_confidence: float = 0,
    direction: str = "LONG",
    hold_days: int = 10,
    stop_pct: float = 5.0,
    target_pct: float = 10.0,
) -> dict:
    symbols = [s.strip().upper() for s in symbols if s.strip()][:MAX_SYMBOLS]
    direction = (direction or "LONG").upper()
    if direction not in ("LONG", "SHORT", "BOTH"):
        direction = "LONG"

    if not symbols:
        return {"status": "INVALID", "detail": "At least one symbol required"}

    # 5Y of daily bars gives the deepest window IBKR will serve cheaply.
    trades: list[dict] = []
    unavailable: list[dict] = []

    # Fetched once and sliced per decision date, so every symbol is measured
    # against the same market over the same window.
    benchmark: list[dict] = []
    bench_chart = market.get_chart(BENCHMARK, "5Y")
    if bench_chart.get("status") == "OK":
        benchmark = [{
            "date": b["t"], "open": b["open"], "high": b["high"],
            "low": b["low"], "close": b["close"], "volume": b["volume"],
        } for b in bench_chart["bars"]]

    for symbol in symbols:
        chart = market.get_chart(symbol, "5Y")
        if chart.get("status") != "OK" or len(chart.get("bars", [])) < 80:
            unavailable.append({"symbol": symbol,
                                "status": chart.get("status", "NO_DATA")})
            continue
        bars = [{
            "date": b["t"], "open": b["open"], "high": b["high"],
            "low": b["low"], "close": b["close"], "volume": b["volume"],
        } for b in chart["bars"]]
        trades.extend(_run_symbol(symbol, bars, min_score, direction,
                                  hold_days, stop_pct, target_pct, start, end,
                                  benchmark or None))

    trades.sort(key=lambda t: t["entry_date"])

    if not trades:
        return {
            "status": "NO_TRADES",
            "detail": "No signal met the threshold in this window",
            "symbols": symbols,
            "unavailable": unavailable,
            "trades": [],
            "stats": {},
            "methodology": _methodology(min_confidence),
        }

    rets = [t["return_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    equity, peak, dd = 0.0, 0.0, 0.0
    for r in rets:
        equity += r
        peak = max(peak, equity)
        dd = min(dd, equity - peak)

    stats = {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_return": round(sum(rets) / len(rets), 2),
        "net_return": round(sum(rets), 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown": round(dd, 2),
        "avg_mfe": round(sum(t["mfe_pct"] for t in trades) / len(trades), 2),
        "avg_mae": round(sum(t["mae_pct"] for t in trades) / len(trades), 2),
        "avg_bars_held": round(sum(t["bars_held"] for t in trades) / len(trades), 1),
        "best": round(max(rets), 2),
        "worst": round(min(rets), 2),
    }

    return {
        "status": "OK",
        "symbols": symbols,
        "unavailable": unavailable,
        "params": {
            "start": start, "end": end, "min_score": min_score,
            "direction": direction, "hold_days": hold_days,
            "stop_pct": stop_pct, "target_pct": target_pct,
        },
        "trades": trades[-300:],
        "stats": stats,
        "methodology": _methodology(min_confidence),
        "source": "IBKR",
    }


def _methodology(min_confidence: float) -> dict:
    return {
        "signal": "Technical composite (trend, RSI, 52w range position, 20d ROC)",
        "point_in_time": (
            "Each signal uses only bars up to that date; entry is the next "
            "bar's open. No later data reaches the decision."
        ),
        "replayable_components": REPLAYABLE,
        "not_replayable": NOT_REPLAYABLE,
        "confidence_gate": (
            "Not applied: confidence depends on components that cannot be "
            "replayed point-in-time, so a historical confidence figure would "
            "be fabricated."
            if min_confidence else "Not requested"
        ),
        "costs": "Commissions and slippage are not modelled",
    }
