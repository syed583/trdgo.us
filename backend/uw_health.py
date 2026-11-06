"""One read-only health summary of the Unusual Whales client: is trading data flowing, and if not, why?

Reads counters the client already keeps -- it never makes a provider request.

    OK        nothing is wrong
    DEGRADED  data still flows, but something is straining it (a recent concurrency 429, a slot taken away by
              the brake, background work standing aside, slow responses, timeouts)
    CRITICAL  live trading data is at risk: the live engine's budget is exhausted, the client is paused after a
              rate limit, live / essential requests are queueing for seconds, or concurrency 429s are frequent
"""
from __future__ import annotations

import time
from typing import Optional

RECENT_S = 600.0                      # "recent" = the last ten minutes


def _recent(times, now: float, window: float = RECENT_S) -> int:
    return sum(1 for t in (times or ()) if now - t <= window)


def summary(now: Optional[float] = None) -> dict:
    import unusualwhales_service as uw
    import uw_budget
    now = time.time() if now is None else now
    m = uw.metrics()
    b = uw_budget.get().snapshot()
    gate = uw._inflight.gate
    c = m.get("concurrency") or {}
    live_cap = uw_budget.get().capacity(1)
    c429 = _recent(uw._metrics.get("c429_times"), now)
    wait95 = b.get("live_queue_wait_p95_s")
    reasons: list[str] = []
    level = "OK"

    def raise_to(new: str, why: str) -> None:
        nonlocal level
        reasons.append(why)
        if new == "CRITICAL" or level == "OK":
            level = new

    if not live_cap["ok"]:
        raise_to("CRITICAL", f"live engine cannot call the API: {live_cap['reason']}")
    if m.get("blocked"):
        raise_to("CRITICAL", f"client paused for {m.get('blocked_for_seconds')}s after a rate limit")
    if wait95 is not None and wait95 > 5.0:
        raise_to("CRITICAL", f"live / essential requests wait {wait95:.1f}s for a connection (p95)")
    if c429 >= 10:
        raise_to("CRITICAL", f"{c429} 'too many simultaneous requests' refusals in 10 minutes: the token is "
                             "shared with another caller")
    elif c429:
        raise_to("DEGRADED", f"{c429} 'too many simultaneous requests' refusal(s) in the last 10 minutes")
    held_back = gate.cfg["slots"] - gate.effective_slots()
    if held_back > 0:
        raise_to("DEGRADED", f"the adaptive brake is holding {held_back} connection(s) back")
    if b.get("background_yielding"):
        raise_to("DEGRADED", "dashboards and the warmer are standing aside for live trading")
    p95 = m.get("p95_latency_ms")
    if p95 is not None and p95 > 3000:
        raise_to("DEGRADED", f"p95 latency {p95} ms")
    if (m.get("timeouts") or 0) and _recent(uw._metrics.get("err_times"), now, 300):
        raise_to("DEGRADED", "recent request errors / timeouts")
    return {
        "status": level, "reasons": reasons, "at": now,
        "concurrency": c,
        "latency_ms": {"avg": m.get("avg_latency_ms"), "p95": m.get("p95_latency_ms"), "p99": m.get("p99_latency_ms"),
                       "last": m.get("last_latency_ms")},
        "queue_wait_ms": {"avg": m.get("avg_wait_ms"), "p95": m.get("p95_wait_ms"), "p99": m.get("p99_wait_ms"),
                          "live_essential_p95_s": wait95},
        "errors": {"by_status": m.get("errors_by"), "http_429_total": (m.get("errors_by") or {}).get("429", 0),
                   "concurrent_429_total": c.get("concurrent_429"), "concurrent_429_last_10min": c429,
                   "http_404_total": m.get("no_data"), "timeouts": m.get("timeouts"),
                   "blocked": m.get("blocked"), "blocked_for_seconds": m.get("blocked_for_seconds")},
        "queues": gate.queue_depth(),
        "gate": {str(k): v for k, v in gate.stats.items()},
        "live_capacity": live_cap,
        "background": {"yielding": b.get("background_yielding"), "warmer_gate": b.get("warmer_gate"),
                       "warmer_paused": b.get("warmer_paused"), "in_session": b.get("in_session")},
        "plan": b.get("plan"),
        "top_404": m.get("no_data_top"),
    }
