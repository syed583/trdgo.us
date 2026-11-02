"""New research candidate v2r-1: unchanged scoring, transparent flow/news layers, headroom (why no signal), independent BUY/SELL per variant, route, no promotion."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_v2 as V2  # noqa: E402
import smart_move_v2r as R  # noqa: E402

from test_flowx_news import CAP, alert, hl  # noqa: E402
from test_v2 import full_factors  # noqa: E402
from test_v2c_and_flow_backtest import stock as _stock  # noqa: E402

UTC = timezone.utc
ED = "2026-11-03"


def stock(*a, **k):
    """The evidence-audit per-stock row shape: every factor also carries its max and the points that have data."""
    st = _stock(*a, **k)
    for n, f in st["factors"].items():
        f["max"] = V2.WEIGHTS[n]
        f["points_with_data"] = 0 if f["state"] == "missing" else V2.WEIGHTS[n] / 2 if f["state"] == "partial" else V2.WEIGHTS[n]
    return st


def v2event(direction=1):
    return {"symbol": "AAPL", "earnings_date": ED, "kind": "AMC", "v2": {v: V2.score(full_factors(v, direction), v1_gates=[], variant=v) for v in ("continuation", "contrarian")}}


def test_the_research_candidate_changes_no_scoring_input_and_awards_news_zero_points():
    assert R.CONFIG["weights"] == V2.WEIGHTS and R.CONFIG["decision"] == V2.CONFIG["decision"] and R.CONFIG["scoring_changes"] == [] and R.CONFIG["factor_redistribution"] == [] and R.CONFIG["news_points"] == 0
    assert R.CONFIG["flowx_rule"] == {"min_alerts": 3, "min_attributed_premium_usd": 500_000.0, "strong_net_ratio": 0.5, "moderate_net_ratio": 0.25, "sweep_weight": 1.5, "min_volume_oi_ratio": 1.0}
    assert R.VERSION == "v2r-1" and R.CONFIG_HASH not in (V2.CONFIG_HASH,)


def test_buy_and_sell_are_evaluated_independently_for_both_variants_and_only_by_the_unchanged_rule():
    for direction, want in ((1, "BUY"), (-1, "SELL")):
        ev = v2event(direction)
        out = R.evaluate(stock(), ev, [alert("call", 300_000)] * 3, [hl("Apple raises guidance $AAPL"), hl("Apple wins contract $AAPL")], "APPLE", CAP, CAP + timedelta(days=1), ED)
        assert out["continuation"]["display_status"] == want and out["continuation"]["research_only"] is True
        assert out["contrarian"]["variant"] == "contrarian" and out["contrarian"]["display_status"] == ev["v2"]["contrarian"]["signal"]
        assert out["continuation"]["news"]["direction"] == "POSITIVE" and out["continuation"]["news"]["scoring_points"] == 0                    # shown, never scored
        assert out["continuation"]["options_flow"]["direction"] == "BULLISH" and out["continuation"]["options_flow"]["qualifies"] is True
        assert out["continuation"]["bull"] == ev["v2"]["continuation"]["bull"] and out["continuation"]["directional_available_points"] == ev["v2"]["continuation"]["directional_available_points"]


def test_headroom_separates_not_enough_evidence_from_evidence_that_points_nowhere():
    strong = dict(v2event(1)["v2"]["continuation"], directional_available_points=55.0, signal="NO_TRADE")
    assert R.headroom(strong)["rule_would_fire_if_covered"] == "BUY"
    weak = dict(strong, bull=12.0, bear=9.0)
    assert R.headroom(weak)["rule_would_fire_if_covered"] is None and R.headroom(weak)["bull_gap_to_35"] == 23.0
    ev = v2event(1)
    ev["v2"]["continuation"] = strong
    st = stock({"ANALYST": ("partial", "INSUFFICIENT_HISTORY"), "NEWS_GUIDANCE": ("missing", "UNVERIFIED_SEMANTICS")})
    out = R.evaluate(st, ev, [alert("call", 100_000)] * 2, None, "APPLE", CAP, None, ED)["continuation"]
    assert out["display_status"] == "DATA_INCOMPLETE" and out["headroom"]["rule_would_fire_if_covered"] == "BUY"
    assert "would say BUY" in out["explanation"] and "more alert(s)" in out["explanation"] and out["options_flow"]["state"] == "LEAN_ONLY_BELOW_MINIMUM"
    assert {m["factor"] for m in out["missing_evidence"]} == {"ANALYST", "NEWS_GUIDANCE"} and out["news"]["direction"] == "NOT_COLLECTED"


def test_the_route_exposes_flow_direction_news_direction_missing_evidence_and_the_research_label(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import smart_move_local_server as srv
    from test_audit import make_run
    root = make_run(tmp_path)
    ev = v2event(1)
    out = R.evaluate(stock({"ANALYST": ("partial", "INSUFFICIENT_HISTORY")}), ev, [alert("call", 300_000)] * 3, None, "APPLE", CAP, None, ED)
    body = {"version": "v2r-1", "status": "RESEARCH_CANDIDATE", "config_hash": "h", "base_v2": "v2-exp-2", "summary": R.summarize([{"v2r": out}]), "scoring_changes": [], "factor_redistribution": [], "accuracy": "NOT VERIFIED", "audit_hash": "a",
            "events": [{"symbol": "S00", "earnings_date": "2026-11-02", "scored_as_of": "t", "v2r": out}]}
    (tmp_path / "v2").mkdir()
    (tmp_path / "v2" / "v2r_run_x.json").write_text(json.dumps(body, default=str))
    monkeypatch.setenv("SMART_MOVE_RUN_ROOT", str(root))
    monkeypatch.setenv("SMART_MOVE_V2_DIR", str(tmp_path / "v2"))
    j = TestClient(srv.app).get("/api/smart-move/v2r?variant=contrarian").json()
    e = j["events"][0]
    assert j["usable_for_decisions"] is False and "RESEARCH ONLY" in j["banner"] and j["accuracy"] == "NOT VERIFIED" and j["scoring_changes"] == []
    assert e["options_flow"]["direction"] == "BULLISH" and e["news"]["direction"] == "NOT_COLLECTED" and e["missing_evidence"][0]["factor"] == "ANALYST" and "evidence_score" in e
    assert j["summary"]["flow_states"] == {"QUALIFIES": 1}
