"""Read-only data-quality audit over SAVED evidence: options-flow filtering, news classification, per-stock DATA INCOMPLETE causes, analyst-history readiness.

No provider request, no score, threshold or prediction is changed, and nothing in a store is written (readiness works on an in-memory copy). Alerts and headlines are read through the same
payload loader the recovery audit uses; classification reuses the existing transparent modules (flowx-1 and news-x1) so no rule is re-implemented or loosened here.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import smart_move_flowx as FX
import smart_move_news as NW

UTC = timezone.utc
GATE_REASON = {"outside_lookback": "older than the 5-day lookback", "multi_leg": "multi-leg (direction ambiguous)", "expiry_before_earnings": "expiry before the earnings date (no earnings exposure)",
               "volume_oi_missing": "volume/open-interest missing", "volume_below_open_interest": "volume below open interest (not new positioning)", "type_unknown": "call/put unknown",
               "side_not_attributable": "ask and bid premium both zero (side not attributable)", "bad_timestamp": "unparseable timestamp", "future_stamped_never_used": "stamped after the capture time"}


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def integrity(alerts: list, symbol: str, capture_ts: datetime) -> dict:
    """Field-level checks on the raw alerts of one stock (read only)."""
    c = Counter()
    seen = set()
    for a in alerts:
        c["alerts"] += 1
        if str(a.get("ticker", "")).upper() != symbol.upper():
            c["symbol_mismatch"] += 1
        if str(a.get("type", "")).lower() not in ("call", "put"):
            c["type_not_call_or_put"] += 1
        key = (a.get("option_chain"), a.get("created_at"), a.get("total_premium"))
        if key in seen:
            c["duplicate"] += 1
        seen.add(key)
        for k in ("created_at", "expiry", "total_premium", "volume_oi_ratio", "total_ask_side_prem", "total_bid_side_prem"):
            if a.get(k) in (None, ""):
                c["null_" + k] += 1
        prem, ask, bid = _f(a.get("total_premium")), _f(a.get("total_ask_side_prem")) or 0.0, _f(a.get("total_bid_side_prem")) or 0.0
        if prem is not None and ask + bid > prem * 1.001:
            c["sides_exceed_premium"] += 1
        if prem and ask + bid < prem * 0.999:
            c["mid_price_share_unattributed"] += 1
        t = FX._t(a.get("created_at"))
        if t is not None and t > capture_ts:
            c["stamped_after_capture"] += 1
        if a.get("has_sweep"):
            c["sweeps"] += 1
        if str(a.get("type", "")).lower() == "call":
            c["call"] += 1
        elif str(a.get("type", "")).lower() == "put":
            c["put"] += 1
    return dict(c)


def flow_audit(events: Iterable[dict], evidence: Iterable[dict], load: Callable[[str], Optional[dict]]) -> dict:
    """events: [{symbol, earnings_date, scored_as_of}]; evidence: v2_evidence rows. Returns per-stock state plus an accepted / rejected / unavailable / failed table."""
    rec = {r["symbol"]: r for r in evidence if r.get("kind") == "uw_flow_alerts"}
    per, totals, states, integ = [], Counter(), Counter(), Counter()
    unavailable = failed = 0
    for e in events:
        sym = e["symbol"]
        cap = FX._t(e["scored_as_of"])
        r = rec.get(sym)
        if r is None:
            unavailable += 1
            per.append({"symbol": sym, "state": "NOT_COLLECTED"})
            states["NOT_COLLECTED"] += 1
            continue
        if r.get("status") != "OK":
            failed += 1
            per.append({"symbol": sym, "state": "API_FAILURE", "status": r.get("status")})
            states["API_FAILURE"] += 1
            continue
        body = load(r["raw_sha256"])
        alerts = (body or {}).get("data") if isinstance(body, dict) else None
        if alerts is None:
            failed += 1
            per.append({"symbol": sym, "state": "PAYLOAD_UNREADABLE"})
            states["PAYLOAD_UNREADABLE"] += 1
            continue
        a = FX.analyse(alerts, e["earnings_date"], cap, 5)
        for k, v in a["waterfall"].items():
            totals[k] += v
        for k, v in integrity(alerts, sym, cap).items():
            integ[k] += v
        states[a["state"]] += 1
        per.append({"symbol": sym, "state": a["state"], "returned": a["waterfall"].get("returned", 0), "inside_lookback": a["waterfall"].get("inside_lookback", 0), "used": a["alerts_used"],
                    "attributed_premium": a["attributed_premium"], "net_ratio": a["net_ratio"], "shortfall": a.get("shortfall"), "lean": a.get("direction"),
                    "dominant_rejection": max(((k, v) for k, v in a["waterfall"].items() if k in GATE_REASON), key=lambda kv: kv[1], default=(None, 0))[0]})
    rejected = {k: v for k, v in totals.items() if k in GATE_REASON}
    return {"stocks": len(per), "table": {"accepted_alerts": totals.get("used", 0), "rejected_alerts": sum(rejected.values()), "returned_alerts": totals.get("returned", 0),
                                          "unavailable_stocks": unavailable, "failed_stocks": failed},
            "rejected_by_reason": {GATE_REASON[k]: v for k, v in sorted(rejected.items(), key=lambda kv: -kv[1])}, "stock_states": dict(states), "integrity": dict(integ), "per_stock": per,
            "rule": FX.RULE, "note": "No filter was changed. Rejections are counted exactly as the existing rule applies them; a lean below the minimum earns no points."}


def news_audit(events: Iterable[dict], evidence: Iterable[dict], load: Callable[[str], Optional[dict]], companies: dict, sample: int = 40) -> dict:
    """Headlines from saved news payloads, each with the classifier's label and reason (research only). Only stocks with a saved news payload can be audited."""
    rec = {r["symbol"]: r for r in evidence if r.get("kind") == "uw_news" and r.get("status") == "OK"}
    ev = {e["symbol"]: e for e in events}
    rows, labels, cats, dup = [], Counter(), Counter(), Counter()
    seen = Counter()
    for sym, r in rec.items():
        e = ev.get(sym)
        if e is None:
            continue
        body = load(r["raw_sha256"])
        cap = FX._t(e["scored_as_of"])
        for h in (body or {}).get("data") or []:
            key = (str(h.get("headline", "")).strip().lower())
            seen[key] += 1
            c = NW.classify(h, sym, companies.get(sym), cap)
            labels[c["label"]] += 1
            cats[c.get("category") or "-"] += 1
            created = FX._t(h.get("created_at"))
            rows.append({"symbol": sym, "headline": h.get("headline"), "source": h.get("source"), "published": h.get("created_at"), "retrieved_at": r["retrieved_at"],
                         "tickers_tagged": h.get("tickers"), "provider_sentiment": h.get("sentiment"), "label": c["label"], "category": c.get("category"), "reason": c.get("reason"),
                         "published_after_capture": bool(created and created > cap), "published_before_retrieval": bool(created and created <= FX._t(r["retrieved_at"]))})
    for k, n in seen.items():
        if n > 1:
            dup[k] = n
    collected = sorted(rec)
    return {"stocks_with_saved_news": len(collected), "stocks_without_saved_news": len(ev) - len([s for s in collected if s in ev]), "stocks": collected, "headlines": len(rows), "labels": dict(labels), "categories": dict(cats),
            "provider_sentiment_values": dict(Counter(str(r["provider_sentiment"]) for r in rows)), "duplicate_headlines": len(dup), "after_capture": sum(1 for r in rows if r["published_after_capture"]),
            "sample": rows[:sample], "approval": "NEWS_GUIDANCE stays blocked: no labelled ground truth exists, so classifier accuracy is not demonstrated. Owner approval is required before it scores.",
            "note": "Only stocks with a saved news payload could be audited; the rest were never collected (DATA NOT COLLECTED), not neutral."}


PROBLEM_KIND = {1: "mapping defect", 2: "collection failure (saved but unused)", 3: "genuine absence of evidence", 4: "provider limitation (history unavailable)", 5: "scoring gate (unverified semantics)",
                6: "collection failure (stale calendar at capture)", 7: "provider limitation (API error)", 8: "implementation defect"}


def root_cause_table(recovery: dict) -> dict:
    """Per stock and unresolved factor: cause, whether the data existed before the cutoff, whether stored data can fix it. From the saved recovery audit (cutoff already passed for every event)."""
    rows = []
    for p in recovery["per_stock"]:
        for u in p["unresolved"]:
            cat = u["category"]
            rows.append({"symbol": p["symbol"], "factor": u["factor"], "points_missing": u["points_missing"], "category": cat, "problem_kind": PROBLEM_KIND.get(cat, str(cat)), "reason": u["reason"],
                         "existed_before_cutoff": cat == 2, "fixable_from_stored_data": bool(u.get("recoverable_today")),
                         "future_collection_required": cat in (4, 6, 7) or u["factor"] in ("ANALYST",)})
    by = defaultdict(lambda: Counter())
    for r in rows:
        by[r["problem_kind"]][r["factor"]] += 1
    return {"stocks": len(recovery["per_stock"]), "rows": rows, "by_problem_kind": {k: dict(v) for k, v in by.items()}, "fixable_from_stored_data": sum(1 for r in rows if r["fixable_from_stored_data"]),
            "note": "existed_before_cutoff is true only where evidence was saved but not used; it is false for absent or later data. Nothing here rewrites a prediction."}


def analyst_readiness(snapshot_rows: list, symbols: Iterable[str], now: datetime) -> dict:
    """How the seven-day consensus history will accumulate, from the saved snapshots only (in memory; the store is not written)."""
    by = defaultdict(list)
    for r in snapshot_rows:
        by[r["symbol"]].append(r)
    syms = list(symbols)
    first = min((r["retrieved_at"] for r in snapshot_rows), default=None)
    have = sum(1 for s in syms if by.get(s))
    measurable = 0
    for s in syms:
        t = sorted(datetime.fromisoformat(r["retrieved_at"]) for r in by.get(s, []))
        if len(t) >= 2 and 7 <= (t[-1] - t[0]).days <= 60:
            measurable += 1
    earliest = (datetime.fromisoformat(first) + timedelta(days=7)).isoformat() if first else None
    return {"stocks": len(syms), "with_a_snapshot": have, "revision_measurable_now": measurable, "first_snapshot": first, "earliest_second_snapshot_useful": earliest,
            "gap_days_required": 7, "max_age_days": 60, "as_of": now.isoformat(),
            "to_enable_later": ["a separately authorized, recurring collection of the earnings-estimates endpoint (one request per stock per day or at least once every 7 days)",
                                "persist the v2_estimate_snapshots ledger (rebuild is idempotent and append-only) from the evidence records",
                                "events whose cutoff is at least 7 days after the first snapshot can then score the revision half (10 of 15 ANALYST points)"],
            "note": "Events already past their cutoff cannot be repaired. The action half (5 points) does not depend on history."}


NEWS_STATES = ("OK", "FILTERED_ONLY", "NO_PRE_CUTOFF_NEWS", "EMPTY", "FAILED", "NOT_ATTEMPTED")


def news_coverage(events: Iterable[dict], evidence: Iterable[dict], load: Callable[[str], Optional[dict]], companies: dict, failed_calls: Iterable[dict] = ()) -> dict:
    """Per stock: was news attempted, and what came back. Successful, empty, failed, filtered and not-attempted are kept apart (an empty or missing record is never read as neutral news).

    OK = at least one in-window, pre-cutoff, entity-verified headline; FILTERED_ONLY = headlines in window but none verified; NO_PRE_CUTOFF_NEWS = headlines exist but none inside the lookback before the capture;
    EMPTY = the provider answered with zero rows; FAILED = a saved record that is not OK; NOT_ATTEMPTED = no request was ever recorded. NEWS_GUIDANCE scoring is blocked regardless."""
    recs = defaultdict(list)
    for r in evidence:
        if r.get("kind") == "uw_news":
            recs[r["symbol"]].append(r)
    states, per, headlines, pre_cutoff, dup = Counter(), [], 0, 0, 0
    failed_syms = {f["symbol"] for f in failed_calls if f.get("kind") == "uw_news"}          # failed calls leave no evidence record; the run report lists them (symbol, kind)
    for e in events:
        sym, cap = e["symbol"], FX._t(e["scored_as_of"])
        rs = recs.get(sym)
        if not rs and sym in failed_syms:
            states["FAILED"] += 1
            per.append({"symbol": sym, "state": "FAILED", "statuses": ["CALL_FAILED_NO_RECORD"]})
            continue
        if not rs:
            states["NOT_ATTEMPTED"] += 1
            per.append({"symbol": sym, "state": "NOT_ATTEMPTED"})
            continue
        ok = [r for r in rs if r.get("status") == "OK"]
        if not ok:
            states["FAILED"] += 1
            per.append({"symbol": sym, "state": "FAILED", "statuses": sorted({str(r.get("status")) for r in rs})})
            continue
        rows, seen = [], set()
        for r in ok:
            for h in (load(r["raw_sha256"]) or {}).get("data") or []:
                k = (h.get("created_at"), h.get("source"), str(h.get("headline", "")).strip().lower())
                if k in seen:
                    dup += 1
                    continue
                seen.add(k)
                rows.append(h)
        headlines += len(rows)
        if not rows:
            states["EMPTY"] += 1
            per.append({"symbol": sym, "state": "EMPTY"})
            continue
        cls = [NW.classify(h, sym, companies.get(sym), cap) for h in rows]
        in_window = [c for c in cls if c["category"] not in ("OUT_OF_WINDOW", "NOT_PRE_ANNOUNCEMENT", "NO_TIMESTAMP")]
        verified = [c for c in in_window if c["label"] != "UNVERIFIED"]
        pre_cutoff += len(in_window)
        st = "OK" if verified else "FILTERED_ONLY" if in_window else "NO_PRE_CUTOFF_NEWS"
        states[st] += 1
        per.append({"symbol": sym, "state": st, "headlines": len(rows), "in_window_pre_cutoff": len(in_window), "verified": len(verified)})
    n = len(per)
    attempted = n - states["NOT_ATTEMPTED"]
    return {"eligible_stocks": n, "news_attempted": attempted, "states": {k: states.get(k, 0) for k in NEWS_STATES}, "stocks_with_stored_news": sum(states.get(k, 0) for k in ("OK", "FILTERED_ONLY", "NO_PRE_CUTOFF_NEWS")),
            "stocks_without_stored_news": n - sum(states.get(k, 0) for k in ("OK", "FILTERED_ONLY", "NO_PRE_CUTOFF_NEWS")), "stored_headlines": headlines, "duplicate_headlines_removed": dup,
            "valid_pre_cutoff_headlines": pre_cutoff, "news_guidance_eligible": 0, "per_stock": per,
            "note": "news_guidance_eligible is 0 by design: NEWS_GUIDANCE scoring stays blocked until classification is validated on independently labelled headlines and the owner approves."}
