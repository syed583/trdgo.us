"""New research candidate v2r-1 (RESEARCH ONLY). Builds on the frozen V2 factor scores and the v2c-1 status taxonomy and adds the two transparent evidence layers built today:
  * flowx-1: options-flow qualification (the unchanged V2 rule) with lean, waterfall and lookback comparison on the same alerts;
  * news-x1: headline classification (POSITIVE / NEGATIVE / NEUTRAL / UNVERIFIED) with source, entity, timestamp and no-double-count checks. It is DISPLAYED, it earns 0 points until it is validated on independently
    labelled headlines.
Scoring framework: 100 points, every weight, factor rule, the 60-point directional minimum and the BUY/SELL thresholds are identical to the frozen V2 (scoring_changes = [], factor_redistribution = []).
So any BUY/SELL can only come from the unchanged rule; the candidate's job is to make every stock's evidence, binding constraint and missing evidence explicit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Optional

import smart_move_flowx as FX
import smart_move_news as NW
import smart_move_v2 as V2
import smart_move_v2c as C

VERSION = "v2r-1"
STATUS = "RESEARCH_CANDIDATE"
CONFIG = {"version": VERSION, "base_v2": V2.VERSION, "base_v2_hash": V2.CONFIG_HASH, "status_logic": C.VERSION, "status_logic_hash": C.CONFIG_HASH, "flowx": FX.VERSION, "flowx_rule": FX.RULE, "news": NW.VERSION,
          "news_points": 0, "weights": dict(V2.WEIGHTS), "decision": dict(V2.CONFIG["decision"]), "scoring_changes": [], "factor_redistribution": [], "fallbacks": []}
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True, default=str).encode()).hexdigest()
D = V2.CONFIG["decision"]


def headroom(v2_variant: dict) -> dict:
    """What the unchanged rule WOULD say if coverage were not the constraint: separates 'not enough evidence' from 'evidence does not point anywhere'."""
    b, e = v2_variant["bull"], v2_variant["bear"]
    buy = b >= D["bull_min"] and e <= D["bear_max"] and b - e >= D["margin_min"]
    sell = e >= D["bull_min"] and b <= D["bear_max"] and e - b >= D["margin_min"]
    return {"bull": b, "bear": e, "rule_would_fire_if_covered": "BUY" if buy else "SELL" if sell else None, "bull_gap_to_35": max(0, round(D["bull_min"] - b, 1)), "bear_gap_to_35": max(0, round(D["bull_min"] - e, 1))}


def evaluate(stock: dict, v2_event: dict, flow_alerts, headlines, company: Optional[str], capture_ts: datetime, release_ts: Optional[datetime], event_date: str) -> dict:
    """stock: per-stock row of the evidence audit; v2_event: the frozen V2 per-stock event; returns the candidate result for both variants."""
    flow = FX.analyse(flow_alerts, event_date, capture_ts, 5)
    flow["lookbacks"] = FX.compare_lookbacks(flow_alerts, event_date, capture_ts) if flow_alerts is not None else None
    news = NW.stock_direction(headlines, v2_event["symbol"], company, capture_ts, release_ts)
    res = {}
    for v in ("continuation", "contrarian"):
        base = C.classify(v2_event["v2"][v], stock)
        x = dict(base, model="V2R", version=VERSION, config_hash=CONFIG_HASH)
        hr = headroom(v2_event["v2"][v])
        missing = []
        for n, f in stock["factors"].items():
            if f["state"] != "available":
                missing.append({"factor": n, "state": f["state"], "cause": f.get("cause"), "points_without_data": f["max"] - f["points_with_data"]})
        x.update(options_flow={"direction": flow.get("direction"), "state": flow["state"], "qualifies": flow["qualifies"], "net_ratio": flow.get("net_ratio"), "alerts_used": flow.get("alerts_used"), "attributed_premium": flow.get("attributed_premium"),
                               "consistency": flow.get("consistency"), "lookbacks": flow.get("lookbacks"), "waterfall": flow.get("waterfall"), "shortfall": flow.get("shortfall")},
                 news={"direction": news["direction"], "reason": news["reason"], "verified_in_window": news["verified_in_window"], "seen": news["seen"], "scoring_points": 0, "validation": news["validation"]},
                 headroom=hr, missing_evidence=missing, evidence_score={"available_of_100": x["available_points"], "directional_of_100": x["directional_available_points"], "missing_of_100": x["missing_points"]})
        x["explanation"] = _explain(x, flow, news, hr)
        res[v] = x
    return res


def _explain(x: dict, flow: dict, news: dict, hr: dict) -> str:
    parts = ["%s." % x["explanation"]]
    parts.append("Options flow: %s (%s)." % (flow["state"].replace("_", " ").lower(), "direction " + flow["direction"].lower() if flow.get("direction") else "no direction") + (" Needs %s more alert(s) and $%s more attributed premium to qualify." % (flow["shortfall"]["alerts_needed"], format(flow["shortfall"]["premium_needed_usd"], ",")) if flow.get("shortfall") else ""))
    parts.append("News: %s (research only, 0 points)." % news["direction"].replace("_", " ").lower())
    if x["display_status"] == "DATA_INCOMPLETE" and hr["rule_would_fire_if_covered"]:
        parts.append("With full coverage the unchanged rule would say %s." % hr["rule_would_fire_if_covered"])
    elif x["display_status"] == "DATA_INCOMPLETE":
        parts.append("Even with full coverage the points so far (bull %g / bear %g) do not clear the rule." % (hr["bull"], hr["bear"]))
    return " ".join(parts)


def summarize(events: list) -> dict:
    out = {"counts": {}, "primary_causes": {}, "rule_would_fire_if_covered": {}, "flow_states": {}, "news_directions": {}}
    for v in ("continuation", "contrarian"):
        c, p, h = {}, {}, {}
        for e in events:
            x = e["v2r"][v]
            c[x["display_status"]] = c.get(x["display_status"], 0) + 1
            if x["display_status"] == "DATA_INCOMPLETE":
                p[x["primary_cause"]] = p.get(x["primary_cause"], 0) + 1
            k = x["headroom"]["rule_would_fire_if_covered"]
            if k:
                h[k] = h.get(k, 0) + 1
        out["counts"][v], out["primary_causes"][v], out["rule_would_fire_if_covered"][v] = c, p, h
    for e in events:
        x = e["v2r"]["continuation"]
        out["flow_states"][x["options_flow"]["state"]] = out["flow_states"].get(x["options_flow"]["state"], 0) + 1
        out["news_directions"][x["news"]["direction"]] = out["news_directions"].get(x["news"]["direction"], 0) + 1
    return out
