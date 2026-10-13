"""The earnings scoring engine: decision tiers and the STRONG upgrade.

The Earnings Trade tab (profile="default") uses tighter cuts than the shared
engine default and a STRONG tier; My Calls (profile="final") and the options
straddle keep the engine's plain two-tier call. These pin that split.
"""

import earnings_engine as eng
from earnings_engine import Param


def _one(bias: float):
    """A single 100-weight param whose bias maps straight to the score:
    score = 50 + bias*50 (so bias 0.64 -> 82, -0.40 -> 30)."""
    return [Param(name="x", label="x", weight=100, bias=bias)]


def _dec(bias, **kw):
    return eng.score(_one(bias), pos_label="BUY", neg_label="SELL", **kw)["decision"]


# --- Earnings tab thresholds (65/35) with the STRONG tier (80/20) -----------

EARN = dict(buy_at=65.0, sell_at=35.0, strong_buy_at=80.0, strong_sell_at=20.0)


def test_strong_buy_at_80():
    assert _dec(0.64, **EARN) == "STRONG BUY"      # score 82


def test_plain_buy_between_65_and_80():
    assert _dec(0.40, **EARN) == "BUY"             # score 70


def test_no_trade_in_the_middle():
    assert _dec(0.20, **EARN) == "NO TRADE"        # score 60 (< 65)
    assert _dec(0.0, **EARN) == "NO TRADE"         # score 50


def test_plain_sell_between_20_and_35():
    assert _dec(-0.40, **EARN) == "SELL"           # score 30


def test_strong_sell_at_20():
    assert _dec(-0.64, **EARN) == "STRONG SELL"    # score 18


# --- Regression: other callers are untouched --------------------------------

def test_defaults_have_no_strong_tier():
    # My Calls path: engine defaults (58/42), no strong thresholds passed.
    assert _dec(0.64) == "BUY"                     # score 82 stays plain BUY
    assert _dec(-0.64) == "SELL"


def test_options_straddle_call_unchanged():
    d = eng.score(_one(0.64), pos_label="STRADDLE", neg_label="NO TRADE",
                  buy_at=58.0, sell_at=-1.0)["decision"]
    assert d == "STRADDLE"
