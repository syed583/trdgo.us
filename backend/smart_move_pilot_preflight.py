"""Preflight for a supervised live pilot: exact counts from SAVED calendar data at a stated evaluation time, calendar-coverage diagnostics, and a deterministic request allocation under a hard ceiling.

No provider request, no store write (a temporary copy of the jsonl files is used), no scoring change. Counts are only as complete as the saved calendar: dates it does not cover are reported as not covered, never invented.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_calendar_guard as G  # noqa: E402
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_collection_plan as CP  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_v2_collect as C  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
PAGE = 100


class _NoNet(L.Fetcher):
    mode = "SIMULATION"

    def fetch(self, kind, symbol, params):
        raise AssertionError("preflight must never call the provider")


def _service(store_dir: Path, now: datetime):
    tmp = Path(tempfile.mkdtemp(prefix="pilot_preflight_"))
    for n in ("cal_obs.jsonl", "v2_evidence.jsonl", "evidence.jsonl"):
        if (store_dir / n).exists():
            shutil.copy2(store_dir / n, tmp / n)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp, refresh_enabled=False, authorized_daily=0, spacing_s=0.0)
    return L.RefreshService(cfg, _NoNet(), gv.FakeClock(now)), tmp


def calendar_coverage(store, now: datetime) -> dict:
    """Which dates the saved calendar covers, per vendor and side, and whether any side/day looks truncated by the old 2-page cap (exactly 100 x pages rows per refresh)."""
    obs = store.read("cal_obs")
    by_refresh = defaultdict(Counter)
    for r in obs:
        if r["source"] == "UW_CALENDAR":
            by_refresh[(r["observed_at"][:16], r["date"], r["label"])][r["request_id"]] += 1
    full = [{"refresh": k[0], "date": k[1], "side": k[2], "rows": sum(v.values())} for k, v in by_refresh.items() if sum(v.values()) >= PAGE and sum(v.values()) % PAGE == 0]
    dates = sorted({r["date"] for r in obs if r["date"] >= (now - timedelta(days=1)).date().isoformat()})
    newest = max((r["observed_at"] for r in obs), default=None)
    return {"newest_observation": newest, "dates_covered_from_yesterday": dates, "span_days": (datetime.fromisoformat(dates[-1]).date() - datetime.fromisoformat(dates[0]).date()).days if dates else 0,
            "sides_with_a_full_last_page_possible_truncation": full, "horizon_14d_covered": bool(dates) and datetime.fromisoformat(dates[-1]).date() >= (now + timedelta(days=10)).date()}


def event_counts(store_dir, now: datetime) -> dict:
    svc, tmp = _service(Path(store_dir), now)
    try:
        age = G.calendar_age_h(svc.store, now)
        window = svc.eligible_events(now)
        tracking = svc.eligible_events(now, svc.cfg.tracking_days_ahead)
        from smvshadow import calendar_confirm as cc
        from smvshadow import cutoff as co
        status = Counter()
        expired = 0
        for sym, pairs in svc._calobs().items():
            ev = cc.evaluate(sym, [o for o, _ in pairs if o.observed_at <= now], now, now.astimezone(co.ET).date())
            status[ev["status"]] += 1
            if ev["eligible_for_shadow"] and ev["event_date"] and ev["kind"] in ("BMO", "AMC"):
                if co.plan_event(ev["event_date"], ev["kind"])["cutoff_utc"] < now:
                    expired += 1
        tb = J.build_tasks(window, tracking, svc.store, now)
        news = Counter(t["pass"] for t in tb["news"])
        return {"evaluated_at_utc": now.isoformat(), "calendar_age_h": age, "calendar_state": "UNKNOWN" if age is None else "STALE" if age > G.MAX_AGE_H else "FRESH",
                "symbols_in_saved_calendar": len(svc._calobs()), "vendor_status_counts": dict(status), "blocked_by_stale_calendar": status.get("STALE", 0), "eligible_events_tracking_14d": len(tracking),
                "eligible_events_open_window": len(window), "expired_eligible_events": expired, "snapshot_a_candidates": len(tb["snapshot_a"]), "snapshot_b_candidates": len(tb["snapshot_b"]),
                "news_initial_candidates": news.get("INITIAL", 0), "news_final_candidates": news.get("FINAL", 0), "tasks_total": len(tb["tasks"]), "request_range": J.request_range(tb["tasks"]),
                "coverage": calendar_coverage(svc.store, now),
                "complete": bool(calendar_coverage(svc.store, now)["horizon_14d_covered"]),
                "note": "counts reflect only what the saved calendar contains; dates it does not cover are not counted and nothing is invented"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pilot_allocation(budget: int = 50, calendar_requests: int = 14, snapshot_a: int = 0, snapshot_b: int = 0, news: int = 0, news_pages: int = 1) -> dict:
    """Deterministic split of a HARD ceiling. Calendar first (it is what makes events eligible), then Snapshot A, B, news. Each line is capped by what is actually due and by what is left; pagination is off.
    The totals can never exceed `budget`; when due work is larger than what is left, the remainder is reported as DEFERRED, never sent."""
    left = budget
    lines = []
    for name, want, endpoint, purpose in (("calendar refresh", calendar_requests, "/api/earnings/{premarket,afterhours}, Finviz export, Finnhub calendar", "freshness + eligibility (2 vendors)"),
                                          ("Snapshot A", snapshot_a, "/api/companies/{ticker}/earnings-estimates", "estimate history ~10 days before the release"),
                                          ("Snapshot B", snapshot_b, "/api/companies/{ticker}/earnings-estimates", "estimates inside the prediction window"),
                                          ("news INITIAL/FINAL", news * news_pages, "/api/news/headlines?ticker=SYM&limit=25 (page 0 only)", "pre-cutoff headlines")):
        take = min(want, left)
        lines.append({"priority": len(lines) + 1, "item": name, "endpoint": endpoint, "purpose": purpose, "due": want, "allocated": take, "deferred": want - take})
        left -= take
    return {"ceiling": budget, "allocated": budget - left, "unallocated": left, "lines": lines, "hard_ceiling": True}
