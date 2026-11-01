"""Connects the outcome pipeline to the approved provider adapter. Every candle request goes through `call(kind, symbol, params) -> (body, sha, at)`, which in production is
RefreshService._call: that path is bound by the governor (authorization cap, spacing, hourly caps, 401/429 stop) and the kill switch. Tests pass a mock `call`; nothing here builds a
provider client. Outcomes are only requested for events whose reaction session has closed plus the safety delay, and at most `max_requests` per run.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Callable, Iterable

import smart_move_outcomes as O
import smart_move_tracking as T


def collect(events: Iterable[dict], track_dir, now: datetime, call: Callable, *, max_requests: int, mode: str = "LIVE", stopped: Callable[[], bool] = lambda: False) -> dict:
    plan = O.plan_fetches(events, track_dir, now, max_requests=max_requests)
    kinds = {(e["symbol"].upper()): e["kind"] for e in events}
    dates = {(e["symbol"].upper()): str(e["earnings_date"])[:10] for e in events}
    res = {"planned": plan["fetch"], "recorded": [], "pending": [], "failed": [], "deferred_by_budget": plan["deferred_by_budget"], "not_due_yet": plan["not_due_yet"], "requests": 0, "stopped": False}
    for sym in plan["fetch"]:
        if stopped():
            res["stopped"] = True
            break
        body, sha, at = call("ohlc", sym, {"timeframe": "1M"})
        res["requests"] += 1
        if body is None:                                   # not done (denied, failed, provider stop): nothing is recorded, the event stays due for the next tick
            res["failed"].append(sym)
            if stopped():
                res["stopped"] = True
                break
            continue
        try:
            rows = json.loads(body).get("data") or []
        except (ValueError, AttributeError):
            res["failed"].append(sym)
            continue
        out = O.compute(sym, dates[sym], kinds[sym], rows, now, mode=mode, source="uw.ohlc.1d raw_sha256=%s" % sha)
        r = O.record(track_dir, out)
        if out["status"] == "GRADED":                       # feed the frozen grader's outcome file too, so Tracking and the accuracy analytics update by themselves; first value wins there, corrections stay versioned in outcome_details
            res.setdefault("tracking", []).append(T.import_outcomes(track_dir, [{"symbol": sym, "earnings_date": dates[sym], "move_pct": out["move_pct"], "source": out["source"]}], mode=mode, recorded_at=now))
        (res["recorded"] if r["result"] in ("recorded", "correction", "duplicate_same") else res["pending"]).append({"symbol": sym, "result": r["result"], "status": out["status"]})
    return res
