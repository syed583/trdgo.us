"""flowx-1 (transparent options-flow analysis, same rule as V2) and news-x1 (transparent headline classifier, research only, zero points)."""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_flowx as FX  # noqa: E402
import smart_move_news as N  # noqa: E402
import smart_move_v2 as V2  # noqa: E402

UTC = timezone.utc
CAP = datetime(2026, 11, 1, 12, tzinfo=UTC)
ED = "2026-11-03"


def alert(typ="call", ask=400_000, bid=0, age_h=24, sweep=False, multi=False, expiry="2026-11-20", voi=2.0, prem=None):
    return {"type": typ, "total_ask_side_prem": str(ask), "total_bid_side_prem": str(bid), "total_premium": str(prem if prem is not None else ask + bid), "has_sweep": sweep, "has_multileg": multi, "expiry": expiry,
            "volume_oi_ratio": str(voi), "created_at": (CAP - timedelta(hours=age_h)).isoformat().replace("+00:00", "Z")}


# ------------------------------------------------------------------ flowx
def test_flowx_uses_exactly_the_v2_rule_and_agrees_with_it():
    assert FX.RULE == {"min_alerts": 3, "min_attributed_premium_usd": 500_000.0, "strong_net_ratio": 0.5, "moderate_net_ratio": 0.25, "sweep_weight": 1.5, "min_volume_oi_ratio": 1.0}
    cfg = V2.CONFIG["options_flow"]
    assert (cfg["min_alerts"], cfg["min_attributed_premium_usd"], cfg["strong_net_ratio"], cfg["moderate_net_ratio"], cfg["sweep_weight"], cfg["min_volume_oi_ratio"]) == tuple(FX.RULE.values())
    cases = [[alert("call", 300_000)] * 3, [alert("put", 300_000)] * 3, [alert("call", 300_000)] * 3 + [alert("put", 200_000)] * 2, [alert("call", 300_000)] * 2, [alert("call", 1000)] * 5,
             [alert("call", 0, 300_000)] * 3, [alert("call", 300_000)] * 3 + [alert("call", 9e6, multi=True)], [alert("call", 300_000, age_h=24 * 8)] * 3]
    for c in cases:
        for L in (5, 10):
            f = FX.analyse(c, ED, CAP, L)
            v = V2.options_flow(c, ED, CAP, "continuation") if L == 5 else None
            if v is not None:
                assert f["qualifies"] == v["available"] and (not f["qualifies"] or (f["direction"] == "BULLISH") == (v["bull"] > 0 or f["direction"] == "BULLISH" and v["bull"] == 0 and v["bear"] == 0))


def test_flowx_waterfall_explains_every_dropped_alert_and_never_uses_future_stamps():
    alerts = ([alert(age_h=24)] * 3 + [alert(age_h=24 * 9)] * 2 + [alert(multi=True)] + [alert(expiry="2026-11-02")] * 2 + [alert(voi=0.4)] * 4 + [alert(ask=0, bid=0, prem=50_000)] + [alert(age_h=-5)])
    f = FX.analyse(alerts, ED, CAP, 5)
    w = f["waterfall"]
    assert w["returned"] == 14 and w["used"] == 3 and w["outside_lookback"] == 2 and w["multi_leg"] == 1 and w["expiry_before_earnings"] == 2 and w["volume_below_open_interest"] == 4
    assert w["side_not_attributable"] == 1 and w["future_stamped_never_used"] == 1 and f["unattributed_share"] is not None
    assert w["returned"] == w["used"] + w["outside_lookback"] + w["multi_leg"] + w["expiry_before_earnings"] + w["volume_below_open_interest"] + w["side_not_attributable"] + w["future_stamped_never_used"]


def test_flowx_reports_the_lean_when_below_the_minimum_but_awards_nothing_and_names_the_shortfall():
    f = FX.analyse([alert("call", 100_000)] * 2, ED, CAP, 5)
    assert f["state"] == "LEAN_ONLY_BELOW_MINIMUM" and f["direction"] == "BULLISH" and f["qualifies"] is False and f["lean_only"] is True
    assert f["shortfall"] == {"alerts_needed": 1, "premium_needed_usd": 300000}
    bal = FX.analyse([alert("call", 300_000)] * 3 + [alert("put", 280_000)] * 3, ED, CAP, 5)
    assert bal["state"] == "QUALIFIED_BUT_BALANCED" and bal["qualifies"] is False and bal["direction"] == "BALANCED"
    assert FX.analyse([], ED, CAP)["state"] == "NO_ALERTS" and FX.analyse(None, ED, CAP)["state"] == "NOT_COLLECTED" and FX.analyse([alert(age_h=24 * 20)], ED, CAP, 5)["state"] == "NONE_IN_LOOKBACK"


def test_lookback_comparison_uses_the_same_alerts_and_wider_windows_only_add_older_ones():
    alerts = [alert("call", 300_000, age_h=24)] * 2 + [alert("call", 300_000, age_h=24 * 8)] * 2 + [alert("put", 900_000, age_h=24 * 20)]
    cmp_ = FX.compare_lookbacks(alerts, ED, CAP)
    assert [cmp_[k]["qualifies"] for k in ("5", "10", "15", "30")] == [False, True, True, False]
    assert [cmp_[k]["alerts_used"] for k in ("5", "10", "15", "30")] == [2, 4, 4, 5]
    assert cmp_["10"]["direction"] == "BULLISH" and cmp_["30"]["state"] == "QUALIFIED_BUT_BALANCED" and cmp_["30"]["net_ratio"] == 0.143 and cmp_["30"]["direction"] == "BALANCED"     # older put flow turns the 30-day view balanced: the windows are NOT interchangeable


# ------------------------------------------------------------------ news
def hl(text, source="Tradex", age_h=30, sentiment="neutral"):
    return {"headline": text, "source": source, "created_at": (CAP - timedelta(hours=age_h)).isoformat().replace("+00:00", "Z"), "sentiment": sentiment}


def c(text, **kw):
    return N.classify(hl(text, **{k: v for k, v in kw.items() if k in ("source", "age_h", "sentiment")}), "AAPL", "APPLE", kw.get("cap", CAP), kw.get("release"))


def test_provider_sentiment_is_ignored_and_guidance_events_drive_the_label():
    assert c("Apple raises full-year guidance $AAPL", sentiment="neutral")["label"] == "POSITIVE" and c("Apple cuts outlook for fiscal 2027 $AAPL", sentiment="positive")["label"] == "NEGATIVE"
    assert c("Apple iPhone Duo draws early complaints of the screen cracking. $AAPL")["label"] == "NEGATIVE" and c("Apple opens a new store $AAPL")["label"] == "NEUTRAL"


def test_untrusted_or_mismatched_headlines_are_unverified_and_ticker_collisions_are_caught():
    assert c("AAPL Price Prediction: MACD flat", source="Blockchain News")["label"] == "UNVERIFIED"
    assert c("New Book from AAPL Equips Physician Leaders", source="PR Newswire")["category"] == "ENTITY_MISMATCH"
    assert N.classify(hl("Vortex Energy Completes Acquisition", source="GlobeNewswire"), "VRTX", "VERTEX PHARMACEUTICALS", CAP)["label"] == "UNVERIFIED"
    assert c("Some wire", source="Mystery Feed")["category"] == "UNKNOWN_SOURCE"


def test_no_double_counting_and_no_post_announcement_information():
    assert c("Loop Capital downgrades Apple to Hold, target $330 $AAPL")["category"] == "ANALYST_ACTION" and c("Loop Capital downgrades Apple to Hold, target $330 $AAPL")["label"] == "NEUTRAL"
    assert c("Apple shares touch record high $AAPL")["category"] == "PRICE_ACTION"
    assert c("Apple posted Q2 revenue of $3.3 BLN, above the $3.2 BLN estimate $AAPL")["category"] == "EARNINGS_RESULT"
    rel = CAP + timedelta(hours=10)
    late = N.classify(hl("Apple raises guidance $AAPL", age_h=-20), "AAPL", "APPLE", CAP + timedelta(days=2), release_ts=rel)
    assert late["category"] == "NOT_PRE_ANNOUNCEMENT" and late["label"] == "UNVERIFIED"                      # stamped after the release: never used
    assert N.classify(hl("Apple raises guidance $AAPL", age_h=24 * 9), "AAPL", "APPLE", CAP)["category"] == "OUT_OF_WINDOW"


def test_stock_direction_needs_a_net_of_two_verified_headlines_and_awards_zero_points():
    heads = [hl("Apple raises guidance $AAPL"), hl("Apple wins contract with the Air Force $AAPL"), hl("Apple opens a new store $AAPL")]
    d = N.stock_direction(heads, "AAPL", "APPLE", CAP)
    assert d["direction"] == "POSITIVE" and d["scoring_points"] == 0 and "NOT VALIDATED" in d["validation"]
    assert N.stock_direction(heads[:1], "AAPL", "APPLE", CAP)["direction"] == "NEUTRAL"
    assert N.stock_direction([hl("AAPL price prediction", source="Blockchain News")], "AAPL", "APPLE", CAP)["direction"] == "UNVERIFIED"
    assert N.stock_direction(None, "AAPL", "APPLE", CAP)["direction"] == "NOT_COLLECTED"


def test_the_classifier_reproduces_the_author_labels_on_the_56_saved_headlines_but_that_is_not_validation():
    f = ROOT / "docs" / "smart_move" / "news_labels_author.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert "NOT independent" in data["labelled_by"] and len(data["rows"]) == 56
    cap = datetime.fromisoformat(data["capture_ts"])
    conf, wrong = Counter(), []
    for r in data["rows"]:
        out = N.classify({"headline": r["headline"], "source": r["source"], "created_at": r["created_at"]}, r["symbol"], r["company"], cap, None, lookback_days=3650)
        conf[(r["label"], out["label"])] += 1
        if out["label"] != r["label"]:
            wrong.append((r["id"], r["label"], out["label"], out["category"]))
    assert not wrong, wrong
    assert Counter(r["provider_sentiment"] for r in data["rows"]) == {"neutral": 56}                         # the provider label carries no information
    assert Counter(r["label"] for r in data["rows"]) == {"UNVERIFIED": 34, "NEUTRAL": 20, "NEGATIVE": 1, "POSITIVE": 1}
