"""V2: weights/config hash, factor scoring, missing-data handling (never normalised), no BUY/SELL below evidence minimum, V1 gates inherited, V1 untouched, comparison INCONCLUSIVE."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_v2 as V  # noqa: E402
import smart_move_v2_compare as C  # noqa: E402

UTC = timezone.utc
CAP = datetime(2026, 11, 1, 12, tzinfo=UTC)


def mk(w, b, e, a=True):
    return {"weight": w, "available": a, "bull": b, "bear": e}


def v1_sub(bull=True):
    b, e = (1, 0) if bull else (0, 1)
    return {"F3_mean": mk(10, 10 * b, 10 * e), "F3_consistency": mk(5, 5 * b, 5 * e), "F5_trend": mk(4, 4 * b, 4 * e), "F5_relative_strength": mk(3, 3 * b, 3 * e), "F5_extension": mk(3, 3 * b, 3 * e),
            "F7_skew": mk(10, 10 * b, 10 * e), "F8_market": mk(2, 2 * b, 2 * e), "F8_sector": mk(3, 3 * b, 3 * e)}


def test_weights_sum_to_100_and_config_is_hashed_and_deterministic():
    assert sum(V.WEIGHTS.values()) == 100 and len(V.CONFIG_HASH) == 64 and V.STATUS == "EXPERIMENTAL"
    import importlib
    assert importlib.reload(V).CONFIG_HASH == V.CONFIG_HASH


def test_v1_factors_are_taken_from_v1_not_recomputed():
    f = {x["factor"]: x for x in V.from_v1(v1_sub())}
    assert f["F3"]["bull"] == 15 and f["F5"]["bull"] == 10 and f["F7"]["bull"] == 10 and f["F8"]["bull"] == 5 and all(x["available"] for x in f.values())
    gone = V.from_v1({k: dict(v, available=False) for k, v in v1_sub().items()})
    assert not any(x["available"] for x in gone)


def test_missing_data_is_not_normalised_and_current_saved_evidence_can_never_decide():
    facs = V.from_v1(v1_sub()) + [V.implied_vs_hist(None, "2026-11-02"), V.rel_strength({}, {}, None, date(2026, 11, 1), "continuation"), V.analyst(None, "x", CAP, "continuation"),
                                  V.options_flow(None, "continuation"), V.short_inst(None, None, "continuation"), V.news_guidance()]
    r = V.score(facs, v1_gates=[], variant="continuation")
    assert r["bull"] == 40 and r["available_points"] == 40 and r["max"] == 100 and r["signal"] == "NO_TRADE" and "INSUFFICIENT_COVERAGE" in r["reason_codes"]
    assert {m["factor"] for m in r["missing"]} == {"IMPLIED_VS_HIST", "REL_STRENGTH", "ANALYST", "OPTIONS_FLOW", "SHORT_INST", "NEWS_GUIDANCE"} and r["usable_for_decisions"] is False
    assert [m for m in r["missing"] if m["factor"] == "NEWS_GUIDANCE"][0]["data_status"] == "BLOCKED"


def full_factors(variant="continuation", direction=1):
    erows = [{"report_date": "2026-11-02", "expected_move_perc": "0.04"}] + [{"report_date": "2026-0%d-15" % m, "post_earnings_move_1d": "0.05"} for m in range(1, 7)]
    d0 = date(2026, 10, 1)
    trend = {d0 + timedelta(days=i): 100 + i * 2 * direction for i in range(30)}
    flat = {d0 + timedelta(days=i): 100 for i in range(30)}
    act = "upgrade" if direction > 0 else "downgrade"
    return V.from_v1(v1_sub(direction > 0)) + [
        V.implied_vs_hist(erows, "2026-11-02"), V.rel_strength(trend, flat, flat, d0 + timedelta(days=29), variant),
        V.analyst([{"action": act, "timestamp": (CAP - timedelta(days=3)).isoformat()}] * 2, "x", CAP, variant),
        V.options_flow([{"net_call_premium": 3e6 * direction, "net_put_premium": 0}], variant), V.short_inst({"si_float": 0.02}, [{"units_change": 100 * direction}], variant), V.news_guidance()]


def test_with_enough_evidence_the_rule_fires_and_v1_gates_still_block():
    r = V.score(full_factors(), v1_gates=[], variant="continuation")
    assert r["available_points"] >= 60 and r["signal"] == "BUY" and r["reason_codes"] == ["RULE"]
    blocked = V.score(full_factors(), v1_gates=["NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN"], variant="continuation")
    assert blocked["signal"] == "NO_TRADE" and "BLOCKED_BY_V1_GATE" in blocked["reason_codes"]
    s = V.score(full_factors(direction=-1), v1_gates=[], variant="continuation")
    assert s["signal"] == "SELL" and s["bear"] >= 35


def test_contrarian_flips_the_new_factors_and_future_actions_never_leak():
    c = V.score(full_factors("contrarian"), v1_gates=[], variant="contrarian")
    assert c["variant"] == "contrarian" and c["bear"] > 0                       # the same bullish tape reads bearish for the contrarian variant
    future = V.analyst([{"action": "upgrade", "timestamp": (CAP + timedelta(hours=1)).isoformat()}] * 5, "x", CAP, "continuation")
    assert future["bull"] == 0 and future["upgrades"] == 0


def test_implied_vs_hist_is_magnitude_only_and_needs_4_reported_moves():
    erows = [{"report_date": "2026-11-02", "expected_move_perc": "0.04"}] + [{"report_date": "2026-0%d-15" % m, "post_earnings_move_1d": "-0.02"} for m in range(1, 5)]
    f = V.implied_vs_hist(erows, "2026-11-02")
    assert f["available"] and f["ratio"] == 2.0 and f["bull"] == f["bear"] == 0
    assert not V.implied_vs_hist(erows[:3], "2026-11-02")["available"]


def test_comparison_is_inconclusive_without_sample_and_never_declares_a_winner():
    rows = [{"symbol": "A", "earnings_date": "2026-11-02", "variant": "continuation", "move_pct": 3.0, "v1_signal": "BUY", "v2_signal": "NO_TRADE", "kind": "AMC", "sector": "Tech"}]
    out = C.compare(rows)
    assert out["verdict"].startswith("INCONCLUSIVE") and not out["variants"]["continuation"]["sample_sufficient"]
    many = [{"symbol": "S%d" % i, "earnings_date": (date(2026, 11, 2) + timedelta(days=7 * (i % 9))).isoformat(), "variant": v, "move_pct": 2.0, "v1_signal": "BUY", "v2_signal": "BUY"}
            for i in range(120) for v in ("continuation", "contrarian")]
    big = C.compare(many, simulated=True)
    assert big["variants"]["continuation"]["sample_sufficient"] and big["verdict"].startswith("SIMULATED") and "better" not in big["verdict"].lower()
    assert len(big["variants"]["continuation"]["walk_forward"]["v1"]) == 3


def test_v2_never_imports_or_edits_the_v1_engine():
    src = (ROOT / "backend" / "smart_move_v2.py").read_text()
    assert "smvshadow" not in src and "import smart_move_live" not in src
