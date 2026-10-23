"""Intraday data safety (Phase 1): stale / missing / invalid data can never produce or confirm an
entry on the six AI intraday boards or the live 5-minute engine. All inputs are mocked: no API
calls, no production database."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import intraday_safety as safety
import swr

ET = ZoneInfo("America/New_York")
FRESH, STALE = 30.0, 99 * 3600.0


class AllSame(dict):
    """A signals dict that answers every key with the same bias (BUY: +, SELL: -)."""
    def __init__(self, v):
        super().__init__({"x": v})
        self.v = v

    def get(self, k, d=None):
        return self.v


NAMES = ["eps_estimates", "revenue_estimates", "guidance", "price_action", "options_positioning",
         "unusual_options", "disparity", "dark_pool", "peer_readthrough", "sector_market",
         "short_insider", "historical_reaction"]


def eq(bias):
    return {"params": [{"name": n, "bias": bias} for n in NAMES], "signals": AllSame(bias)}


# ----------------------------------------------------------------- pure gate ---
def row(signal="BUY", score=72.0, coverage=100.0, now=100.0):
    return {"symbol": "AAA", "signal": signal, "score": score, "coverage": coverage, "now": now,
            "entry": now, "tp": 102.0, "sl": 99.0,
            "params": [{"name": "a", "label": "Options flow", "bias": 1.0},
                       {"name": "b", "label": "Volume P/C", "bias": None}]}


def test_fresh_complete_data_passes_untouched():
    r = safety.gate(row(), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    assert r["signal"] == "BUY" and r["score"] == 72.0 and not r.get("wait") and r["data"]["ok"]


@pytest.mark.parametrize("kw, text", [
    (dict(signals_age_s=STALE, price_age=FRESH), "old"),
    (dict(signals_age_s=None, price_age=FRESH), "no build time"),
    (dict(signals_age_s=FRESH, price_age=STALE), "live price is"),
    (dict(signals_age_s=FRESH, price_age=None), "no timestamp"),
])
def test_stale_or_untimed_inputs_become_wait(kw, text):
    r = safety.gate(row(), phase="rth", **kw)
    assert r["signal"] == "WAIT" and r["tier"] == "LOW DATA" and r["wait"] is True
    assert r["score"] is None                                  # no misleading confidence
    assert r["score_raw"] == 72.0 and r["signal_raw"] == "BUY"   # kept for exit management only
    assert r["entry"] is None and r["tp"] is None and r["sl"] is None
    assert any(text in why for why in r["wait_reasons"])


def test_missing_price_and_low_coverage_explain_what_is_missing():
    r = safety.gate(row(coverage=40.0, now=None), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    assert r["signal"] == "WAIT"
    why = " ".join(r["wait_reasons"])
    assert "no live price" in why and "40%" in why and "needs 75%" in why
    assert "Volume P/C" in r["missing"]                         # names the missing input


def test_unhealthy_live_engine_blocks():
    for status in ("MARKET_STALE", "STOCK_STALE", "WITHHELD", "EXCLUDED"):
        r = safety.gate(row(), phase="rth", signals_age_s=FRESH, price_age=FRESH, model_status=status)
        assert r["signal"] == "WAIT" and any(status in w for w in r["wait_reasons"])


def test_buy_and_sell_are_treated_identically():
    for kw in (dict(signals_age_s=FRESH, price_age=FRESH), dict(signals_age_s=STALE, price_age=FRESH),
               dict(signals_age_s=FRESH, price_age=None)):
        b = safety.gate(row("BUY", 72), phase="rth", **kw)
        s = safety.gate(row("SELL", 28), phase="rth", **kw)
        assert (b["signal"] == "WAIT") == (s["signal"] == "WAIT")
        assert b["data"]["ok"] == s["data"]["ok"]


def test_age_limits_follow_the_session_phase():
    assert safety.max_signal_age("rth") == 600.0
    assert safety.max_signal_age("closed") == 18 * 3600.0
    ok = safety.gate(row(), phase="closed", signals_age_s=2 * 3600, price_age=FRESH)
    assert ok["signal"] == "BUY"                               # fine overnight ...
    bad = safety.gate(row(), phase="rth", signals_age_s=2 * 3600, price_age=FRESH)
    assert bad["signal"] == "WAIT"                             # ... not in the regular session


def test_effective_eq_prefers_a_fresher_live_reading(monkeypatch):
    monkeypatch.setattr(safety, "age_s", lambda k: 7200.0)     # snapshot is 2 h old
    live = {f"s{i}": 0.5 for i in range(10)}
    monkeypatch.setattr(safety, "live_signals", lambda s: (live, 45.0))
    base = {"params": [{"name": "x"}], "signals": {f"s{i}": 0.1 for i in range(10)}}
    e, age, src = safety.effective_eq("AAA", base)
    assert age == 45.0 and src == "live directional" and e["signals"]["s0"] == 0.5
    monkeypatch.setattr(safety, "live_signals", lambda s: ({"only": 1.0}, 45.0))   # too sparse to trust
    e, age, src = safety.effective_eq("AAA", base)
    assert age == 7200.0 and src == "earnings snapshot"


def test_compose_has_a_wait_bucket_and_counts():
    rows = [row("BUY", 70), dict(row("SELL", 30), symbol="BBB"),
            safety.gate(dict(row("BUY", 80), symbol="CCC"), phase="rth", signals_age_s=STALE, price_age=FRESH)]
    out = safety.compose(rows, "src", 3, "rth")
    assert out["counts"] == {"buy": 1, "sell": 1, "neutral": 0, "wait": 1}
    assert [r["symbol"] for r in out["wait"]] == ["CCC"] and out["scored"] == 2


def test_tradable_requires_a_passing_gate():
    assert safety.tradable(safety.gate(row(), phase="rth", signals_age_s=FRESH, price_age=FRESH))
    assert not safety.tradable(safety.gate(row(), phase="rth", signals_age_s=STALE, price_age=FRESH))
    assert not safety.tradable(row("NEUTRAL"))


# ------------------------------------------------------- the six boards -------
def _prep(monkeypatch, age, bias, phase="rth", price=(100.0, 2.0), price_age=None):
    import ai_trade_service as ats
    import intraday_grok_service as ig
    import intraday_indicators as ind
    import kimi_intraday_service as kin
    swr.clear()
    monkeypatch.setattr(ats, "UNIVERSE", ["AAA", "BBB"])
    monkeypatch.setattr(ind, "market_phase", lambda *a, **k: phase)
    monkeypatch.setattr(safety, "age_s", lambda key: (price_age if (price_age is not None and key.startswith("grokintra:px")) else age))
    monkeypatch.setattr(safety, "live_signals", lambda s: None)
    e = eq(bias)
    for mod in (ig, kin):
        monkeypatch.setattr(mod, "_eq_cached", lambda s, e=e: e)
        monkeypatch.setattr(mod, "_price_atr", lambda s: price)


def _boards():
    import gemini_intraday_service as gi
    import intraday_grok_service as ig
    import kimi_intraday_service as kin
    import meta_intraday_service as mi
    return [("grok", ig.board), ("meta", mi.board), ("kimi", kin.board), ("gemini", gi.board)]


@pytest.mark.parametrize("name_idx", [0, 1, 2, 3])
def test_equity_boards_fresh_data_gives_buys_and_sells_symmetrically(monkeypatch, name_idx):
    name, board = _boards()[name_idx]
    _prep(monkeypatch, FRESH, +0.9)
    up = board()
    _prep(monkeypatch, FRESH, -0.9)
    down = board()
    assert up["counts"]["buy"] == 2 and up["counts"]["sell"] == 0 and up["counts"]["wait"] == 0, name
    assert down["counts"]["sell"] == 2 and down["counts"]["buy"] == 0 and down["counts"]["wait"] == 0, name


@pytest.mark.parametrize("name_idx", [0, 1, 2, 3])
def test_equity_boards_stale_data_gives_wait_never_a_signal(monkeypatch, name_idx):
    name, board = _boards()[name_idx]
    for bias in (+0.9, -0.9):
        _prep(monkeypatch, STALE, bias)
        out = board()
        assert out["counts"]["buy"] == out["counts"]["sell"] == 0, (name, bias)
        assert out["counts"]["wait"] == 2 and len(out["wait"]) == 2
        w = out["wait"][0]
        assert w["signal"] == "WAIT" and w["score"] is None and w["wait_reasons"] and w["tp"] is None


@pytest.mark.parametrize("name_idx", [0, 1, 2, 3])
def test_equity_boards_missing_price_or_signals_give_wait(monkeypatch, name_idx):
    name, board = _boards()[name_idx]
    _prep(monkeypatch, FRESH, +0.9, price=(None, None))
    out = board()
    assert out["counts"]["wait"] == 2 and out["counts"]["buy"] == 0                 # no live price
    _prep(monkeypatch, FRESH, +0.9)
    import intraday_grok_service as ig
    import kimi_intraday_service as kin
    sparse = {"params": [{"name": "eps_estimates", "bias": 0.5}], "signals": {}}
    for mod in (ig, kin):
        monkeypatch.setattr(mod, "_eq_cached", lambda s: sparse)
    out = board()
    assert out["counts"]["buy"] == 0 and out["counts"]["sell"] == 0 and out["counts"]["wait"] == 2
    assert any("inputs have data" in " ".join(r["wait_reasons"]) for r in out["wait"])   # low coverage named


def _im2(status="OK", bias=+1.0):
    def P(mx=10.0, val=None):
        return {"value": (bias * mx) if val is None else val, "max": mx, "has_data": True}
    names = ["market", "sector", "vwap_pos", "ema_trend", "orb", "rsi", "rvol", "rel_strength",
             "flow", "unusual", "pc_ratio", "levels", "disparity", "gap", "news"]
    return {"symbol": "AAA", "status": status, "spot": 100.0, "atr": 2.0, "score": 70.0,
            "params": [dict(P(), name=n) for n in names], "spy_above_vwap": True}


@pytest.mark.parametrize("module", ["chatgpt_intraday_service", "claude_pro_intraday_service"])
def test_live_path_boards_gate_on_engine_age_and_status(monkeypatch, module):
    import importlib
    mod = importlib.import_module(module)
    for bias in (+1.0, -1.0):
        for age, status, expect_wait in ((FRESH, "OK", False), (STALE, "OK", True),
                                         (FRESH, "MARKET_STALE", True), (FRESH, "STOCK_STALE", True)):
            _prep(monkeypatch, age, +0.9, phase="rth")
            for sym in ("AAA", "BBB"):
                swr._values[f"im2:{sym}"] = (time.time(), dict(_im2(status, bias), symbol=sym))
            out = mod.board()
            assert (out["counts"]["wait"] == 2) is expect_wait, (module, bias, age, status)
            if expect_wait:
                assert out["counts"]["buy"] == out["counts"]["sell"] == 0
    swr.clear()


@pytest.mark.parametrize("module", ["chatgpt_intraday_service", "claude_pro_intraday_service"])
def test_closed_market_equity_path_is_gated_too(monkeypatch, module):
    import importlib
    import intraday_grok_service as ig
    mod = importlib.import_module(module)
    _prep(monkeypatch, STALE, +0.9, phase="closed")
    monkeypatch.setattr(ig, "_eq_cached", lambda s: eq(+0.9))
    stale = mod.board()
    assert stale["counts"]["buy"] == stale["counts"]["sell"] == 0 and stale["counts"]["wait"] == 2
    _prep(monkeypatch, 3600.0, +0.9, phase="closed", price_age=FRESH)    # 1 h-old signals are fine overnight
    monkeypatch.setattr(ig, "_eq_cached", lambda s: eq(+0.9))
    fresh = mod.board()
    assert fresh["counts"]["wait"] == 0 and fresh["counts"]["buy"] + fresh["counts"]["neutral"] == 2


# ------------------------------------------------- paper-trade entry safety ----
def test_tracker_row_map_keeps_wait_rows_for_exits_but_never_for_entries():
    import demo_trade_service as dts
    stale = safety.gate(dict(row("BUY", 75), symbol="OLD"), phase="rth", signals_age_s=STALE, price_age=FRESH)
    good = safety.gate(dict(row("SELL", 25), symbol="NEW"), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    m = dts._row_map({"buyers": [], "sellers": [good], "neutral": [], "wait": [stale]})
    assert m["NEW"]["tradable"] is True and m["OLD"]["tradable"] is False
    assert m["OLD"]["score"] == 75 and m["OLD"]["now"] == 100.0       # exit management still sees them


def test_tick_opens_trades_only_from_fresh_rows_both_sides(monkeypatch):
    import demo_trade_service as dts
    import database
    from models_demo_trades import DemoTrade
    raw = database.engine.raw_connection()                  # clear a scratch twin left by other tests
    try:
        raw.cursor().execute("DROP TABLE IF EXISTS demo_trades_local")
        raw.commit()
    finally:
        raw.close()
    dts._schema_ready = False
    dts.ensure_schema()
    db = database.SessionLocal()
    db.query(DemoTrade).delete()
    db.commit()
    db.close()
    fresh_buy = safety.gate(dict(row("BUY", 75), symbol="FB"), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    fresh_sell = safety.gate(dict(row("SELL", 25), symbol="FS"), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    old_buy = safety.gate(dict(row("BUY", 75), symbol="OB"), phase="rth", signals_age_s=STALE, price_age=FRESH)
    old_sell = safety.gate(dict(row("SELL", 25), symbol="OS"), phase="rth", signals_age_s=STALE, price_age=FRESH)
    board = {"buyers": [fresh_buy], "sellers": [fresh_sell], "neutral": [], "wait": [old_buy, old_sell]}
    monkeypatch.setattr(dts, "MODELS", {"grok": ("Grok", "x:y")})
    monkeypatch.setattr(dts, "_board", lambda spec: board)
    monkeypatch.setattr(dts, "_can_open", lambda *a, **k: True)
    dts.tick(force=True)
    db = database.SessionLocal()
    try:
        opened = {t.symbol: t.side for t in db.query(DemoTrade).all()}
    finally:
        db.query(DemoTrade).delete()
        db.commit()
        db.close()
    assert opened == {"FB": "LONG", "FS": "SHORT"}                   # stale BUY and stale SELL opened nothing


# ----------------------------------------------- live 5-minute engine ----------
class _Rec:
    def __init__(self):
        self.sql = []

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        return self


@pytest.mark.parametrize("side, call", [("BUY", "BUY"), ("SELL", "SELL")])
def test_engine_only_opens_or_confirms_on_healthy_data(monkeypatch, side, call):
    import intraday_signal_v2 as eng
    now = datetime(2026, 10, 22, 11, 0, tzinfo=ET)
    base = {"call": call, "side": side, "score": 70.0 if side == "BUY" else 30.0, "spot": 100.0, "atr": 2.0}
    monkeypatch.setattr(eng, "_pending", lambda *a, **k: None)
    ok = _Rec()
    eng._maybe_open(ok, "AAA", "2026-10-22", dict(base, status="OK"), now)
    assert any("INSERT INTO intraday_sig_v2_pending" in q for q in ok.sql)        # starts confirming
    for bad in ("MARKET_STALE", "STOCK_STALE", "WITHHELD", None):
        rec = _Rec()
        sc = dict(base, status=bad) if bad else dict(base)
        assert eng._maybe_open(rec, "AAA", "2026-10-22", sc, now) is None
        assert not any("INSERT" in q for q in rec.sql), (side, bad)                # nothing recorded
    # a stale scan also cancels confirmation progress rather than counting as the second scan
    monkeypatch.setattr(eng, "_pending", lambda *a, **k: {"side": side, "count": 1, "last_ts": now})
    rec = _Rec()
    eng._maybe_open(rec, "AAA", "2026-10-22", dict(base, status="STOCK_STALE"), now)
    assert any("DELETE FROM intraday_sig_v2_pending" in q for q in rec.sql)
    assert not any("INSERT INTO intraday_sig_v2 " in q for q in rec.sql)


def _compute_with(monkeypatch, *, bias, spy_stale, stock_stale):
    import intraday_model as im
    P = lambda mx=10.0: {"value": bias * mx, "max": mx, "has_data": True}   # noqa: E731
    monkeypatch.setattr(im, "bars_meta", lambda sym, critical=False: (
        [{"o": 1, "c": 1}], spy_stale if critical else stock_stale))
    monkeypatch.setattr(im.ind, "last_price", lambda b: 100.0)
    monkeypatch.setattr(im.ind, "session_bars", lambda b: b)
    monkeypatch.setattr(im.ind, "pct_change_today", lambda b: 0.1)
    monkeypatch.setattr(im.ind, "vwap", lambda b: 90.0 if bias > 0 else 110.0)
    monkeypatch.setattr(im, "_market", lambda: P())
    monkeypatch.setattr(im, "_sector", lambda s: P())
    monkeypatch.setattr(im, "_price_trend", lambda b, c: {n: P() for n in im.GROUP["B"]})
    monkeypatch.setattr(im, "_options", lambda s, spot, ref=None: {n: P() for n in im.GROUP["C"]})
    monkeypatch.setattr(im, "_gap", lambda b: P())
    monkeypatch.setattr(im, "_news", lambda s: P())
    monkeypatch.setattr(im, "_earnings_blocked", lambda s: False)
    monkeypatch.setattr(im, "_liquidity_ok", lambda s, spot: True)
    monkeypatch.setattr(im, "atr", lambda s: 2.0)
    return im._compute("AAA")


@pytest.mark.parametrize("bias, call", [(+1.0, "STRONG BUY"), (-1.0, "STRONG SELL")])
def test_live_engine_flags_each_kind_of_staleness_on_both_sides(monkeypatch, bias, call):
    healthy = _compute_with(monkeypatch, bias=bias, spy_stale=False, stock_stale=False)
    assert healthy["status"] == "OK" and healthy["call"] == call
    spy = _compute_with(monkeypatch, bias=bias, spy_stale=True, stock_stale=False)
    assert spy["status"] == "MARKET_STALE" and spy["market_stale"] is True
    stock = _compute_with(monkeypatch, bias=bias, spy_stale=False, stock_stale=True)
    assert stock["status"] == "STOCK_STALE" and stock["stock_stale"] is True
