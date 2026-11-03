"""Offline simulation kit: a controllable clock, a scripted adapter and an approval-file writer. Used by the tests and by `python -m earnings_monitor.sim` to rehearse the whole system (scheduling, change detection,
alerts, restart recovery, budgets) with sample events. It contains no provider code and cannot reach one."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import config as C
from .alerts import FileSink
from .gate import ProviderAccess, ProviderBlocked, evaluate
from .locks import ScanLock
from .scheduler import Scheduler
from .service import Adapters, Monitor
from .store import Store

UTC = timezone.utc
SIM_ENV_BASE = {"EARNINGS_MONITOR_ENABLED": "1"}


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> datetime:
        self.t += timedelta(**kw)
        return self.t


class SimAdapters(Adapters):
    model_labels = {"claude": "Claude", "grok": "Grok", "meta": "Meta", "ped": "Pre-Earnings Direction", "smart_move": "Smart Move score40-v1"}

    def __init__(self):
        self.cal, self.mods, self.res, self.out = [], {}, {}, {}
        self.calls = {"refresh_calendar": 0, "refresh_event": 0, "verify_outcome": 0}
        self.fail_calendar = 0                      # number of upcoming refresh_calendar calls that raise

    # cache reads
    def calendar(self):
        return [dict(r) for r in self.cal]

    def models(self):
        return {m: {s: dict(o) for s, o in rows.items()} for m, rows in self.mods.items()}

    def result(self, symbol, d):
        return self.res.get((symbol, d))

    def outcome(self, symbol, d):
        return self.out.get((symbol, d))

    # provider paths (only ever reached through ProviderAccess)
    def refresh_calendar(self):
        self.calls["refresh_calendar"] += 1
        if self.fail_calendar > 0:
            self.fail_calendar -= 1
            raise RuntimeError("simulated provider failure")
        return [dict(r) for r in self.cal]

    def refresh_event(self, symbol):
        self.calls["refresh_event"] += 1

    def verify_outcome(self, symbol, d):
        self.calls["verify_outcome"] += 1

    # scripting helpers
    sm_cov = None                                    # scripted answer of smart_move_coverage
    sm_win = None                                    # scripted answer of smart_move_windows

    def smart_move_windows(self):
        return self.sm_win

    collector = None                                 # scripted collector run-log summary (None = no collector configured)
    app_used = None                                  # scripted application request count today

    def collector_status(self):
        return self.collector

    def app_requests_today(self):
        return self.app_used

    def smart_move_coverage(self):
        return self.sm_cov

    def set_model(self, model, symbol, signal, as_of, score=None, coverage=None, tier=None, source="sim_cache", engine_version=None, event_date=None):
        self.mods.setdefault(model, {})[symbol] = {"event_date": event_date, "signal": signal, "score": score, "coverage": coverage, "tier": tier, "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of, "evidence_as_of": None,
                                                   "source": source, "engine_version": engine_version}

    plan = None                                      # scripted answer of smart_move_plan (None = engine not installed)
    ev_items: dict = {}                              # scripted cache-only evidence: {SYMBOL: [item, ...]}
    sources: dict = {}                               # scripted model source text: {model: "..."}
    smd: dict = {}                                   # scripted Smart Move details: {(SYMBOL, date): {...}}

    def evidence(self, symbol, d):
        return [dict(i) for i in self.ev_items.get(symbol, [])]

    def model_sources(self):
        return dict(self.sources)

    def smart_move_details(self, symbol, d):
        return self.smd.get((symbol, d))

    def smart_move_plan(self, events, now):
        return self.plan


def write_approval(d: Path, now: datetime, mode: str = "refresh", budget: int = 700, events: int = 8, days: int = 14, purposes=("calendar", "event", "outcome"), **over) -> Path:
    a = {"approved_by": "owner", "approved_at": now.isoformat(), "expires_at": (now + timedelta(days=days)).isoformat(), "mode": mode, "scheduling_approved": True,
         "provider_authorization": mode == "refresh", "kill_switch_acknowledged": True, "daily_request_budget": budget, "expected_events_per_day": events, "max_inflight": 1,
         "refresh_calendar": "calendar" in purposes, "refresh_event_evidence": "event" in purposes, "verify_outcomes": "outcome" in purposes}
    a.update(over)
    p = d / "approval.json"
    p.write_text(json.dumps(a), encoding="utf-8")
    return p


def build_sim(d: Path, clock: Clock, adapters: Optional[SimAdapters] = None, cfg: Optional[C.Config] = None, env: Optional[dict] = None, sink_path: Optional[Path] = None):
    d.mkdir(parents=True, exist_ok=True)
    env = dict(SIM_ENV_BASE if env is None else env)
    env.setdefault("EARNINGS_MONITOR_KILL", str(d / "KILL"))
    env.setdefault("EARNINGS_MONITOR_APPROVAL", str(d / "approval.json"))
    cfg = cfg or C.Config()
    store = Store(d / "monitor.sqlite")
    ad = adapters or SimAdapters()
    access = ProviderAccess(store, cfg, evaluate(cfg, d, clock(), env), d, clock, env)
    mon = Monitor(store, cfg, ad, access, clock, sinks=[FileSink(sink_path or d / "alerts_outbox.jsonl")])
    sched = Scheduler(store, cfg, mon, access, ScanLock(d / "scan.lock"), lambda t: evaluate(cfg, d, t, env))
    return store, cfg, ad, mon, sched, access


def demo(d: Path) -> dict:
    """The rehearsal the owner can run: `python -m earnings_monitor.sim`. Sample events over three simulated days, with the monitor in REFRESH mode against a scripted adapter."""
    from .store import iso
    clock = Clock(datetime(2026, 11, 9, 17, 0, tzinfo=UTC))
    write_approval(d, clock(), mode="refresh", budget=450, events=20)
    store, cfg, ad, mon, sched, access = build_sim(d, clock)

    def run():
        sched.tick(clock())
        sched.run_pending(clock())
    ad.cal = [{"symbol": "AAA", "company": "Alpha", "date": "2026-11-11", "session": "BMO"}, {"symbol": "BBB", "company": "Beta", "date": "2026-11-12", "session": "AMC"},
              {"symbol": "CCC", "company": "Gamma", "date": "2026-11-12", "session": "UNKNOWN"}]
    for m, sig in (("claude", "BUY"), ("grok", "BUY"), ("meta", "BUY"), ("ped", "NEUTRAL"), ("smart_move", "BUY")):
        ad.set_model(m, "AAA", sig, clock(), score=60, coverage="8/10")
    run()
    clock.advance(hours=6)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-12", "session": "BMO"}, {"symbol": "CCC", "date": "2026-11-12", "session": "AMC"}, {"symbol": "DDD", "date": "2026-11-13", "session": "BMO"}]
    ad.set_model("claude", "AAA", "SELL", clock(), score=35, coverage="8/10")
    run()
    clock.advance(hours=6)
    run()
    clock.t = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)
    ad.res[("AAA", "2026-11-12")] = {"published_at": "2026-11-12T11:30:00+00:00"}
    ad.out[("AAA", "2026-11-12")] = {"move_pct": -4.2, "direction": "SELL"}
    run()
    return {"events": store.q("SELECT symbol, earnings_date, session, status FROM events ORDER BY id"),
            "changes": [(c["detected_at"][:16], c["symbol"], c["kind"], c["model"], c["old_value"], c["new_value"]) for c in store.q("SELECT * FROM changes ORDER BY id")],
            "alerts": [(a["rule"], a["symbol"], a["model"]) for a in store.q("SELECT rule, symbol, model FROM alerts ORDER BY id")], "provider_requests_by_day": store.q("SELECT day, SUM(requests) requests FROM usage GROUP BY day"),
            "provider_calls": dict(ad.calls), "jobs": store.job_counts()}


if __name__ == "__main__":
    import tempfile
    out = demo(Path(tempfile.mkdtemp(prefix="em_demo_")))
    for k, v in out.items():
        print("== %s" % k)
        for row in (v if isinstance(v, list) else [v]):
            print("  ", row)
