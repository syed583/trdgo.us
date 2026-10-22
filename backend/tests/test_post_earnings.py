"""Phase 7 tests — experimental post-earnings signal engine (pure logic).
BUY/SELL/WAIT, four hypotheses, WAIT on missing/conflict, and the rule that a
positive surprise never auto-buys."""
import post_earnings_service as pe


def _r(reaction=None, v5=0.0, ema5=0.0, mom5=0.0, trend15=None, eps=None, cov=80.0, missing=None):
    return {"reaction": reaction, "coverage_pct": cov,
            "tf5": {"vwap_pos": v5, "ema": ema5, "momentum": mom5},
            "tf15": ({"trend": trend15} if trend15 is not None else None),
            "eps_surprise_pct": eps, "missing": missing or []}


def test_bullish_continuation():
    s = pe.pe_signal(_r(reaction=0.5, v5=0.6, ema5=0.5, mom5=0.4, trend15=0.5, eps=5.0))
    assert s["signal"] == "BUY" and s["hypothesis"] == "bullish_continuation"


def test_bearish_continuation():
    s = pe.pe_signal(_r(reaction=-0.5, v5=-0.6, ema5=-0.5, mom5=-0.4, trend15=-0.5, eps=-5.0))
    assert s["signal"] == "SELL" and s["hypothesis"] == "bearish_continuation"


def test_bullish_reversal():
    # initial selloff (reaction<0) reclaimed on 5m (tf5>0), non-negative surprise
    s = pe.pe_signal(_r(reaction=-0.4, v5=0.6, ema5=0.5, mom5=0.4, trend15=None, eps=2.0))
    assert s["signal"] == "BUY" and s["hypothesis"] == "bullish_reversal"


def test_bearish_reversal():
    s = pe.pe_signal(_r(reaction=0.4, v5=-0.6, ema5=-0.5, mom5=-0.4, trend15=None, eps=-2.0))
    assert s["signal"] == "SELL" and s["hypothesis"] == "bearish_reversal"


def test_positive_surprise_does_not_auto_buy():
    # strong positive surprise but NO reaction / flat intraday -> must NOT be BUY
    s = pe.pe_signal(_r(reaction=0.0, v5=0.0, ema5=0.0, mom5=0.0, eps=20.0))
    assert s["signal"] == "WAIT"


def test_wait_on_missing_reaction():
    s = pe.pe_signal(_r(reaction=None, v5=0.5, ema5=0.5, eps=5.0))
    assert s["signal"] == "WAIT" and s["hypothesis"] == "missing_data"


def test_wait_on_low_coverage():
    s = pe.pe_signal(_r(reaction=0.5, v5=0.5, ema5=0.5, eps=5.0, cov=20.0))
    assert s["signal"] == "WAIT" and s["hypothesis"] == "missing_data"


def test_wait_on_timeframe_conflict():
    # 5m bullish, 15m bearish -> WAIT (conflict)
    s = pe.pe_signal(_r(reaction=0.5, v5=0.6, ema5=0.6, mom5=0.6, trend15=-0.6, eps=5.0))
    assert s["signal"] == "WAIT" and s["hypothesis"] == "conflict"


def test_confidence_scales_with_coverage():
    hi = pe.pe_signal(_r(reaction=0.5, v5=0.6, ema5=0.5, mom5=0.4, trend15=0.5, eps=5.0, cov=90.0))
    lo = pe.pe_signal(_r(reaction=0.5, v5=0.6, ema5=0.5, mom5=0.4, trend15=0.5, eps=5.0, cov=50.0))
    assert hi["confidence"] > lo["confidence"]
