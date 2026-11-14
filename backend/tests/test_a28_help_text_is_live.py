"""A.28: the Strategy Trades help text is written from the rules the SERVER sends, not from numbers typed into the page.
It had drifted - it still said breakeven at 50% of the distance to the target, a 15:50 close, and nothing about the
short market filter, all of which A.22 and A.27 had changed. These tests fail if it ever drifts again.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import demo_trade_service as dts  # noqa: E402
import tab_settings as cfg  # noqa: E402

PAGE = (ROOT.parent / "frontend" / "src" / "pages" / "StrategyTradesPage.tsx").read_text(encoding="utf-8")
NOTE = PAGE[PAGE.index('className="st-note"'):]
NOTE = NOTE[:NOTE.index("<b>Take Now?</b>")]


def test_the_blotter_sends_every_number_the_text_needs():
    src = (ROOT / "demo_trade_service.py").read_text(encoding="utf-8")
    rules = src[src.index('"rules": {'):]
    rules = rules[:rules.index("},")]
    for field in ("target_atr_mult", "breakeven_atr", "short_market_filter", "short_market_proxies",
                  "score_drop_exit", "eod_close_et", "entry_window_et", "max_stop_pct", "notional_usd"):
        assert field in rules, "the blotter does not send %s, so the text cannot show it" % field


def test_the_text_reads_the_live_rules_and_hard_codes_no_rule_number():
    assert "const rules: any = d?.rules || {};" in PAGE
    for field in ("rules.target_atr_mult", "rules.breakeven_atr", "rules.short_market_filter",
                  "rules.score_drop_exit", "rules.eod_close_et", "rules.max_stop_pct", "rules.entry_window_et"):
        assert field in NOTE, "the help text does not read %s" % field
    # the stale sentences are gone
    for stale in ("50% of the distance", "15:50 ET", "10 min before an early close", "$10,000 notional"):
        assert stale not in NOTE, "the help text still says %r" % stale


def test_the_text_says_agent_and_names_no_model():
    assert "per agent per day" in NOTE and "each agent" in NOTE
    for name in ("Grok", "Meta", "Kimi", "Gemini", "ChatGPT", "model's"):
        assert name not in NOTE, "the help text mentions %r" % name


def test_the_defaults_in_the_text_match_what_is_actually_shipped():
    """If the server ever omits a field the page falls back to a literal; those literals must not themselves be stale."""
    assert "?? '15:45'" in NOTE and "?? 2.5" in NOTE and "?? 15" in NOTE
    assert cfg.PAPER_EXECUTION["max_stop_pct"] == 2.5
    assert dts.SCORE_DROP_EXIT == 15.0
    assert cfg.PAPER_EXECUTION["eod_minutes_before_close"] == 15        # 16:00 - 15 min = 15:45
