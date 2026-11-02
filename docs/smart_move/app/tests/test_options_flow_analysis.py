"""Phase 4 offline: Options Flow analysis, UI states and the backend/frontend contract audit. Synthetic rows only (labelled synthetic); the live service is read, never changed."""
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import options_flow_analysis as OF  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
EPOCH = int(NOW.timestamp() * 1000)


def row(right="C", side="ask", premium=50_000.0, volume=500, oi=100, expiry="2026-11-06", kind="PRINT", sweep=False, epoch=EPOCH - 60_000, **kw):
    return dict({"symbol": "SYNTH", "right": right, "side": side, "premium": premium, "volume": volume, "open_interest": oi, "expiry": expiry, "strike": 100, "kind": kind, "sweep": sweep, "epoch": epoch}, **kw)


def test_direction_comes_from_the_side_crossed_and_an_unknown_side_is_never_guessed():
    cases = {("C", "ask"): "BULLISH", ("C", "bid"): "BEARISH", ("P", "ask"): "BEARISH", ("P", "bid"): "BULLISH", ("C", "mid"): "NEUTRAL", ("P", "mid"): "NEUTRAL",
             ("C", None): "UNKNOWN_SIDE", ("P", ""): "UNKNOWN_SIDE", ("C", "weird"): "UNKNOWN_SIDE", ("X", "ask"): "UNKNOWN_SIDE", (None, "ask"): "UNKNOWN_SIDE"}
    for (r, s), exp in cases.items():
        assert OF.classify({"right": r, "side": s}) == exp


def test_summary_covers_calls_puts_sweeps_blocks_unusual_expiry_and_shares():
    rows = [row("C", "ask", 100_000, 900, 100, kind="SWEEP", sweep=True), row("P", "bid", 60_000, 300, 100), row("P", "ask", 400_000, 50, 500, kind="BLOCK", expiry="2026-11-02"),
            row("C", "mid", 10_000, 10, 1000, expiry="2027-03-19"), row("C", None, 5_000, 1, 1, expiry="2026-12-18")]
    s = OF.summarize(rows, NOW)
    assert s["rows"] == 5 and s["calls"] == {"count": 3, "volume": 911, "premium": 115_000.0} and s["puts"] == {"count": 2, "volume": 350, "premium": 460_000.0}
    assert s["sweeps"] == 1 and s["blocks"] == 1 and s["classification"] == {"BULLISH": 2, "BEARISH": 1, "NEUTRAL": 1, "UNKNOWN_SIDE": 1}
    assert s["bullish_premium"] == 160_000.0 and s["bearish_premium"] == 400_000.0 and s["bullish_premium_share"] == 0.286 and s["flow_lean"] == "BEARISH"
    assert s["unusual"] == 2 and s["vol_oi_available"] == 5                                                          # vol/OI > 1 and premium >= 25k: the first two rows
    assert s["expiry_buckets"] == {"1-7D": 2, "0DTE": 1, ">90D": 1, "31-90D": 1} and s["put_call_premium_ratio"] == 4.0


def test_freshness_and_missing_data_indicators():
    assert OF.summarize([row(epoch=EPOCH - 30_000)], NOW)["freshness"]["state"] == "LIVE"
    assert OF.summarize([row(epoch=EPOCH - 10 * 60_000)], NOW)["freshness"]["state"] == "RECENT"
    assert OF.summarize([row(epoch=EPOCH - 5 * 3600_000)], NOW)["freshness"]["state"] == "STALE"
    s = OF.summarize([row(epoch=None, side=None, premium=None), row()], NOW)
    assert s["freshness"]["state"] == "LIVE" and s["missing_data"]["by_field"]["side"] == 1 and s["missing_data"]["by_field"]["premium"] == 1 and s["missing_data"]["rows_missing_any_required"] == 1
    assert s["missing_data"]["unclassifiable_share"] == 0.5
    empty = OF.summarize([], NOW)
    assert empty["rows"] == 0 and empty["flow_lean"] is None and empty["freshness"]["state"] == "NO_TIMESTAMPS" and empty["put_call_premium_ratio"] is None
    assert OF.summarize([None, "x", {}], NOW)["rows"] == 1                                                           # junk rows are ignored, an empty dict counts as a row with everything missing


def test_ui_states_never_leave_a_panel_blank():
    f = lambda env: OF.ui_state(env, NOW)["state"]  # noqa: E731
    t = [{"x": 1}]
    assert f(None) == "ERROR" and f({"status": "INVALID_SYMBOL", "trades": []}) == "INVALID_SYMBOL" and f({"status": "NO_TRADES", "trades": []}) == "EMPTY" and f({"status": "NO_DATA"}) == "EMPTY"
    assert f({"status": "RATE_LIMITED", "trades": []}) == "ERROR" and f({"status": "WEIRD", "trades": []}) == "ERROR"
    assert f({"status": "OK", "trades": t, "delay_minutes": 15, "note": "OPRA delay"}) == "DELAYED"
    assert f({"status": "OK", "trades": t, "delay_minutes": 0, "note": "The market is closed; this is the last session's tape."}) == "LAST_SESSION"
    assert f({"status": "OK", "trades": t, "delay_minutes": 0, "note": "Live option flow."}) == "LIVE"
    assert all(OF.ui_state(e, NOW)["headline"] for e in (None, {"status": "NO_TRADES"}, {"status": "OK", "trades": t}))


def test_contract_audit_finds_the_known_frontend_backend_drift_in_the_real_files():
    r = OF.audit_file_pair(ROOT)
    assert {"symbol", "option_symbol", "right", "strike", "expiry", "volume", "premium", "side", "sweep", "kind", "sentiment", "type", "epoch"} <= set(r["backend_keys"])
    assert r["consistent"] is False and set(r["value_domain_drift"]) == {"type", "kind", "sentiment"}
    assert {"exchanges", "prints"} <= set(r["frontend_only_fields"])
    assert r["value_domain_drift"]["kind"]["backend_sends"] == ["BLOCK", "PRINT", "SWEEP"] and "SPLIT" in r["value_domain_drift"]["kind"]["frontend_declares"]


def test_the_documented_sentiment_defect_matches_the_live_function_and_the_live_service_is_unchanged():
    try:
        import uw_flow_service as live
    except Exception as e:  # noqa: BLE001
        pytest.skip("live service not importable offline: %s" % e)
    assert live._sentiment({"side": None, "right": "C"}) == "BEARISH" and live._sentiment({"side": None, "right": "P"}) == "BULLISH"       # the defect, reproduced
    assert OF.classify({"side": None, "right": "C"}) == "UNKNOWN_SIDE"
    for side in ("ask", "bid", "mid"):                                                                                                        # where a side exists the two agree
        for right in ("C", "P"):
            assert OF.classify({"side": side, "right": right}) == live._sentiment({"side": side, "right": right})
    assert any(f["id"] == "SENTIMENT_MISSING_SIDE" and f["status"].startswith("NOT FIXED") for f in OF.AUDIT_FINDINGS)
    src = (ROOT / "backend" / "options_flow_analysis.py").read_text(encoding="utf-8")
    assert not [ln for ln in src.splitlines() if ln.startswith(("import ", "from ")) and any(w in ln for w in ("uw_flow_service", "requests", "httpx", "urllib", "socket"))]
