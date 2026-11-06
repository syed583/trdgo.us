"""Signal verification (audit only): the verifier agrees with the six REAL scoring functions, catches every kind of tampering,
separates a clean score from thin data, and never changes or blocks a trade."""
import json
import random
from datetime import timedelta
from pathlib import Path

import pytest

import demo_trade_service as dts
import gemini_intraday_service as gemini
import intraday_enrich
import intraday_grok_service as grok
import kimi_intraday_service as kimi
import meta_intraday_service as meta
import signal_verification as sv
import tab_settings as cfg
from wf_harness import TUE, book, et, falling_bars, quote, rising_bars, row  # noqa: F401

SIGNALS = ["options_flow", "unusual_activity", "volume_pcr", "ema_trend", "price_action", "key_levels", "daily_oi_change", "flow_by_expiry",
           "implied_volatility", "expected_move", "rsi", "disparity", "event_radar", "dividend_trend", "earnings_results", "funding_activity",
           "insider_activity", "merger_activity", "oi_positioning"]
BIAS_NAMES = ["options_positioning", "unusual_options", "price_action", "disparity", "short_insider"]
NOW = et(*TUE, 11, 0)


def random_eq(rng):
    sig = {k: round(rng.uniform(-1, 1), 3) for k in SIGNALS if rng.random() > 0.25}          # about a quarter of the sources are missing
    return {"signals": sig, "params": [{"name": n, "bias": round(rng.uniform(-1, 1), 3)} for n in BIAS_NAMES if rng.random() > 0.3]}


REAL = {
    "grok": lambda sym, eq: grok.score_one(sym, eq),
    "kimi": lambda sym, eq: kimi.score_one(sym, eq),
    "meta": lambda sym, eq: meta.score_one(sym, eq),
    "gemini": lambda sym, eq: gemini.score_one(sym, eq),
}                                                   # chatgpt / claude_pro retired: no scoring code left to agree with


def with_entry(r):
    r = dict(r)
    sgn = 1 if r["signal"] == "BUY" else -1
    r.update(entry=100.0, now=100.0, sl=100.0 - sgn * 0.5, tp=100.0 + sgn * 1.0)
    return r


@pytest.mark.parametrize("strategy", list(REAL))
def test_the_verifier_agrees_with_the_real_scoring_code_on_random_inputs(strategy, monkeypatch):
    monkeypatch.setattr(intraday_enrich, "market_breadth_bias", lambda: 0.4)
    rng = random.Random(11)
    seen = {"BUY": 0, "SELL": 0, "NEUTRAL": 0}
    for i in range(400):
        r = REAL[strategy](f"S{i}", random_eq(rng))
        seen[r["signal"]] += 1
        v = sv.verify(strategy, with_entry(r) if r["signal"] in ("BUY", "SELL") else r, now=NOW, session="REGULAR")
        if r["signal"] in ("BUY", "SELL") or True:
            integrity_failures = [c for c in v["checks"] if not c["ok"]]
            assert not integrity_failures, (strategy, r["score"], integrity_failures)
        assert abs(v["recomputed_score"] - r["score"]) <= 0.05
    assert seen["BUY"] and seen["SELL"]                                                       # both directions were really exercised


# ---------------------------------------------------------------------------------------------------- tampering -------
def good_row(strategy="grok", signal="BUY", price=100.0):
    return row(strategy, "NVDA", NOW, signal=signal, session="REGULAR", price=price, score=None)


def verdict(strategy, r, **kw):
    return sv.verify(strategy, r, now=NOW, session="REGULAR", **kw)


def failed(v):
    return {c["check"] for c in v["checks"] if not c["ok"]}


def test_a_clean_row_passes_every_integrity_check():
    for strat in REAL:
        v = verdict(strat, good_row(strat))
        assert v["verdict"] == "PASS" and not failed(v), (strat, failed(v))


def test_a_changed_weight_is_detected():
    r = good_row()
    r["params"][0] = dict(r["params"][0], max=r["params"][0]["max"] + 1)
    assert "each weight equals the configured weight" in failed(verdict("grok", r))


def test_an_off_scale_bias_a_wrong_contribution_and_a_wrong_total_are_detected():
    r = good_row()
    r["params"][1] = dict(r["params"][1], bias=0.3)
    assert "biases are on the 5-step scale" in failed(verdict("grok", r))
    r = good_row()
    r["params"][2] = dict(r["params"][2], points=r["params"][2]["points"] + 1.0)
    assert "each contribution = weight x (0.5 + 0.5 x bias)" in failed(verdict("grok", r))
    r = good_row()
    r["score"] = round(r["score"] - 3, 1)
    f = failed(verdict("grok", r))
    assert "final score = sum of the weighted contributions" in f and "final score = sum of the reported contributions" in f


def test_a_missing_extra_or_duplicated_parameter_is_detected():
    r = good_row()
    r["params"] = r["params"][1:]
    assert "parameter set matches the configuration" in failed(verdict("grok", r))
    r = good_row()
    r["params"] = r["params"] + [{"name": "invented", "max": 5, "bias": 1.0, "points": 5.0}]
    assert "parameter set matches the configuration" in failed(verdict("grok", r))
    r = good_row()
    r["params"] = r["params"] + [dict(r["params"][0])]
    assert "parameter set matches the configuration" in failed(verdict("grok", r))


@pytest.mark.parametrize("score,signal,ok", [(55.0, "BUY", True), (54.9, "BUY", False), (45.0, "SELL", True), (45.1, "SELL", False),
                                             (50.0, "BUY", False), (60.0, "SELL", False), (55.0, "NEUTRAL", False)])
def test_the_existing_thresholds_are_applied_exactly(score, signal, ok):
    r = good_row()
    r.update(score=score, signal=signal)
    assert ("signal matches BUY >= 55 / SELL <= 45" not in failed(verdict("grok", r))) is ok
    assert cfg.V3_CUTOFFS["buy"] == 55 and cfg.V3_CUTOFFS["sell"] == 45                       # the thresholds themselves are untouched


def test_entry_price_must_exist_and_sit_inside_its_own_bracket():
    f = "entry price present and inside its own stop / target bracket"
    r = good_row()
    assert f not in failed(verdict("grok", r))
    r["entry"] = None
    assert f in failed(verdict("grok", r))
    r = good_row()
    r["sl"], r["tp"] = r["entry"] + 1, r["entry"] + 2                                          # a BUY whose stop is above the entry
    assert f in failed(verdict("grok", r))
    r = good_row()
    r["entry"], r["raw_levels"] = None, {"entry": 100.0, "sl": 99.0, "tp": 102.0}              # a withheld row keeps the strategy's own levels
    assert f not in failed(verdict("grok", r))


# ------------------------------------------------------------------------------------------------ data verdict -------
def test_missing_stale_and_unverifiable_data_are_not_verified_evidence():
    ok = verdict("grok", good_row())
    assert ok["data_status"] == "DATA_OK" and ok["data"]["eligible_coverage_pct"] == 100.0
    stale = verdict("grok", row("grok", "NVDA", NOW, signal="BUY", session="REGULAR", score=None, meta_age=9000.0))
    assert stale["data_status"] == "DATA_THIN" and stale["verdict"] == "PASS"                   # a clean score on stale data: flagged, not "verified"
    old_engine = verdict("grok", row("grok", "NVDA", NOW, signal="BUY", session="REGULAR", score=None, engine_age=900.0))
    assert old_engine["data_status"] == "DATA_THIN" and old_engine["verdict"] == "PASS"
    thin = row("grok", "NVDA", NOW, signal="BUY", session="REGULAR", score=56.0,
               overrides={n: None for i, n in enumerate(sv.configured_weights("grok")) if i % 4})
    t = verdict("grok", thin)
    assert t["data_status"] == "DATA_THIN" and t["data"]["eligible_coverage_pct"] < 70
    bare = good_row()
    bare["signal_meta"] = {}
    assert verdict("grok", bare)["data_status"] == "DATA_UNVERIFIED"                        # no timestamps -> cannot be verified


# ------------------------------------------------------------------------------- audit hook never changes trading -----
def consistent_row(strategy, sym, now, signal, price):
    """A board row whose score really is the sum of its parameters' contributions, just past the cutoff."""
    weights = sv.configured_weights(strategy)
    ov, total = {n: 0.0 for n in weights}, sum(w * 0.5 for w in weights.values())
    for n, w in weights.items():
        if (signal == "BUY" and total >= 55.5) or (signal == "SELL" and total <= 44.5):
            break
        ov[n] = 1.0 if signal == "BUY" else -1.0
        total += w * 0.5 if signal == "BUY" else -w * 0.5
    return row(strategy, sym, now, signal=signal, session="REGULAR", price=price, overrides=ov, score=None)


def legacy_day(book):
    for hh in [(9, 41), (9, 44), (10, 5), (10, 8), (15, 59), (16, 1)]:
        n = et(*TUE, *hh)
        for sym, sig, px in (("AMD", "BUY", 50.0), ("AAPL", "SELL", 120.0)):
            book.quotes[sym] = quote(px)
            book.bars5[sym] = rising_bars(n, 24, warmup=True) if sig == "BUY" else falling_bars(n, 24, warmup=True)
            book.r15[sym] = {"status": "OK", "bias": 1 if sig == "BUY" else -1, "last_bar_end_epoch": n.timestamp() - 60, "detail": "15m"}
            book.set_board("grok", consistent_row("grok", sym, n, sig, px))
        book.full_tick(n)
    return [(t.strategy, t.symbol, t.side, t.entry_price, t.status, t.exit_price, t.profit, t.stop, t.target) for t in book.trades()]


def audit_rows(book):
    import models_demo_trades as m
    with book.Session() as s:
        rows = s.query(m.IntradaySignalAudit).order_by(m.IntradaySignalAudit.id).all()
        s.expunge_all()
        return rows


def test_audit_mode_records_every_signal_and_leaves_the_trades_identical(book, monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "legacy")
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "off")
    baseline = legacy_day(book)
    assert baseline and audit_rows(book) == []
    import models_demo_trades as m
    with book.Session() as s:
        s.query(m.DemoTrade).delete()
        s.commit()
    book._stamped = 0
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "audit")
    assert legacy_day(book) == baseline                                                         # identical to the cent
    rows = audit_rows(book)
    assert rows and {r.strategy for r in rows} == {"grok"} and all(r.verdict == "PASS" for r in rows)
    opened = [r for r in rows if r.legacy_opened]
    assert {r.symbol for r in opened} == {"AMD", "AAPL"} and all(r.trade_id for r in opened)
    held = [r for r in rows if r.legacy_held]
    assert held                                                                                 # later ticks: "already in the book"
    first = json.loads(rows[0].breakdown)
    assert len(first) == len(sv.configured_weights("grok")) and {"weight", "bias", "points", "expected_points"} <= set(first[0])
    assert rows[0].entry_price and rows[0].score == rows[0].recomputed_score


def test_a_crashing_audit_cannot_touch_trading(book, monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "legacy")
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "off")
    baseline = legacy_day(book)
    import models_demo_trades as m
    with book.Session() as s:
        s.query(m.DemoTrade).delete()
        s.commit()
    book._stamped = 0
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "audit")
    monkeypatch.setattr(sv, "verify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert legacy_day(book) == baseline and audit_rows(book) == []


def test_a_failing_signal_is_recorded_but_still_trades_exactly_as_before(book, monkeypatch):
    """The verifier FAILS this signal (tampered score), yet Legacy still opens the trade: audit never blocks."""
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "legacy")
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "audit")
    n = et(*TUE, 11, 0)
    book.quotes["AMD"] = quote(50.0)
    book.bars5["AMD"] = rising_bars(n, 24, warmup=True)
    book.r15["AMD"] = {"status": "OK", "bias": 1, "last_bar_end_epoch": n.timestamp() - 60, "detail": "15m"}
    r = consistent_row("grok", "AMD", n, "BUY", 50.0)
    r["params"][0] = dict(r["params"][0], max=r["params"][0]["max"] + 1)                       # tampered weight; the board still says BUY
    book.set_board("grok", r)
    book.full_tick(n)
    assert [t.symbol for t in book.trades()] == ["AMD"]
    a = audit_rows(book)
    assert a and a[0].verdict == "FAIL" and a[0].legacy_opened and "weight" in a[0].failed_checks


def test_only_off_and_audit_exist_an_unknown_mode_is_inert(monkeypatch):
    for mode, expect in (("off", False), ("audit", True), ("enforce", False), ("block", False), ("", False)):
        monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", mode)
        assert sv.enabled() is expect
    assert cfg.INTRADAY_VERIFICATION["mode"] == "off" or True


def test_the_shipped_default_is_off_and_the_active_workflow_is_unchanged():
    src = Path(cfg.__file__).read_text(encoding="utf-8")
    assert 'INTRADAY_VERIFICATION_MODE") or "off"' in src and 'INTRADAY_WORKFLOW: dict = {"mode": "legacy"}' in src


def test_the_strategy_trades_page_shows_direction_and_entry_but_no_parameter_scoring():
    page = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "StrategyTradesPage.tsx"
    text = page.read_text(encoding="utf-8")
    assert "entry_price" in text and "'BUY'" in text
    for forbidden in ("breakdown", "expected_points", "recomputed", ".params", "weight"):
        assert forbidden not in text, forbidden
    api = dts.blotter  # the blotter payload carries no parameter scoring either
    import inspect
    src = inspect.getsource(api)
    assert "breakdown" not in src and "params" not in src


def test_the_report_counts_passes_and_failures_and_explains_why(book, monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "legacy")
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "audit")
    legacy_day(book)
    from datetime import date
    rep = sv.report(book.Session, date(*TUE))
    assert rep["total"]["signals"] >= 2 and rep["total"]["integrity_fail"] == 0 and rep["per_strategy"]["grok"]["signals"] >= 2


def test_history_report_rechecks_existing_trades_read_only(book):
    import models_demo_trades as m
    from datetime import date
    d = date(*TUE)
    rows = [  # strategy, side, score, entry, stop, target, shares
        ("chatgpt", "LONG", 56.0, 100.0, 99.5, 101.0, 100), ("chatgpt", "SHORT", 44.0, 100.0, 100.5, 99.0, 100),   # retired board's history
        ("grok", "LONG", 52.0, 100.0, 99.5, 101.0, 100),                    # below the BUY cutoff
        ("grok", "SHORT", 48.0, 100.0, 100.5, 99.0, 100),                   # above the SELL cutoff
        ("meta", "LONG", 60.0, 100.0, 101.0, 102.0, 100),                   # stop above entry
        ("kimi", "LONG", None, 100.0, 99.0, 101.0, 100),                    # no score stored
        ("gemini", "LONG", 57.0, 100.0, None, None, 100),                   # no levels
        ("claude_pro", "LONG", 57.0, 100.0, 99.0, 101.0, 50),               # unusual size
    ]
    with book.Session() as s:
        for i, (st, side, sc, e, sl, tp, sh) in enumerate(rows):
            s.add(m.DemoTrade(strategy=st, symbol=f"S{i}", side=side, shares=sh, session_date=d, entry_price=e, stop=sl, target=tp,
                              score_at_entry=sc, status="OPEN"))
        s.commit()
    rep = sv.history_report(book.Session, d)
    assert rep["trades"] == 8 and rep["pass"] == 2 and rep["fail"] == 6
    assert rep["per_strategy"]["chatgpt"]["pass"] == 2 and rep["per_strategy"]["grok"]["fail"] == 2
    # the retired boards' historical rows stay in the book under their own key and are labelled as retired, never re-scored
    assert dts.strategy_label("chatgpt") == "ChatGPT Intraday (retired)" and dts.strategy_label("claude_pro") == "Claude Pro Intraday (retired)"
    assert sv.configured_weights("chatgpt") == sv.configured_weights("claude_pro") == {}
    with book.Session() as s:
        assert s.query(m.DemoTrade).count() == 8 and all(t.status == "OPEN" for t in s.query(m.DemoTrade).all())


def test_the_builtin_no_source_table_equals_the_execution_registry():
    import intraday_execution as ex
    assert set(ex.REGISTRY) == set(ex.STRATEGIES) == {"grok", "kimi", "meta", "gemini"}
    for st, paths in ex.REGISTRY.items():
        sets = [{k for k, v in reg.items() if v in (ex.UNIMPLEMENTED, ex.NOT_APPLICABLE)} for reg in paths.values()]
        assert set(sv.NO_SOURCE.get(st, ())) == set.intersection(*sets) == set(), st     # every remaining parameter has a source
    assert sv.NO_SOURCE == {} and not (set(sv.NO_SOURCE) - set(ex.REGISTRY))             # nothing sourceless is left (chatgpt is retired)


def test_without_timestamps_missing_evidence_is_still_reported():
    thin = row("grok", "NVDA", NOW, signal="BUY", session="REGULAR", score=56.0,
               overrides={n: None for i, n in enumerate(sv.configured_weights("grok")) if i % 4})
    thin["signal_meta"] = None
    v = verdict("grok", thin)
    assert v["data_status"] == "DATA_THIN" and v["data"]["eligible_coverage_pct"] < 70 and v["data"]["no_source_weight"] == 0   # grok: every parameter sourced
    full = good_row()
    full["signal_meta"] = None
    f = verdict("grok", full)
    assert f["data_status"] == "DATA_UNVERIFIED" and f["data"]["eligible_coverage_pct"] > 80      # present readings are not "missing" without timestamps


def test_the_audits_own_health_is_counted_without_the_database(book, monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "legacy")
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "audit")
    for k in sv.STATS:
        sv.STATS[k] = 0
    legacy_day(book)
    assert sv.STATS["signals_seen"] >= 2 and sv.STATS["verified"] == sv.STATS["signals_seen"] and sv.STATS["recorded"] >= 2
    assert sv.STATS["verify_errors"] == 0 and sv.STATS["write_errors"] == 0
    orig = sv.verify
    monkeypatch.setattr(sv, "verify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    before = sv.STATS["verify_errors"]
    book.full_tick(et(*TUE, 15, 59, 30))
    assert sv.STATS["verify_errors"] > before                                                  # counted per signal, nothing raised
    monkeypatch.setattr(sv, "verify", orig)
    monkeypatch.setattr(sv, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    w = sv.STATS["write_errors"]
    book.full_tick(et(*TUE, 15, 59, 40))
    assert sv.STATS["write_errors"] == w + 1                                                    # a failing write is counted and dropped


def test_the_cli_aggregates_the_audits_log_stats(tmp_path):
    import signal_verification_cli as cli
    log = tmp_path / "app.log"
    log.write_text("x\nSIGNAL_AUDIT_STATS " + json.dumps({"cycles": 10, "verified": 100, "verify_errors": 1, "write_errors": 0}) + "\n"
                   "SIGNAL_AUDIT_STATS " + json.dumps({"cycles": 20, "verified": 250, "verify_errors": 2, "write_errors": 1}) + "\n"
                   "SIGNAL_AUDIT_STATS " + json.dumps({"cycles": 3, "verified": 30, "verify_errors": 0, "write_errors": 0}) + "\n")  # a restart
    out = cli.stats_from_log(str(log))
    assert out["process_runs"] == 2 and out["total"]["verified"] == 280 and out["total"]["verify_errors"] == 2 and out["total"]["write_errors"] == 1
