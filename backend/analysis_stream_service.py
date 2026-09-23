"""
Streams the directional analysis stage by stage as each provider answers.

Why stream at all
-----------------
Assembling the score means calling eight providers, and on a cold symbol that
genuinely takes thirty to ninety seconds. A spinner for that long tells the
reader nothing and looks broken; naming each source as it lands tells them
what is happening and roughly how much is left.

The progress is real
--------------------
Every event is emitted when a provider actually returns. Nothing is on a
timer, and there is no artificial delay to make the work look substantial --
if the data is cached and the whole thing finishes in three seconds, it
finishes in three seconds. A padded progress bar is a lie about how fresh the
data is, which is the one thing this screen exists to communicate.

Stages map to the same sixteen parameters the score uses, so what the reader
watches assemble is exactly what the model then scores.

A deliberate run asks for ``refresh``, which clears this symbol's cached
provider answers before starting. That is what makes pressing Analyse take
real time on a symbol the app looked at minutes ago: the providers are asked
again. The alternative -- holding the screen open on a timer while showing
cached figures -- would be exactly the padded progress bar described above.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Iterator, Optional

# Each stage names the providers it waits on and the model parameters it
# feeds, so the screen can say what a source is actually for.
STAGES: list[dict] = [
    {"key": "sec_filings", "label": "SEC Filings",
     "sub": "Form 4, 13D/13G", "feeds": ["insider_activity"]},
    {"key": "insider", "label": "Insider Trades",
     "sub": "open-market decisions only", "feeds": ["insider_activity"]},
    {"key": "institutional", "label": "Institutional Holdings",
     "sub": "13F", "feeds": ["insider_activity"]},
    {"key": "options_flow", "label": "Options Flow",
     "sub": "sweeps, blocks, unusual activity",
     "feeds": ["options_flow", "unusual_activity"]},
    {"key": "gex", "label": "GEX / OI Data",
     "sub": "dealer positioning", "feeds": ["gamma_exposure", "oi_positioning",
                                            "daily_oi_change"]},
    {"key": "price_action", "label": "Price Action",
     "sub": "structure, levels, volume",
     "feeds": ["price_action", "ema_trend", "rsi"]},
    {"key": "relative_strength", "label": "Relative Strength",
     "sub": "vs SPY", "feeds": ["price_action"]},
    {"key": "macro", "label": "Market Environment",
     "sub": "index trend", "feeds": []},
    {"key": "iv_greeks", "label": "IV & Options Greeks",
     "sub": "volatility surface",
     "feeds": ["implied_volatility", "expected_move"]},
    {"key": "volume", "label": "Volume & Liquidity",
     "sub": "put/call, expiry flow",
     "feeds": ["volume_pcr", "flow_by_expiry", "key_levels"]},
    {"key": "news", "label": "News & Sentiment",
     "sub": "headline tone", "feeds": ["event_radar"]},
    {"key": "earnings", "label": "Earnings & Estimates",
     "sub": "reported vs consensus", "feeds": ["earnings_results"]},
]

TOTAL_STAGES = len(STAGES)

# Which parameters roll up into each headline category on the result panel.
# Grouping is presentational only -- the weights underneath are the model's
# and are not re-weighted here.
#
# Every weighted parameter belongs to exactly one group and the groups sum to
# the model's full hundred. That is not decoration: the panel is headed "100
# pts", so a parameter missing from this table would be a point of the score
# the reader is told about nowhere. Four parameters were added to the model --
# disparity, mergers, fund flows and the dividend trend -- and until now this
# table had not moved, so the column added up to less than it claimed. The
# test suite now checks the arithmetic rather than trusting the comment.
CATEGORIES: list[dict] = [
    # Grouped under the two lists the weights are built from, so the eighty
    # and the twenty are visible on the panel rather than something a reader
    # has to add up. ``group`` is the list a category belongs to.

    # ---- market and price: 80 ----------------------------------------
    {"key": "options_tape", "label": "Options Tape", "group": "Market & Price",
     "params": ["options_flow", "unusual_activity", "disparity", "volume_pcr",
                "key_levels", "daily_oi_change", "flow_by_expiry",
                "oi_positioning"]},                                  # 52
    {"key": "trend", "label": "Trend & Price Action", "group": "Market & Price",
     "params": ["ema_trend", "rsi", "price_action"]},                # 19
    {"key": "volatility", "label": "Implied Volatility",
     "group": "Market & Price",
     "params": ["implied_volatility"]},                              # 7
    # The session parameters belong to this list too: the today and tomorrow
    # outlooks score them instead of the trend and tape above.
    {"key": "session", "label": "Session & Intraday", "group": "Market & Price",
     "params": ["vwap", "opening_range", "intraday_trend", "gap_hold",
                "close_location", "after_hours", "relative_strength_day",
                "relative_volume"]},

    # ---- company and ownership: 20 ------------------------------------
    {"key": "ownership", "label": "Insider & Fund Ownership",
     "group": "Company & Ownership",
     "params": ["insider_activity", "fund_flows"]},                  # 9
    {"key": "reported", "label": "Earnings, Deals & Funding",
     "group": "Company & Ownership",
     "params": ["earnings_results", "merger_activity",
                "funding_activity"]},                                # 9
    {"key": "slow", "label": "Dividends & Analyst Actions",
     "group": "Company & Ownership",
     "params": ["dividend_trend", "event_radar"]},                   # 2
]
                                                                # 100


def _event(kind: str, payload: dict) -> str:
    """One server-sent event."""
    return f"data: {json.dumps({'type': kind, **payload})}\n\n"


def categorise(signals: list[dict]) -> list[dict]:
    """
    Roll the sixteen parameters into headline categories.

    A category's score is the weighted average of its parameters' biases
    mapped onto 0-100, using the model's own weights. Nothing is re-weighted
    for display -- a category that looks strong here is strong because the
    parameters underneath it are, and clicking through to the score panel
    shows the same numbers.
    """
    by_name = {s["name"]: s for s in signals}
    out = []

    for group in CATEGORIES:
        members = [by_name[p] for p in group["params"] if p in by_name]
        available = [m for m in members if m.get("available")]
        # What this group is worth in the model, measured or not. The eight
        # groups sum to the full hundred, so the column can be read as the
        # score's own arithmetic rather than as eight unrelated gauges.
        possible = sum(m.get("weight") or 0 for m in members)

        if not available:
            out.append({
                "key": group["key"], "label": group["label"],
                # Which of the model's two lists this belongs to, so the panel
                # can show the eighty and the twenty as they are weighted.
                "group": group.get("group", "Market & Price"),
                "score": None, "available": False,
                "weight_possible": possible,
                "weight": 0,
                "points": None,
                "parameters": group["params"],
                "detail": "No data returned for this group.",
            })
            continue

        weight = sum(m.get("weight") or 0 for m in available)
        if not weight:
            continue
        blended = sum((m.get("bias") or 0.0) * (m.get("weight") or 0)
                      for m in available) / weight

        # A category whose parameters all describe magnitude carries no
        # direction either: showing "-1.1" against implied volatility
        # contradicted the "sizing only" the parameter itself reports.
        directional = [m for m in available if m.get("directional")]

        out.append({
            "key": group["key"],
            "label": group["label"],
            "group": group.get("group", "Market & Price"),
            "score": round(50 + blended * 50),
            "available": True,
            "directional": bool(directional),
            "weight": weight,
            "weight_possible": possible,
            # Points this group actually contributed, on the model's own
            # scale -- the same figure the parameter rows show, summed.
            "points": (round(sum(m.get("points") or 0 for m in directional), 1)
                       if directional else None),
            "parameters": group["params"],
            "detail": "; ".join(
                m.get("detail", "") for m in available if m.get("detail"))[:200],
        })

    return out


# Cache families a deliberate run keeps rather than re-fetching.
#
# A refresh should re-read what can have moved since the last look. These
# cannot: a Form 4 filed yesterday is the same Form 4, a 13F is a quarter old
# by the time anyone can read it, and a company's SIC code does not change
# between two presses of Analyse. Re-fetching them added about fifty seconds
# to every run and could not change a single figure on the screen.
SLOW_MOVING = ("sec", "form4", "ownership", "institutional", "profile",
               "earnhist", "symvalid", "symsearch")


def stream_analysis(symbol: str, refresh: bool = False,
                    horizon: str = "SWING") -> Iterator[str]:
    """
    Run the analysis, emitting an event whenever a provider lands.

    The work runs on a worker thread and reports through a queue, so the
    response starts immediately rather than after everything resolves.

    ``refresh`` drops this symbol's cached provider answers first, so the run
    actually re-reads the market. Pressing Analyse means "go and look now";
    serving that from a cache filled ten minutes ago answers a different
    question, and the whole point of this screen is to show what is being
    read. It is the reason a deliberate run takes the better part of a minute
    while the board's background scan does not -- the wait is real work, not
    a progress bar on a timer.
    """
    symbol = (symbol or "").upper().strip()
    started = time.time()

    if refresh and symbol:
        from live_market_service import cache

        cache.purge(symbol, keep=SLOW_MOVING)
    updates: "queue.Queue[Optional[dict]]" = queue.Queue()

    yield _event("start", {
        "symbol": symbol,
        "stages": STAGES,
        "total": TOTAL_STAGES,
    })

    def worker() -> None:
        import directional_score_service as ds

        try:
            # Each provider reports as it completes. The gather is already
            # concurrent, so stages land out of order -- which is honest:
            # that is the order the data actually arrives in.
            def progress(key: str, status: str, detail: str = "",
                         elapsed: Optional[float] = None) -> None:
                updates.put({
                    "type": "stage",
                    "key": key,
                    "status": status,
                    "detail": detail,
                    "elapsed": round(elapsed if elapsed is not None
                                     else time.time() - started, 2),
                })

            result = ds.get_directional_score(symbol, progress=progress)

            # Nothing downstream should run on a symbol that does not exist:
            # scoring an outlook over an empty base invents one, and the board
            # and the call log would both carry a stock nobody can trade.
            if result.get("status") == "UNKNOWN_SYMBOL":
                updates.put({"type": "result", "result": result})
                return

            # A short horizon is scored from the swing result already in
            # hand, then reads the day's bars and quotes it needs on top.
            if horizon in ("TODAY", "TOMORROW"):
                import horizon_model

                result = horizon_model.score_horizon(symbol, horizon, base=result)

            # A run the reader just watched is the freshest score there is, so
            # the AI Trade board takes it rather than waiting for its own scan
            # to come round again. Otherwise the two screens show different
            # numbers for the same name and neither is wrong, which is the
            # most confusing kind of disagreement.
            try:
                import ai_trade_service as board

                board.record_score(symbol, result, horizon=horizon)
            except Exception:  # noqa: BLE001 - the analysis stands alone
                pass

            # Write the call down with its working, so what the screen shows
            # can be explained later and checked against what happened.
            try:
                import calls_service

                calls_service.record_async(symbol, result, horizon=horizon,
                                           origin="analysis")
            except Exception:  # noqa: BLE001
                pass

            updates.put({"type": "result", "result": result})
        except Exception as exc:  # noqa: BLE001
            updates.put({"type": "error", "detail": str(exc)[:300]})
        finally:
            updates.put(None)

    thread = threading.Thread(target=worker, daemon=True, name="analysis")
    thread.start()

    completed = 0
    while True:
        try:
            item = updates.get(timeout=120)
        except queue.Empty:
            yield _event("error", {"detail": "Analysis timed out."})
            return

        if item is None:
            break

        if item["type"] == "stage":
            if item["status"] in ("complete", "unavailable"):
                completed += 1
            yield _event("stage", {
                **item,
                "completed": completed,
                "total": TOTAL_STAGES,
                "percent": round(completed / TOTAL_STAGES * 100),
            })
        elif item["type"] == "result":
            result = item["result"]
            yield _event("result", {
                "result": result,
                "categories": categorise(result.get("signals") or []),
                "elapsed": round(time.time() - started, 2),
                "sources_complete": completed,
                "sources_total": TOTAL_STAGES,
            })
        elif item["type"] == "error":
            yield _event("error", item)

    yield _event("done", {"elapsed": round(time.time() - started, 2)})
