"""Smart Move V2 CANDIDATE (v2c-1): a separately versioned RESEARCH model. Frozen V1 and the frozen V2 (v2-exp-2) are untouched and remain the baselines.

What changed (and what did not), decided only from evidence available today:
  UNCHANGED  the ten factors and their 100 points, every weight, every factor rule, the 60-point DIRECTIONAL minimum, the BUY/SELL thresholds (bull >= 35, bear <= 10, margin >= 25), the V1 eligibility gates.
  UNCHANGED  the options-flow lookback stays 5 days: the point-in-time backtest (smart_move_flow_backtest.py) is INCONCLUSIVE (3-8 decided events per window, no window can be ranked).
  UNCHANGED  the analyst revision factor stays unavailable: the only saved consensus history is 13.3 h long (116 stocks, 0 changes), below the 7-day band; nothing is manufactured.
  UNCHANGED  news/guidance stays BLOCKED (the `sentiment` label is undefined and all 56 saved values are "neutral"); its 10 points are NOT redistributed and NOT given away.
  CHANGED    the STATUS LOGIC only. Frozen V2 reported 90 of 95 stocks as DATA INCOMPLETE regardless of why. The candidate assigns every stock exactly one status category, so a
             genuine decision is no longer mixed with missing data:
               GENUINE_NO_TRADE   enough directional evidence and the rule says no, OR a known ineligibility with complete data (20-day dollar volume known and < $20M), as the V1 board does
               DATA_INCOMPLETE    fewer than 60 directional points or an event the gates cannot confirm; the primary cause is named: STALE_CALENDAR (captured against a calendar older than 6 h),
                                  API_FAILED, MISSING_REQUIRED (not collected / history missing), then the secondary causes: VALID_ABSENCE (flow endpoint answered, nothing qualifying; informational,
                                  no directional credit), UNVERIFIED_SEMANTICS (news)
               BUY / SELL         only through the unchanged rule.
No missing evidence becomes a point; a valid absence of flow adds no directional points. Research only: not for decisions; accuracy is NOT VERIFIED (no outcomes exist).
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Optional

import smart_move_v2 as V2

VERSION = "v2c-1"
STATUS = "RESEARCH_CANDIDATE"
CONFIG = {"version": VERSION, "base_version": V2.VERSION, "base_config_hash": V2.CONFIG_HASH, "weights": dict(V2.WEIGHTS), "decision": copy.deepcopy(V2.CONFIG["decision"]),
          "flow_lookback_days": V2.CONFIG["options_flow"]["lookback_days"], "scoring_changes": [], "factor_redistribution": [], "fallbacks": [],
          "status_logic": ["GENUINE_NO_TRADE for known ineligibility with complete data (known 20-day dollar volume < $20M)", "named primary cause for every DATA_INCOMPLETE", "valid absence of flow reported as informational, no directional credit"]}
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()
DIRECTIONAL = V2.DIRECTIONAL
CAUSE_ORDER = ["STALE_CALENDAR", "API_FAILED", "MISSING_REQUIRED", "EVENT_NOT_CONFIRMED"]


def _factor_cause(name: str, f: dict) -> Optional[str]:
    """Why a factor has no/partial directional data, as one of the five categories. None = it has its data."""
    c = f.get("cause")
    if f["state"] == "available":
        return None
    if c == "STALE_CALENDAR_AT_CAPTURE":
        return "STALE_CALENDAR"
    if c == "API_FAILURE":
        return "API_FAILED"
    if c in ("GENUINE_ABSENCE", "FILTERED_BY_RULE") and name == "OPTIONS_FLOW":
        return "VALID_ABSENCE"
    if c == "UNVERIFIED_SEMANTICS":
        return "UNVERIFIED_SEMANTICS"
    if c in ("NOT_COLLECTED_YET", "INSUFFICIENT_HISTORY", "V1_EVIDENCE_GAP", "OTHER"):
        return "MISSING_REQUIRED"
    return "MISSING_REQUIRED"


def classify(v2_result: dict, stock: dict) -> dict:
    """v2_result: one variant of the frozen V2 result (signal, bull, bear, directional_available_points, ...). stock: the per-stock row of the evidence audit
    (factors with cause, eligibility, stale_calendar_at_capture). Returns the candidate's own result for that variant."""
    dir_av = v2_result["directional_available_points"]
    gates = set((stock.get("eligibility") or {}).get("v1_gates") or [])
    f5_ok = (stock["factors"].get("F5") or {}).get("state") == "available"
    causes = {n: _factor_cause(n, f) for n, f in stock["factors"].items()}
    gap = {n: c for n, c in causes.items() if c and (n in DIRECTIONAL or c == "VALID_ABSENCE")}
    out = {"model": "V2C", "version": VERSION, "status": STATUS, "config_hash": CONFIG_HASH, "variant": v2_result["variant"], "bull": v2_result["bull"], "bear": v2_result["bear"],
           "available_points": v2_result["available_points"], "directional_available_points": dir_av, "max": 100, "missing_points": v2_result["missing_points"],
           "factor_gaps": gap, "research_only": True, "accuracy": "NOT VERIFIED", "frozen_v2_signal": v2_result["signal"]}
    if stock.get("stale_calendar_at_capture"):
        return dict(out, signal="NO_TRADE", display_status="DATA_INCOMPLETE", category="DATA_INCOMPLETE", primary_cause="STALE_CALENDAR",
                    secondary_causes=sorted({c for c in gap.values() if c != "STALE_CALENDAR"}), explanation="captured against an earnings calendar older than 6 h (frozen rule); the event could not be confirmed at capture time")
    if "ILLIQUID_OR_UNKNOWN" in gates and f5_ok:
        return dict(out, signal="NO_TRADE", display_status="NO_TRADE", category="GENUINE_NO_TRADE", primary_cause="ILLIQUID_KNOWN", secondary_causes=sorted(set(gap.values())),
                    explanation="20-day average dollar volume is known and below $20M: a valid, evidence-complete ineligibility, not missing data")
    other_gates = sorted(gates & {"NOT_TRADABLE", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "OUT_OF_WINDOW", "STALE_EVIDENCE", "SUSPECT_WINDOW", "BLOCKED_BY_GATE"})
    if other_gates:
        return dict(out, signal="NO_TRADE", display_status="DATA_INCOMPLETE", category="DATA_INCOMPLETE", primary_cause="EVENT_NOT_CONFIRMED", secondary_causes=sorted(set(gap.values())),
                    explanation="the V1 eligibility gates do not confirm a tradable event: " + ", ".join(other_gates))
    if dir_av < V2.CONFIG["decision"]["min_directional_available"]:
        prim = next((c for c in CAUSE_ORDER if c in gap.values()), "MISSING_REQUIRED")
        return dict(out, signal="NO_TRADE", display_status="DATA_INCOMPLETE", category="DATA_INCOMPLETE", primary_cause=prim, secondary_causes=sorted(set(gap.values()) - {prim}),
                    explanation="%g of the required 60 directional points have data; gaps: %s" % (dir_av, ", ".join("%s (%s)" % (n, c) for n, c in sorted(gap.items())) or "none"))
    sig = v2_result["signal"]
    if sig in ("BUY", "SELL"):
        return dict(out, signal=sig, display_status=sig, category=sig, primary_cause="RULE", secondary_causes=sorted(set(gap.values())), explanation="the unchanged V2 rule is met with >= 60 directional points; research only")
    return dict(out, signal="NO_TRADE", display_status="NO_TRADE", category="GENUINE_NO_TRADE", primary_cause="BELOW_MARGIN", secondary_causes=sorted(set(gap.values())),
                explanation="enough directional evidence and the unchanged rule does not fire")


def summarize(results: list) -> dict:
    """results: list of per-stock candidate dicts {continuation, contrarian}. Exact counts for both variants and the primary-cause breakdown."""
    out = {"counts": {}, "primary_causes": {}}
    for v in ("continuation", "contrarian"):
        c, p = {}, {}
        for r in results:
            x = r[v]
            c[x["display_status"]] = c.get(x["display_status"], 0) + 1
            if x["display_status"] == "DATA_INCOMPLETE":
                p[x["primary_cause"]] = p.get(x["primary_cause"], 0) + 1
        out["counts"][v], out["primary_causes"][v] = c, p
    return out
