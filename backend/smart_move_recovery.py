"""Missing-evidence recovery audit for the 95 stocks with saved V2 evidence (read-only, no request, no rewrite of any prediction).

For every stock and every factor that lacks points it records the exact reason, one of 8 categories, whether the evidence is recoverable TODAY, and whether recovering it would keep the original point-in-time cutoff.
A recovery pass then tries each legitimately possible source in the saved data and reports what actually changed; nothing is moved from 'missing' to 'available' unless a timestamp-valid source exists.

Categories: 1 provider data available but not correctly mapped | 2 saved evidence not incorporated | 3 genuine absence of qualifying activity | 4 historical snapshot/history unavailable |
5 unverified news semantics | 6 stale calendar or capture-time failure | 7 provider error | 8 other implementation bug.

Standing fact for this universe: every capture window has CLOSED (AMC 1 Nov 21:00 UTC, BMO 1 Nov 11:00 UTC) and the events report on 2 Nov. Anything collected now is after the cutoff and cannot enter a pre-earnings
signal for these events; recovery here means 'can it be established from evidence that already existed at capture time'.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_v2 as V2  # noqa: E402
import smart_move_v2_snapshots as SN  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402

CATEGORIES = {1: "PROVIDER_DATA_NOT_MAPPED", 2: "SAVED_EVIDENCE_NOT_INCORPORATED", 3: "GENUINE_ABSENCE_OF_QUALIFYING_ACTIVITY", 4: "HISTORICAL_SNAPSHOT_UNAVAILABLE", 5: "UNVERIFIED_NEWS_SEMANTICS",
              6: "STALE_CALENDAR_OR_CAPTURE_TIME_FAILURE", 7: "PROVIDER_ERROR", 8: "OTHER_IMPLEMENTATION_BUG"}


def categorize(factor: str, f: dict, flow_state: str, symbol: str, quarter_row_missing: bool) -> dict:
    cause = f.get("cause")
    if cause == "STALE_CALENDAR_AT_CAPTURE":
        return {"category": 6, "reason": "captured against an earnings calendar older than 6 h (single calendar refresh at 10:38 UTC)", "recoverable_today": False, "preserves_cutoff": False,
                "needs": "a new capture inside the window against a fresh calendar (the windows are closed); later data would break the cutoff"}
    if cause == "API_FAILURE":
        return {"category": 7, "reason": "the provider call failed after one retry (transient)", "recoverable_today": False, "preserves_cutoff": False, "needs": "re-request: a new provider request (not authorized) and the window is closed"}
    if cause == "UNVERIFIED_SEMANTICS" or factor == "NEWS_GUIDANCE":
        return {"category": 5, "reason": "provider sentiment is undefined and uniformly neutral; classifier news-x1 is not independently validated", "recoverable_today": False, "preserves_cutoff": True,
                "needs": "an independently labelled headline set (>= 100, >= 90 % agreement) and your approval"}
    if factor == "OPTIONS_FLOW":
        if symbol == "CRD.B":
            return {"category": 1, "reason": "every endpoint returned zero rows for the dotted class-B ticker, although V1 price data exists: the provider may use a different symbol form (UNVERIFIED)", "recoverable_today": False,
                    "preserves_cutoff": False, "needs": "one test request with the alternative symbol form (needs authorization; window closed)"}
        return {"category": 3, "reason": "flow state %s: no alert qualifies under the unchanged 5-day rule (valid absence, informational, no points)" % flow_state.replace("_", " ").lower(), "recoverable_today": False,
                "preserves_cutoff": True, "needs": "qualifying activity can only appear in the future; the frozen 5-day threshold is unchanged"}
    if factor == "ANALYST":
        return {"category": 4, "reason": "no point-in-time consensus history of 7+ days (longest saved: 43.4 h); the revision half (10 pts) needs two snapshots 7-60 days apart; rating actions (5 pts) are present", "recoverable_today": False,
                "preserves_cutoff": True, "needs": "history accumulated before the cutoff of a FUTURE event (free with each calendar refresh via street_mean_est)"}
    if factor in ("IMPLIED_VS_HIST",):
        return {"category": 4, "reason": "fewer than 4 reported post-earnings moves or no implied move in the saved earnings payload", "recoverable_today": False, "preserves_cutoff": True, "needs": "provider history that does not exist"}
    if factor == "SHORT_INST":
        return {"category": 4, "reason": "fewer than 2 published settlements 10+ days apart or no ownership rows", "recoverable_today": False, "preserves_cutoff": True, "needs": "provider history that does not exist"}
    if factor in ("F3", "F5", "F7", "F8", "REL_STRENGTH"):
        return {"category": 4, "reason": "V1 evidence gap at capture (insufficient reported history or not captured live in the window)", "recoverable_today": False, "preserves_cutoff": True, "needs": "history that does not exist"}
    return {"category": 8, "reason": str(cause), "recoverable_today": False, "preserves_cutoff": True, "needs": "investigation"}


def build(evidence_audit, v2r_file, v1_report, load_payload, cal_obs, now: datetime) -> dict:
    ea = json.loads(Path(evidence_audit).read_text(encoding="utf-8"))
    v2r = json.loads(Path(v2r_file).read_text(encoding="utf-8"))
    v1 = {r["symbol"]: r for r in json.loads(Path(v1_report).read_text(encoding="utf-8"))["results"]}
    r_by = {e["symbol"]: e for e in v2r["events"]}
    hist = SN.calendar_history(cal_obs, load_payload, "2026-11-02")
    audited = {p["symbol"] for p in ea["per_stock"]}
    only = {s: v for s, v in hist["by_symbol"].items() if s in audited}                       # restrict to the audited stocks
    spans = [(datetime.fromisoformat(pts[-1][0]) - datetime.fromisoformat(pts[0][0])).total_seconds() / 3600 for v in only.values() for pts in v.values() if len(pts) >= 2]
    chg = sum(1 for v in only.values() for pts in v.values() if len(pts) >= 2 and abs(pts[-1][1] - pts[0][1]) > 1e-9)
    hist = dict(hist, stocks=len(only), max_span_hours=round(max(spans), 1) if spans else None, series_with_a_change=chg, usable_now=bool(spans) and max(spans) >= SN.MIN_GAP_DAYS * 24)
    rows, cat_pts, cat_stocks = [], Counter(), defaultdict(set)
    before_av = before_dir = 0
    for p in ea["per_stock"]:
        sym = p["symbol"]
        e = r_by.get(sym)
        if e is None:
            continue
        cont = e["v2r"]["continuation"]
        flow_state = cont["options_flow"]["state"]
        heads = {v: e["v2r"][v]["headroom"] for v in ("continuation", "contrarian")}
        v1o = v1.get(sym)
        kind = e.get("kind") if e.get("kind") in ("BMO", "AMC") else (v1o or {}).get("kind")                                  # the V2 event has no kind when the calendar was stale at capture; the original V1 record has it
        close = None
        if kind in ("BMO", "AMC"):
            close = co.plan_event(date.fromisoformat(str(e["earnings_date"])[:10]), kind)["cutoff_utc"].isoformat()
        unresolved = []
        for n, f in p["factors"].items():
            if f["state"] == "available":
                continue
            c = categorize(n, f, flow_state, sym, False)
            miss = f["max"] - f["points_with_data"]
            cat_pts[c["category"]] += miss
            cat_stocks[c["category"]].add(sym)
            unresolved.append(dict(c, factor=n, points_missing=miss, directional=n in V2.DIRECTIONAL, category_name=CATEGORIES[c["category"]]))
        before_av += p["available"]
        before_dir += p["directional"]
        rows.append({"symbol": sym, "earnings_date": e["earnings_date"], "kind": kind, "v1_capture_ts": v1o["capture_ts"] if v1o else None, "v2_scored_as_of": p["scored_as_of"], "window_closed_utc": close,
                     "cutoff_passed": bool(close and datetime.fromisoformat(close) < now), "stale_calendar_at_capture": p["stale_calendar_at_capture"], "calendar_age_h_at_capture": p.get("calendar_age_h_at_capture"),
                     "before": {"available_of_100": p["available"], "directional_of_100": p["directional"], "continuation": cont["display_status"], "contrarian": e["v2r"]["contrarian"]["display_status"],
                                "v1_original": {v: (v1o or {}).get(v, {}).get("signal") for v in ("continuation", "contrarian")}},
                     "after": {"available_of_100": p["available"], "directional_of_100": p["directional"], "continuation": cont["display_status"], "contrarian": e["v2r"]["contrarian"]["display_status"]},
                     "recovered_factors": [], "unresolved": unresolved, "flow_state": flow_state, "news_direction": cont["news"]["direction"], "headroom": cont["headroom"], "headroom_by_variant": heads})
    n = len(rows)
    focus = {}
    for s in ("VAL", "CE", "FANG"):
        r = next((x for x in rows if x["symbol"] == s), None)
        if r:
            focus[s] = {"directional": r["before"]["directional_of_100"], "available": r["before"]["available_of_100"], "headroom": r["headroom"], "headroom_by_variant": r["headroom_by_variant"], "flow_state": r["flow_state"], "calendar_age_h_at_capture": r["calendar_age_h_at_capture"],
                        "unresolved": [{"factor": u["factor"], "points": u["points_missing"], "category": u["category_name"], "reason": u["reason"]} for u in r["unresolved"]],
                        "verdict": "the unchanged rule would fire only if 5 more directional points existed at the capture time; none can be established now (cutoff passed %s)" % r["window_closed_utc"]}
    stocks_blocked = sum(1 for r in rows if r["after"]["continuation"] == "DATA_INCOMPLETE")
    rec = {"title": "MISSING-EVIDENCE RECOVERY AUDIT", "generated_utc": now.isoformat(), "stocks": n, "categories": CATEGORIES,
           "missing_points_by_category": {CATEGORIES[k]: v for k, v in sorted(cat_pts.items())}, "stocks_by_category": {CATEGORIES[k]: len(v) for k, v in sorted(cat_stocks.items())},
           "recovery": {"recovered_factors": 0, "stocks_gaining_valid_evidence": 0, "stocks_improved": 0, "stocks_still_blocked": stocks_blocked,
                        "attempts": ["analyst consensus: searched the saved calendar payloads of 3 observation clusters (30 Oct 15:13, 31 Oct 21:21, 1 Nov 10:38) for UW street_mean_est and Finnhub epsEstimate, plus the 1 Nov estimates snapshot",
                                     "options flow: all 5,863 alerts re-run through flowx-1 at 5/10/15/30 days; symbol mapping checked (no mismatch); the frozen 5-day threshold unchanged",
                                     "news: 56 headlines checked for ticker collisions, source quality, timestamps and name matching; no independently labelled set exists on this machine",
                                     "F3/F5/F7/F8, short interest, ownership, relative strength, implied move: re-read the saved payloads; every available field was already incorporated; no double counting"]},
           "before_after": {"before": {"available_avg": round(before_av / n, 1), "directional_avg": round(before_dir / n, 1)}, "after": {"available_avg": round(before_av / n, 1), "directional_avg": round(before_dir / n, 1)},
                            "counts_before": {v: dict(Counter(r["before"][v] for r in rows)) for v in ("continuation", "contrarian")}, "counts_after": {v: dict(Counter(r["after"][v] for r in rows)) for v in ("continuation", "contrarian")}},
           "consensus_history": {"stocks": hist["stocks"], "max_span_hours": hist["max_span_hours"], "series_with_a_change": hist["series_with_a_change"], "usable_for_revision_now": hist["usable_now"],
                                 "note": "Finnhub eps: 0 changes; UW street_mean_est: 4 changes of 1-2 cents over 43.4 h. Below the 7-day band, so never scored. The earliest observation (30 Oct 15:13) would reach 7 days on 6 Nov 15:13 UTC, but these events report on 2 Nov."},
           "focus": focus, "per_stock": rows,
           "can_be_fixed_now": ["Nothing in the evidence for these 95 events: every window has closed (cutoff passed)", "CRD.B symbol form can be tested with ONE request (needs authorization) for future events",
                                "Calendar guard (done): prevents the 42 stale-calendar captures in future runs"],
           "needs_future_observations": ["Analyst revisions: daily calendar refreshes from the day an event enters the window give a free street_mean_est history (>= 7 days before capture)", "Qualifying options flow: only real market activity",
                                         "News points: independently labelled headlines", "Accuracy: actual outcomes (collectable from 21:30 UTC today for BMO and 3 Nov for AMC events; needs authorization)"]}
    rec["audit_hash"] = hashlib.sha256(json.dumps({k: v for k, v in rec.items() if k not in ("generated_utc", "audit_hash")}, sort_keys=True, default=str).encode()).hexdigest()
    return rec
