"""The trade-permission rules (intraday_execution.py), one rule at a time, plus the guards that keep the four strategies untouched."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import intraday_execution as ex
import intraday_safety
import market_calendar as mc
import tab_settings as cfg
from wf_harness import STRATEGIES, TUE, WEIGHTS, bar, et, falling_bars, make_params, rising_bars, row

BOUNDS = mc.session_bounds(et(*TUE).date())
PREV_CLOSE = et(2026, 10, 26, 16, 0)


# ---------------------------------------------------------------------------- the strategies are untouched -----------
def test_the_original_cutoffs_and_every_weight_table_are_unchanged():
    assert cfg.V3_CUTOFFS == {"buy": 55.0, "sell": 45.0}
    for name, w in WEIGHTS.items():
        assert sum(w.values()) == 100, name
    assert WEIGHTS["grok"]["options_flow"] == 34 and WEIGHTS["kimi"]["options_flow"] == 28 and WEIGHTS["meta"]["options_flow"] == 25
    assert WEIGHTS["gemini"]["options_flow"] == 13
    assert set(WEIGHTS) == set(STRATEGIES) == {"grok", "meta", "kimi", "gemini"}       # chatgpt / claude_pro are retired, not configured


def test_the_registry_names_exactly_the_parameters_each_strategy_declares():
    """If a strategy gains, loses or renames a parameter this fails: the classification table cannot drift silently."""
    assert set(ex.REGISTRY) == set(ex.STRATEGIES) == set(STRATEGIES)                  # no registry entry for a retired board
    for s in STRATEGIES:
        assert set(ex.REGISTRY[s]["equity"]) == set(WEIGHTS[s]), s
        for path, reg in ex.REGISTRY[s].items():                                       # every remaining parameter has a real data source
            assert not [k for k, v in reg.items() if v in (ex.UNIMPLEMENTED, ex.NOT_APPLICABLE)], (s, path)


def _scoring_text(src: str) -> str:
    """The scoring code of a strategy module: ``score_one`` (and ``score_from_model`` where it exists) -- the parts that
    decide a BUY / SELL. The level / quote helpers (``_price_atr`` / ``_levels``) are shared plumbing and may change."""
    import re
    blocks = re.findall(r"^def (?:score_one|score_from_model)\(.*?(?=^(?:def |@|[A-Z_]+ *=))", src, flags=re.S | re.M)
    assert blocks, "no scoring function found"
    return "\n".join(blocks)


def test_the_four_strategy_scorers_are_byte_identical_to_the_release_they_started_from():
    """The SCORING of grok / meta / kimi / gemini (and the shared claude_last quantiser) is unchanged since the release
    277f30f. Only the shared entry-level helper (bid / ask / spread on the boards) differs, and that never touches a score."""
    root = Path(__file__).resolve().parents[2]
    files = ["intraday_grok_service", "meta_intraday_service", "kimi_intraday_service", "gemini_intraday_service"]
    for f in files + ["claude_last_service"]:
        try:
            out = subprocess.run(["git", "-C", str(root), "show", f"277f30f:backend/{f}.py"], capture_output=True, text=True,
                                 timeout=30, encoding="utf-8", errors="ignore")
        except Exception:
            pytest.skip("git unavailable")
        if out.returncode != 0:
            pytest.skip("the release commit is not in this checkout")
        now = (root / "backend" / f"{f}.py").read_text(encoding="utf-8")
        if f == "claude_last_service":
            assert now.replace("\r\n", "\n") == out.stdout.replace("\r\n", "\n"), f
        else:
            assert _scoring_text(now) == _scoring_text(out.stdout), f


# ------------------------------------------------------------------------------------------ classification -----------
def classify(strategy, session, now, **kw):
    params = make_params(strategy, session, kw.pop("bias", 1.0), kw.pop("overrides", None))
    meta = kw.pop("meta", None)
    from wf_harness import fresh_meta
    meta = fresh_meta(strategy, session, now) if meta is None else meta
    return ex.classify_parameters(strategy, session, params, meta, now=now, bounds=mc.session_bounds(now.date()),
                                  prev_close=PREV_CLOSE, **kw)


def cats(classified):
    return {r["name"]: r["category"] for r in classified}


# test_unimplemented_parameters_are_not_treated_as_provider_failures was deleted: it pinned the ChatGPT board's seven sourceless
# (UNIMPLEMENTED) parameters and its engine-only order_flow; that board is retired and no remaining strategy declares such a parameter
# (test_the_registry_names_exactly_the_parameters_each_strategy_declares now asserts that).


def test_options_tape_parameters_are_unavailable_before_the_open_and_excluded_from_eligibility():
    cl = classify("grok", "PREMARKET", et(*TUE, 7, 15))
    c = cats(cl)
    assert c["options_flow"] == c["unusual_activity"] == c["volume_pcr"] == c["oi_change_expiry"] == ex.UNAVAILABLE_PREMARKET
    assert c["trend_price"] == c["key_levels"] == c["event_radar"] == c["rsi_momentum"] == c["disparity"] == ex.AVAILABLE
    assert c["volatility_em"] == ex.ZERO_WEIGHT
    cov = ex.coverage(cl)
    assert cov["eligible_weight"] == 24 + 8 + 5 + 3 + 2 and cov["eligible_pct"] == 100.0
    assert cov["total_pct"] == pytest.approx(42.0)                                     # of ALL weight, only 42% can exist pre-market


def test_neutral_missing_and_stale_readings_are_told_apart():
    now = et(*TUE, 7, 15)
    from wf_harness import fresh_meta
    meta = fresh_meta("grok", "PREMARKET", now)
    meta["ema_trend"] = dict(meta["ema_trend"], age_s=500000.0)
    meta["price_action"] = dict(meta["price_action"], age_s=500000.0)
    cl = classify("grok", "PREMARKET", now, overrides={"rsi_momentum": 0.0, "key_levels": None, "event_radar": 1.0}, meta=meta)
    c = cats(cl)
    assert c["rsi_momentum"] == ex.NEUTRAL                                             # a reading of "neutral" is data
    assert c["key_levels"] == ex.MISSING
    assert c["trend_price"] == ex.STALE


def test_a_signal_built_before_the_last_close_is_stale_for_the_premarket():
    now = et(*TUE, 7, 15)
    from wf_harness import fresh_meta
    meta = fresh_meta("grok", "PREMARKET", now, age=60.0 * 60 * 16)               # 16 h old: built yesterday at 15:15, before the close
    c = cats(classify("grok", "PREMARKET", now, meta=meta))
    assert c["trend_price"] == ex.STALE


def test_agreement_counts_only_valid_covered_weight():
    now = et(*TUE, 7, 15)
    cl = classify("grok", "PREMARKET", now, overrides={"trend_price": 1.0, "key_levels": -1.0, "event_radar": -1.0, "rsi_momentum": 1.0, "disparity": 1.0})
    assert ex.agreement(cl, "BUY") == pytest.approx((24 + 3 + 2) / 42, abs=0.001)
    assert ex.agreement(cl, "SELL") == pytest.approx((8 + 5) / 42, abs=0.001)


def test_evaluate_data_passes_a_clean_premarket_row_and_names_every_failure():
    now = et(*TUE, 7, 15)
    r = row("grok", "X", now, signal="BUY", session="PREMARKET")
    ok = ex.evaluate_data("grok", "BUY", "PREMARKET", r, now=now, bounds=BOUNDS, prev_close=PREV_CLOSE)
    assert ok["ok"] and ok["agreement"] == 1.0 and ok["coverage"]["eligible_pct"] == 100.0
    bad = ex.evaluate_data("grok", "BUY", "PREMARKET", row("grok", "X", now, session="PREMARKET", bias=1.0,
                           overrides={"trend_price": None, "key_levels": None}),          # 32 of the 42 eligible points missing
                           now=now, bounds=BOUNDS, prev_close=PREV_CLOSE)
    assert not bad["ok"] and bad["code"] == ex.WAIT_INSUFFICIENT
    against = ex.evaluate_data("grok", "BUY", "PREMARKET", row("grok", "X", now, session="PREMARKET", bias=-1.0), now=now,
                               bounds=BOUNDS, prev_close=PREV_CLOSE)
    assert not against["ok"] and "agrees" in against["reasons"][0]


# ------------------------------------------------------------------------------------------------ quote, liquidity ----
def test_quote_rules_are_stricter_before_the_open():
    q = {"price": 100.0, "bid": 99.93, "ask": 100.07, "age_s": 60.0}               # spread 0.14%
    assert not ex.validate_quote(q, session="PREMARKET", side="BUY", reference_price=100.0)["ok"]
    assert ex.validate_quote(q, session="REGULAR", side="BUY", reference_price=100.0)["ok"]
    assert not ex.validate_quote(dict(q, bid=99.97, ask=100.03, age_s=200.0), session="PREMARKET", side="BUY", reference_price=100.0)["ok"]
    assert ex.validate_quote(dict(q, bid=99.97, ask=100.03, age_s=200.0), session="REGULAR", side="BUY", reference_price=100.0)["ok"]


@pytest.mark.parametrize("q, why", [
    (None, "no quote"), ({"price": 0, "bid": 0, "ask": 0, "age_s": 1}, "no valid last"),
    ({"price": 100, "bid": 100.1, "ask": 99.9, "age_s": 1}, "crossed"), ({"price": 100, "bid": None, "ask": None, "age_s": 1}, "two-sided"),
    ({"price": 100, "bid": 99.97, "ask": 100.03, "age_s": None}, "no age"), ({"price": 105, "bid": 99.97, "ask": 100.03, "age_s": 1}, "differs"),
])
def test_an_unusable_quote_is_refused_with_a_reason(q, why):
    v = ex.validate_quote(q, session="PREMARKET", side="BUY", reference_price=100.0)
    assert not v["ok"] and v["code"] == ex.WAIT_PRICE and any(why in r for r in v["reasons"]), v


def test_the_paper_fill_is_the_ask_for_a_buy_and_the_bid_for_a_sell():
    q = {"price": 100.0, "bid": 99.98, "ask": 100.02, "age_s": 5.0}
    assert ex.validate_quote(q, session="REGULAR", side="BUY", reference_price=100.0)["fill_price"] == 100.02
    assert ex.validate_quote(q, session="REGULAR", side="SELL", reference_price=100.0)["fill_price"] == 99.98
    one_sided = ex.validate_quote({"price": 100.0, "bid": None, "ask": None, "age_s": 5.0}, session="REGULAR", side="BUY", reference_price=100.0)
    assert one_sided["ok"] and "last trade" in one_sided["assumption"]


def test_liquidity_uses_the_live_engines_own_floors():
    assert ex.validate_liquidity(150.0, 2_000_000.0)["ok"]
    assert ex.validate_liquidity(9.0, 2_000_000.0)["code"] == ex.WAIT_LIQUIDITY
    assert ex.validate_liquidity(150.0, 500_000.0)["code"] == ex.WAIT_LIQUIDITY
    assert ex.validate_liquidity(150.0, None)["code"] == ex.WAIT_LIQUIDITY
    candles = [{"date": f"2026-10-{d:02d}", "volume": 2_000_000.0} for d in range(1, 24)] + [{"date": "2026-10-27", "volume": 10.0}]
    assert ex.average_volume(candles, "2026-10-27") == 2_000_000.0                     # today's partial bar is excluded


# ------------------------------------------------------------------------------------------------------ persistence ---
def test_persistence_needs_distinct_evidence_far_enough_apart():
    obs = [{"ts": 1000.0, "evidence": "a", "direction": "BUY"}]
    assert not ex.persistence(obs, "BUY")["ok"]
    assert not ex.persistence(obs + [{"ts": 1030.0, "evidence": "a", "direction": "BUY"}], "BUY")["ok"]      # same evidence twice
    assert not ex.persistence(obs + [{"ts": 1030.0, "evidence": "b", "direction": "BUY"}], "BUY")["ok"]      # too close together
    assert ex.persistence(obs + [{"ts": 1200.0, "evidence": "b", "direction": "BUY"}], "BUY")["ok"]
    flipped = obs + [{"ts": 1200.0, "evidence": "b", "direction": "BUY"}, {"ts": 1300.0, "evidence": "c", "direction": "SELL"}]
    assert not ex.persistence(flipped, "BUY")["ok"]                                    # the newest evidence points the other way


# ------------------------------------------------------------------------------------------------ candles ------------
def test_only_closed_candles_count_and_the_forming_one_never_confirms():
    now = et(*TUE, 11, 2, 30)                                                          # the 11:00 bar is still forming
    bars = rising_bars(now, 24, warmup=True)
    bars.append(bar(et(*TUE, 11, 0), 101.0, 102.0, 101.0, 101.9))
    cb = ex.closed_bars(bars, 300, now)
    assert all(b["dt"] + timedelta(seconds=300) <= now for b in cb) and max(b["dt"] for b in cb) < et(*TUE, 11, 0)


def test_candle_confirmation_rules_by_session_and_timeframe():
    now = et(*TUE, 11, 0, 5)
    up, down = ex.candle_reading(rising_bars(now, 24, warmup=True), 300, now, now.date()), ex.candle_reading(falling_bars(now, 24, warmup=True), 300, now, now.date())
    assert up["status"] == "OK" and up["bias"] == 1 and down["bias"] == -1
    fresh15 = {"status": "OK", "bias": 1, "last_bar_end_epoch": now.timestamp() - 60, "detail": ""}
    assert ex.confirm_candles("BUY", "REGULAR", up, fresh15, now)["ok"]
    assert not ex.confirm_candles("SELL", "REGULAR", up, fresh15, now)["ok"]
    assert not ex.confirm_candles("BUY", "REGULAR", up, dict(fresh15, last_bar_end_epoch=now.timestamp() - 3600), now)["ok"]    # stale 15m
    assert not ex.confirm_candles("BUY", "REGULAR", up, None, now)["ok"]                                                         # no 15m reading after warm-up
    assert ex.confirm_candles("BUY", "REGULAR", up, {"status": "WARMUP", "detail": "first candles"}, now)["ok"]                  # 15m not supported yet: 5m decides
    assert not ex.confirm_candles("BUY", "REGULAR", None, fresh15, now)["ok"]                                                    # 5m always needed
    pre = ex.confirm_candles("BUY", "PREMARKET", None, None, now)
    assert pre["ok"] and pre["applies"] is False                                                                                 # nothing regular-session is required


# -------------------------------------------------------------------------------------------------------- risk --------
def pos(strategy="grok", symbol="AAA", side="LONG", shares=100, price=100.0):
    return {"strategy": strategy, "symbol": symbol, "side": side, "shares": shares, "price": price}


def test_risk_limits_each_have_their_own_reason(monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_EXEC, "max_open_total", 3)
    full = [pos(symbol=f"S{i}") for i in range(3)]
    r = ex.check_risk(strategy="meta", symbol="NEW", direction="BUY", price=50.0, open_positions=full, day_pnl={})
    assert not r["ok"] and r["code"] == ex.WAIT_LIMIT and "3 open positions" in r["reasons"][0]
    monkeypatch.setitem(cfg.INTRADAY_EXEC, "max_open_total", 60)
    r = ex.check_risk(strategy="meta", symbol="NEW", direction="BUY", price=50.0, open_positions=[], day_pnl={"meta": -6000.0})
    assert r["code"] == ex.WAIT_LOSS
    r = ex.check_risk(strategy="meta", symbol="NEW", direction="BUY", price=50.0, open_positions=[], day_pnl={"meta": 0.0, "total": -16000.0})
    assert r["code"] == ex.WAIT_LOSS
    r = ex.check_risk(strategy="grok", symbol="AAA", direction="BUY", price=100.0, open_positions=[pos()], day_pnl={})
    assert r["code"] == ex.WAIT_DUPLICATE
    r = ex.check_risk(strategy="meta", symbol="AAA", direction="BUY", price=100.0, open_positions=[pos("grok"), pos("kimi"), pos("gemini")], day_pnl={})
    assert r["code"] == ex.WAIT_LIMIT and "3 strategies already hold AAA LONG" in r["reasons"][0]
    r = ex.check_risk(strategy="meta", symbol="AAA", direction="SELL", price=100.0, open_positions=[pos("grok"), pos("kimi"), pos("gemini")], day_pnl={})
    assert r["ok"]                                                                     # the opposite side is a different exposure


def test_the_position_size_is_reduced_to_fit_never_raised(monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_EXEC, "max_exposure_per_symbol_usd", 10_500.0)
    r = ex.check_risk(strategy="meta", symbol="AAA", direction="BUY", price=100.0, open_positions=[], day_pnl={})
    assert r["ok"] and r["quantity"] == 100                                           # the existing convention
    r = ex.check_risk(strategy="meta", symbol="AAA", direction="BUY", price=100.0, open_positions=[pos("grok", shares=60)], day_pnl={})
    assert r["ok"] and r["quantity"] == 45                                            # 10 500 - 6 000 = 4 500 of room
    r = ex.check_risk(strategy="meta", symbol="AAA", direction="BUY", price=100.0, open_positions=[pos("grok", shares=105)], day_pnl={})
    assert not r["ok"] and r["code"] == ex.WAIT_LIMIT


# ---------------------------------------------------------------------------------------- the board is annotated ------
def test_workflow_v2_leaves_the_boards_signal_score_and_levels_exactly_as_the_strategy_made_them(monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "v2")
    r = {"symbol": "X", "signal": "BUY", "score": 61.5, "entry": 100.0, "now": 100.0, "sl": 99.0, "tp": 102.0, "params": [], "coverage": 40.0}
    out = intraday_safety.gate(r, phase="pre", signals_age_s=90000.0, price_age=99999.0, inputs_source="x", meta={"meta": {"rsi": {"age_s": 5}}})
    assert out["signal"] == "BUY" and out["score"] == 61.5 and out["sl"] == 99.0 and out["tp"] == 102.0 and out["entry"] == 100.0
    assert out["signal_meta"] == {"rsi": {"age_s": 5}} and out["gate_ts"] and not out.get("wait")
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "legacy")
    legacy = intraday_safety.gate(dict(r), phase="pre", signals_age_s=90000.0, price_age=99999.0, inputs_source="x")
    assert legacy["signal"] == "WAIT" and legacy["score"] is None                    # the release behaviour is untouched in legacy mode
