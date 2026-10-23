"""Fresh inputs for the six AI intraday boards, one shared feed.

The boards score from a bundle of "signals". Those signals are NOT all equally time-sensitive,
and until now they all came from an earnings snapshot that could be a day old:

    fast, time-sensitive    options flow, unusual activity, volume/put-call, flow by expiry,
                            key levels, implied volatility, expected move, disparity, daily OI
                            change, EMA / RSI / price action (daily bars with today's live bar)
    slow                    event radar, earnings results, insider / ownership ...

This module refreshes ONLY the fast group, once per symbol for all six models (not once per model),
with the SAME fetchers and the SAME signal functions the directional score uses (nothing about the
scoring changes), under the "essential" API priority, and spread over time by the API budget:

    feed_cycle()   one scheduling pass: refresh the stalest symbols that are due and permitted
    read(symbol)   what the boards use: a cache read, never an API call

Slow signals keep coming from the earnings snapshot but are judged by THEIR OWN freshness limit
(earnings 48 h, ownership 7 days), not the 10-minute rule. A signal past its limit is dropped, so
the model sees it as missing -- it is never converted into a bullish or bearish reading.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import tab_settings as cfg
import uw_budget
import uw_priority as pri

FAST_SIGNALS = ("options_flow", "unusual_activity", "volume_pcr", "oi_positioning", "flow_by_expiry",
                "key_levels", "implied_volatility", "expected_move", "disparity", "daily_oi_change",
                "ema_trend", "rsi", "price_action")
MIN_FAST_COVERAGE = 0.5            # a refresh that produced under half the fast signals is not stored
KEY = "intrafeed:{}"

PHASE_SCALE = {"rth": 1.0, "post": 3.0, "pre": 6.0, "closed": 36.0}
_SLOW_TYPES = {"earnings_fundamentals", "ownership_insider"}      # not scaled by session phase

# At least one fresh member of each group must exist or the name has no tradable signal.
CRITICAL_GROUPS = {
    "options flow": ("options_flow", "unusual_activity", "volume_pcr", "flow_by_expiry"),
    "price action (daily bars + live bar)": ("ema_trend", "price_action", "rsi"),
}


# ----------------------------------------------------------------- limits -----
def limits_for(signal: str, phase: Optional[str]) -> tuple[float, float]:
    """(live seconds, maximum seconds) for one signal in this session phase."""
    typ = cfg.SIGNAL_DATA_TYPE.get(signal, "options_positioning")
    live, mx = cfg.INTRADAY_FRESHNESS[typ]
    k = 1.0 if typ in _SLOW_TYPES else PHASE_SCALE.get(phase or "rth", 1.0)
    return live * k, mx * k


# ------------------------------------------------------------------- feed -----
def compute_fast(symbol: str) -> Optional[dict]:
    """One refresh of the time-sensitive signals for one symbol (this DOES call the API)."""
    import directional_score_service as dss
    import disparity_service
    import live_market_service as market
    import live_options_analytics as options
    import price_action_service as pa
    import uw_flow_service as odflow
    from technical_service import calculate_technicals

    symbol = symbol.upper()
    data: dict = {}
    with pri.use(pri.ESSENTIAL, "ai_boards"):
        data["overview"] = options.get_overview(symbol) or {}
        data["oi"] = odflow.daily_oi_change(symbol) or {}
        data["disparity"] = disparity_service.get_disparity(symbol) or {}
        bars = (market._fallback_bars(symbol, "1 Y") or ([], None))[0]
        bench = (market._fallback_bars("SPY", "1 Y") or ([], None))[0]
    data["technicals"] = calculate_technicals(bars) if bars else None
    data["price_action"] = pa.score_price_action(pa.analyse(bars, bench or None)) if bars else {}
    # build_signals() yields Signal objects; score_direction() turns them into the dict form the
    # rest of the app reads (name / bias / available) -- exactly what get_directional_score serves.
    scored = dss.score_direction(dss.build_signals(data))
    out = {s["name"]: s["bias"] for s in scored.get("signals", [])
           if s.get("name") in FAST_SIGNALS and s.get("available") and s.get("bias") is not None}
    if len(out) < MIN_FAST_COVERAGE * len(FAST_SIGNALS):
        return None                                     # a thin answer is not a refresh
    return {"symbol": symbol, "signals": out, "ts": time.time()}


def store(symbol: str, value: dict) -> None:
    import swr
    swr._run(KEY.format(symbol.upper()), lambda: value)     # memory + the persistent tier


def read(symbol: str) -> Optional[dict]:
    """The last stored fast signals for ``symbol`` (cache read only), with their build time."""
    import swr
    v = swr.peek(KEY.format(symbol.upper()))
    return v if isinstance(v, dict) and v.get("signals") and v.get("ts") else None


def refresh_after(phase: Optional[str]) -> float:
    """How old a symbol's fast signals may get before the feeder refreshes them (70% of 'live')."""
    live, _mx = limits_for("options_flow", phase)
    return 0.7 * live


_INTEREST_KEY = "intrafeed:interest"


def note_interest(rows: list) -> None:
    """Boards report how far each name is from neutral; the feed refreshes the interesting names first."""
    import swr
    cur = dict(swr.peek(_INTEREST_KEY) or {})
    for r in rows:
        sc = r.get("score") if r.get("score") is not None else r.get("score_raw")
        if sc is not None and r.get("symbol"):
            cur[r["symbol"]] = abs(float(sc) - 50.0)
    swr._values[_INTEREST_KEY] = (time.time(), cur)


def interest() -> dict:
    import swr
    v = swr.peek(_INTEREST_KEY)
    return v if isinstance(v, dict) else {}


def feed_cycle(symbols: list[str], phase: Optional[str], max_symbols: int = 6,
               now: Optional[float] = None, compute=compute_fast) -> dict:
    """Refresh the due symbols the shared budget permits.

    There is not enough API budget to keep every name inside its options-flow limit all day, so the
    order matters: names that hold a signal or lean hardest from neutral first, then the stalest.
    Stops at the hourly cap for this feed and whenever the budget says no."""
    now = time.time() if now is None else now
    ctl = uw_budget.get()
    cap = float(cfg.UW_BUDGET.get("feed_hourly_cap", 700))
    hot = interest()
    due = []
    for s in symbols:
        v = read(s)
        age = (now - v["ts"]) if v else float("inf")
        if age >= refresh_after(phase):
            due.append((hot.get(s, 0.0), age, s))
    due.sort(reverse=True)                              # interesting first, then stalest
    done, skipped, failed = [], [], []
    for _i, _age, s in due[:max_symbols]:
        if ctl.module_hour_calls("ai_boards") >= cap:
            skipped.append((s, "FEED_HOURLY_CAP"))
            break
        d = ctl.peek(pri.ESSENTIAL)
        if not d.ok:
            skipped.append((s, d.code))
            break                                       # the budget says stop; do not hammer it
        try:
            val = compute(s)
        except Exception:  # noqa: BLE001
            val = None
        if val:
            store(s, val)
            done.append(s)
        else:
            failed.append(s)
    return {"refreshed": done, "failed": failed, "skipped": skipped, "due": len(due)}


# ------------------------------------------------------------ the boards' view --
def effective_inputs(symbol: str, snapshot: Optional[dict], snapshot_age_s: Optional[float],
                     phase: Optional[str], now: Optional[float] = None,
                     live: Optional[tuple] = None) -> dict:
    """The signals to score from, each judged by its OWN freshness limit.

    Returns {"signals": {name: bias} (stale ones removed), "meta": {name: {...}}, "dropped": [...],
             "missing_critical": [...]}. Pure given its arguments (reads the feed cache only)."""
    now = time.time() if now is None else now
    feed = read(symbol)
    snap_sig = (snapshot or {}).get("signals") or {}
    live_sig, live_age = (live or ({}, None))
    names = list(dict.fromkeys(list(snap_sig.keys() if hasattr(snap_sig, "keys") else [])
                               + list(FAST_SIGNALS) + list(live_sig.keys())))
    signals: dict = {}
    meta: dict = {}
    dropped: list[str] = []
    for n in names:
        cands = []                                      # (build time, value, source): newest wins
        if n in FAST_SIGNALS and feed and n in feed["signals"]:
            cands.append((feed["ts"], feed["signals"][n], "intraday feed"))
        if live_age is not None and n in live_sig:
            cands.append((now - live_age, live_sig[n], "live directional"))
        if n in snap_sig and snapshot_age_s is not None:
            cands.append((now - snapshot_age_s, snap_sig[n], "earnings snapshot"))
        if not cands:
            continue
        ts, val, src = max(cands, key=lambda c: c[0])
        age = max(0.0, now - ts)
        live, mx = limits_for(n, phase)
        status = "LIVE" if age <= live else "DELAYED" if age <= mx else "STALE"
        meta[n] = {"type": cfg.SIGNAL_DATA_TYPE.get(n, "options_positioning"), "age_s": round(age),
                   "status": status, "source": src, "max_s": mx}
        if status == "STALE" or val is None:
            dropped.append(n)
            continue
        signals[n] = val
    missing_critical = [g for g, members in CRITICAL_GROUPS.items()
                        if not any(m in signals for m in members)]
    return {"signals": signals, "meta": meta, "dropped": dropped, "missing_critical": missing_critical}


# ----------------------------------------------------------------- the loop ----
_started = False


def start_background(interval_s: float = 60.0) -> None:
    """Production only: keep the board universe's fast signals fresh within the API budget."""
    global _started
    if _started:
        return
    _started = True

    def loop() -> None:
        pri.set_thread_default(pri.ESSENTIAL, "ai_boards")
        time.sleep(40)
        while True:
            try:
                import ai_trade_service as ats
                import intraday_indicators as ind
                phase = ind.market_phase()
                # Outside the session nothing moves; a slow trickle keeps the morning ready.
                per_cycle = 6 if phase in ("rth", "pre", "post") else 1
                feed_cycle([s.upper() for s in getattr(ats, "UNIVERSE", [])], phase, max_symbols=per_cycle)
            except Exception:  # noqa: BLE001 - the feeder must never die
                pass
            time.sleep(interval_s)

    threading.Thread(target=loop, name="intraday-feed", daemon=True).start()
