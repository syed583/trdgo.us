"""Grok / ChatGPT analyst-data integration (Finnhub recommendation consensus).

These pin the Phase-1 data-completeness change:
  * analyst recommendation counts are read correctly and are directional
    (bearish lowers the score, bullish raises it);
  * every kind of bad/absent Finnhub response degrades to no-data, never a
    fabricated value;
  * a failed refresh never overwrites a good cached reading with None;
  * the Finnhub rate gate is shared by every caller (not just the warmer);
  * the two models that should NOT use recommendation counts (Kimi price_target,
    Claude Pro estimate_dispersion) stay off.
"""
from __future__ import annotations

import time

import grok_service as gk
import chatgpt_service as cg
import finnhub_service as fh


# --- test doubles ----------------------------------------------------------- #
def _rec(sb=0, b=0, h=0, s=0, ss=0):
    return {"strongBuy": sb, "buy": b, "hold": h, "sell": s, "strongSell": ss,
            "period": "2026-10-01"}


def _mock_rec(monkeypatch, value):
    monkeypatch.setattr(gk, "_uw_analyst_bias", lambda symbol, days=90: None)   # Finnhub fallback path
    monkeypatch.setattr(fh, "configured", lambda: True)
    monkeypatch.setattr(fh, "recommendation", lambda symbol: value)


def _eq_stub():
    # A minimal equity analysis: everything neutral so the only mover is analyst.
    sig = {k: 0.0 for k in ("options_flow", "unusual_activity", "disparity",
                            "insider_activity", "price_action", "implied_volatility",
                            "oi_positioning", "earnings_results")}
    params = [{"name": n, "bias": 0.0} for n in
              ("eps_estimates", "guidance", "peer_readthrough", "price_action",
               "revenue_estimates", "options_positioning", "unusual_options",
               "disparity", "dark_pool")]
    return {"params": params, "signals": sig, "short_interest": None,
            "score": 50.0, "decision": "NEUTRAL"}


def _trade_stub():
    return {"historical_moves": [], "spark": None, "expected_move": {"percent": None}}


# --- 2 & 3: interpretation + directionality --------------------------------- #
def test_bullish_recommendation_is_positive(monkeypatch):
    _mock_rec(monkeypatch, _rec(sb=20, b=10, h=2))
    assert gk._analyst_bias_calc("AAA") > 0.5


def test_bearish_recommendation_is_negative(monkeypatch):
    _mock_rec(monkeypatch, _rec(h=2, s=10, ss=20))
    assert gk._analyst_bias_calc("AAA") < -0.5


def test_neutral_recommendation_is_zero(monkeypatch):
    _mock_rec(monkeypatch, _rec(h=10))
    assert gk._analyst_bias_calc("AAA") == 0.0


def test_missing_recommendation_is_none(monkeypatch):
    _mock_rec(monkeypatch, None)                 # provider returned nothing
    assert gk._analyst_bias_calc("AAA") is None


def test_malformed_recommendation_is_none(monkeypatch):
    _mock_rec(monkeypatch, {"garbage": "x"})      # no counts -> total 0
    assert gk._analyst_bias_calc("AAA") is None


def test_rate_limited_recommendation_is_none(monkeypatch):
    # _get returns None on a 429/timeout; recommendation then yields None.
    monkeypatch.setattr(fh, "configured", lambda: True)
    monkeypatch.setattr(fh, "_get", lambda path, ttl=600.0: None)
    assert fh.recommendation("AAA") is None
    assert gk._analyst_bias_calc("AAA") is None


def test_not_configured_is_none(monkeypatch):
    monkeypatch.setattr(fh, "configured", lambda: False)
    assert gk._analyst_bias_calc("AAA") is None


# --- 3: bearish lowers the model score, bullish raises it ------------------- #






# --- 5: a failed refresh must not overwrite good cached data ---------------- #
def test_failed_refresh_keeps_last_good(monkeypatch):
    import swr
    key = "grok:analyst:KEEP"
    swr._values.clear()
    # seed a good, but STALE, cached reading so serve triggers a refresh
    swr._values[key] = (time.time() - 7 * 3600, 0.9)
    _mock_rec(monkeypatch, None)                 # the refresh will fail
    val = gk._analyst_bias("KEEP")
    assert val == 0.9                            # stale-good served, not None
    time.sleep(0.25)                             # let the bg refresh run
    held = swr.peek(key)
    assert held == 0.9                           # NOT overwritten with None


def test_no_prior_still_caches_none(monkeypatch):
    import swr
    swr._values.clear()
    _mock_rec(monkeypatch, None)
    assert gk._analyst_bias("NEVERSEEN") is None   # genuine no-data is fine


# --- 4: the rate gate is shared by every Finnhub caller --------------------- #
def test_rate_gate_paces_calls(monkeypatch):
    monkeypatch.setattr(fh, "_min_gap", lambda: 0.05)
    fh._next_slot[0] = 0.0
    t0 = time.time()
    for _ in range(4):
        fh._rate_gate()
    assert time.time() - t0 >= 0.1               # 4 calls spaced by >=0.05s


def test_every_caller_goes_through_the_gate(monkeypatch):
    # recommendation AND peers (different services' calls) both gate.
    monkeypatch.setattr(fh, "configured", lambda: True)
    monkeypatch.setattr(fh, "_key", lambda: "k")
    fh._cache.clear()
    hits = {"n": 0}
    monkeypatch.setattr(fh, "_rate_gate", lambda: hits.__setitem__("n", hits["n"] + 1))
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    fh.recommendation("AAA")
    fh.peers("AAA")
    assert hits["n"] == 2                         # both callers paced


# --- 6: warmer is safe / non-blocking-friendly ------------------------------ #
def test_warmer_handles_empty_and_unconfigured(monkeypatch):
    monkeypatch.setattr(fh, "configured", lambda: False)
    assert gk.warm_analyst(["AAA", "BBB"]) == 0
    assert gk.warm_analyst([]) == 0


def test_warmer_counts_readings(monkeypatch):
    import swr; swr._values.clear()
    _mock_rec(monkeypatch, _rec(sb=10, b=10))
    assert gk.warm_analyst(["AAA", "BBB", "CCC"]) == 3


# --- 7: models that must NOT use recommendation counts stay off ------------- #
def test_kimi_price_target_unaffected_by_analyst_when_off(monkeypatch):
    # With with_analyst=False (the matrix path), the analyst feed must not touch
    # Kimi's price_target: the score is identical whether Finnhub is bullish or
    # absent -> recommendation counts are NOT substituted for price target.
    import kimi_service as ki, swr
    eq, tr = _eq_stub(), _trade_stub()
    _mock_rec(monkeypatch, _rec(sb=25, b=5)); swr._values.clear()
    bull = ki.score_one("AAA", eq, tr, {}, with_analyst=False, mkt=None)["score"]
    _mock_rec(monkeypatch, None); swr._values.clear()
    none_ = ki.score_one("AAA", eq, tr, {}, with_analyst=False, mkt=None)["score"]
    assert bull == none_


def test_claude_pro_estimate_dispersion_unaffected_when_off(monkeypatch):
    import claude_pro_service as cp, swr
    eq, tr = _eq_stub(), _trade_stub()
    _mock_rec(monkeypatch, _rec(sb=25, b=5)); swr._values.clear()
    bull = cp.score_one("AAA", eq, tr, {}, with_analyst=False, mkt=None)["score"]
    _mock_rec(monkeypatch, None); swr._values.clear()
    none_ = cp.score_one("AAA", eq, tr, {}, with_analyst=False, mkt=None)["score"]
    assert bull == none_


# --- UW analyst ratings are the primary source -------------------------------- #
def test_uw_analyst_ratings_are_primary(monkeypatch):
    import unusualwhales_service as uw
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"timestamp": now, "ticker": "AAA", "recommendation": "buy", "action": "upgraded"},
            {"timestamp": now, "ticker": "AAA", "recommendation": "overweight", "action": "maintained"}]
    monkeypatch.setattr(uw, "analyst_actions", lambda s, n=50: {"status": "OK", "data": rows})
    monkeypatch.setattr(fh, "configured", lambda: True)
    monkeypatch.setattr(fh, "recommendation", lambda s: _rec(ss=30))   # Finnhub bearish, must be ignored
    assert gk._analyst_bias_calc("AAA") > 0.5


def test_uw_empty_falls_back_to_finnhub(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "analyst_actions", lambda s, n=50: {"status": "OK", "data": []})
    _mock_rec(monkeypatch, _rec(sb=20, b=10))
    assert gk._analyst_bias_calc("AAA") > 0.5
