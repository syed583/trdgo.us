"""A.27 (owner decisions 2026-11-14, from the measured reports):
  * a SHORT opens only when SPY or QQQ is below its own day open; longs are never affected; an unreadable market refuses
    the short rather than guessing;
  * the target sits at 0.5 ATR instead of the board's 1.0 ATR, on both sides, and the STOP is left exactly where the
    board put it;
  * every tick records the score it saw for each open trade, so the score-exit threshold can be tested later.
Rejected and deliberately absent: the VWAP rule, the agreement rule and a 2.0% short stop cap. The score exit and the
2.5% stop cap are unchanged. No provider request and no database write happens in these tests.
"""
import json
import sys

import pytest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import demo_trade_service as dts  # noqa: E402
import tab_settings as cfg  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 15, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _gate_on():
    """conftest defaults the short market gate OFF for the whole suite; these tests are about it, so turn it on."""
    saved = cfg.PAPER_EXECUTION.get("short_market_filter")
    cfg.PAPER_EXECUTION["short_market_filter"] = True
    yield
    cfg.PAPER_EXECUTION["short_market_filter"] = saved


# ----------------------------------------------------------------- the target
def test_the_target_is_half_an_atr_and_the_stop_is_untouched():
    assert cfg.PAPER_EXECUTION["target_atr_mult"] == 0.5
    assert dts.target_atr_mult() == 0.5
    # board: entry 100, stop 99.5 (0.5 ATR), target 101 (1.0 ATR) -> ATR = 1.00
    sl, tp = dts.rebase_levels("LONG", 100.0, 99.5, 101.0, 100.0)
    assert sl == 99.5, "the stop must stay where the board put it"
    assert tp == 100.5, "the target must be half an ATR"
    sl, tp = dts.rebase_levels("SHORT", 100.0, 100.5, 99.0, 100.0)
    assert (sl, tp) == (100.5, 99.5)


def test_the_target_scales_from_the_real_entry_not_the_board_price():
    """The board priced at 200; we filled at 202. Distances are the board's, measured from our entry."""
    sl, tp = dts.rebase_levels("LONG", 200.0, 198.0, 204.0, 202.0)
    assert sl == 200.0 and tp == 204.0          # stop 2.00 below, target 0.5 x 4.00 = 2.00 above


def test_the_old_behaviour_is_one_setting_away():
    import contextlib
    saved = cfg.PAPER_EXECUTION["target_atr_mult"]
    with contextlib.suppress(Exception):
        cfg.PAPER_EXECUTION["target_atr_mult"] = 1.0
        assert dts.rebase_levels("LONG", 100.0, 99.5, 101.0, 100.0) == (99.5, 101.0)
    cfg.PAPER_EXECUTION["target_atr_mult"] = saved


# ----------------------------------------------------------------- the short market filter
def _market(monkeypatch, quotes):
    import live_market_service as market
    monkeypatch.setattr(market, "get_quote", lambda sym, *a, **k: quotes.get(sym.upper(), {}), raising=False)


def test_a_short_opens_only_when_a_proxy_is_below_its_day_open(monkeypatch):
    _market(monkeypatch, {"SPY": {"price": 599.0, "open": 600.0}, "QQQ": {"price": 500.0, "open": 499.0}})
    assert dts.short_block_reason("SHORT", NOW) is None          # SPY is down: that is enough
    _market(monkeypatch, {"SPY": {"price": 601.0, "open": 600.0}, "QQQ": {"price": 500.0, "open": 499.0}})
    reason = dts.short_block_reason("SHORT", NOW)
    assert reason and reason.startswith("SHORT_MARKET_NOT_DOWN"), reason
    assert "SPY 601.00 vs open 600.00" in reason                 # the reading is in the log line, not just a verdict


def test_longs_are_never_touched_by_the_filter(monkeypatch):
    _market(monkeypatch, {"SPY": {"price": 601.0, "open": 600.0}, "QQQ": {"price": 501.0, "open": 500.0}})
    assert dts.short_block_reason("LONG", NOW) is None


def test_an_unreadable_market_refuses_the_short_rather_than_guessing(monkeypatch):
    _market(monkeypatch, {})
    reason = dts.short_block_reason("SHORT", NOW)
    assert reason and reason.startswith("SHORT_MARKET_UNKNOWN"), reason
    down, detail = dts.market_is_down(NOW)
    assert down is None and "SPY/QQQ" in detail


def test_a_provider_failure_is_unknown_not_permission(monkeypatch):
    import live_market_service as market

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(market, "get_quote", boom, raising=False)
    assert str(dts.short_block_reason("SHORT", NOW)).startswith("SHORT_MARKET_UNKNOWN")


def test_the_filter_can_be_switched_off_without_touching_code(monkeypatch):
    _market(monkeypatch, {"SPY": {"price": 601.0, "open": 600.0}})
    monkeypatch.setitem(cfg.PAPER_EXECUTION, "short_market_filter", False)
    assert dts.short_block_reason("SHORT", NOW) is None


def test_the_entry_path_consults_the_filter():
    src = (ROOT / "demo_trade_service.py").read_text(encoding="utf-8")
    body = src[src.index('side = "LONG" if sig == "BUY" else "SHORT"'):]
    body = body[:body.index("s.add(row)")]
    assert "short_block_reason(side, et)" in body
    assert body.index("short_block_reason") < body.index("rebase_levels"), "the filter must run before the levels"


# ----------------------------------------------------------------- the per-tick score log
def test_a_tick_writes_one_line_per_open_trade(monkeypatch, tmp_path):
    monkeypatch.setenv("SCORE_TICK_DIR", str(tmp_path))
    t = SimpleNamespace(id=77, strategy="grok", symbol="AAA", side="LONG", score_at_entry=61.0, status="OPEN")
    dts._log_score_tick(t, 48.5, NOW)
    dts._log_score_tick(t, 47.0, NOW)
    f = tmp_path / ("%s.jsonl" % NOW.date().isoformat())
    recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 2
    assert recs[0]["trade_id"] == 77 and recs[0]["score"] == 48.5 and recs[0]["score_at_entry"] == 61.0
    assert recs[0]["strategy"] == "grok" and recs[0]["side"] == "LONG" and recs[0]["at"].startswith("2026-11-17")


def test_no_score_and_a_broken_log_are_both_harmless(monkeypatch, tmp_path):
    monkeypatch.setenv("SCORE_TICK_DIR", str(tmp_path / "sub"))
    t = SimpleNamespace(id=1, strategy="meta", symbol="BBB", side="SHORT", score_at_entry=40.0, status="OPEN")
    dts._log_score_tick(t, None, NOW)                       # nothing to record
    assert not (tmp_path / "sub").exists() or not list((tmp_path / "sub").glob("*.jsonl"))
    monkeypatch.setenv("SCORE_TICK_DIR", str(tmp_path / "file-not-dir"))
    (tmp_path / "file-not-dir").write_text("x", encoding="utf-8")
    dts._log_score_tick(t, 20.0, NOW)                       # must not raise


def test_the_manage_loop_records_the_tick_score():
    src = (ROOT / "demo_trade_service.py").read_text(encoding="utf-8")
    assert '_log_score_tick(t, info.get("score"), et)' in src
    assert cfg.PAPER_EXECUTION["score_tick_log"] is True


# ----------------------------------------------------------------- what must NOT have changed
def test_the_rejected_rules_were_not_built():
    src = (ROOT / "demo_trade_service.py").read_text(encoding="utf-8")
    low = src.lower()
    assert "vwap" not in low, "the rejected VWAP rule appears in the tracker"
    assert "agents_agree" not in low and "min_agents" not in low
    assert cfg.PAPER_EXECUTION["max_stop_pct"] == 2.5, "the stop cap must stay 2.5%"
    assert dts.SCORE_DROP_EXIT == 15.0, "the score exit must stay at 15 points"


def test_the_mt5_magic_numbers_and_the_strategy_map_are_untouched():
    import mt5_autotrade as mt5
    assert (mt5.magic_for("grok"), mt5.magic_for("meta")) == (240911, 240912)
    assert (mt5.magic_for("kimi"), mt5.magic_for("gemini")) == (240913, 240914)
    assert set(dts.MODELS) == {"grok", "meta", "kimi", "gemini"}


# ----------------------------------------------------------------- the breakeven trail (chosen by backtest)
def test_the_trail_fires_at_four_tenths_of_an_atr_not_before():
    """Target 100.5 is 0.5 ATR from 100, so ATR is 1.00 and the trail must wait for +0.40, not the old +0.25."""
    assert cfg.PAPER_EXECUTION["breakeven_atr"] == 0.40
    for last in (100.20, 100.24, 100.39):
        assert dts.breakeven_stop("LONG", 100.0, 99.5, 100.5, last) is None, last
    assert dts.breakeven_stop("LONG", 100.0, 99.5, 100.5, 100.40) == 100.0
    assert dts.breakeven_stop("LONG", 100.0, 99.5, 100.5, 100.80) == 100.0


def test_the_trail_works_the_same_way_on_a_short():
    assert dts.breakeven_stop("SHORT", 100.0, 100.5, 99.5, 99.80) is None
    assert dts.breakeven_stop("SHORT", 100.0, 100.5, 99.5, 99.60) == 100.0


def test_the_trail_never_moves_a_stop_against_the_trade():
    assert dts.breakeven_stop("LONG", 100.0, 100.0, 100.5, 100.90) is None      # already at the entry
    assert dts.breakeven_stop("SHORT", 100.0, 100.0, 99.5, 99.10) is None


def test_an_explicit_trigger_still_means_a_share_of_the_target_distance():
    """Existing callers pass a fraction; that path must not change meaning."""
    assert dts.breakeven_stop("LONG", 100.0, 99.5, 100.5, 100.25, 0.5) == 100.0
    assert dts.breakeven_stop("LONG", 100.0, 99.5, 100.5, 100.20, 0.5) is None


def test_clearing_the_atr_setting_restores_the_old_fraction_rule(monkeypatch):
    monkeypatch.setitem(cfg.PAPER_EXECUTION, "breakeven_atr", None)
    assert dts.breakeven_stop("LONG", 100.0, 99.5, 100.5, 100.25) == 100.0      # 0.5 of a 0.50 distance
