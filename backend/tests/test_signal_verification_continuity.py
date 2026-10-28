"""Trading-continuity and failure-isolation evidence for the audit (portable: runs unchanged on the hotfix build and the feature branch).

Proves: the audit runs only AFTER the Legacy transaction has committed (a second database connection already sees the trades),
a time budget bounds it, an audit fault never changes a trade, attribution / identity / staleness are reported honestly."""
import random
import time
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import demo_trade_service as dts
import intraday_enrich
import intraday_grok_service as grok
import kimi_intraday_service as kimi
import models_demo_trades as m
import signal_verification as sv
import tab_settings as cfg

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 27, 11, 0, tzinfo=ET)
SIGNALS = ["options_flow", "unusual_activity", "volume_pcr", "ema_trend", "price_action", "key_levels", "daily_oi_change", "flow_by_expiry",
           "implied_volatility", "expected_move", "rsi", "disparity", "event_radar"]
DDL = """CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, strategy VARCHAR(24) NOT NULL, symbol VARCHAR(16) NOT NULL,
    side VARCHAR(5) NOT NULL, shares INTEGER NOT NULL, session_date DATE NOT NULL, entry_time DATETIME NOT NULL, entry_price FLOAT NOT NULL,
    stop FLOAT, target FLOAT, score_at_entry FLOAT, last_price FLOAT, profit FLOAT, profit_pct FLOAT, status VARCHAR(16) NOT NULL,
    exit_time DATETIME, exit_price FLOAT, updated_at DATETIME, CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date))"""


def eq(rng):
    return {"signals": {k: round(rng.uniform(-1, 1), 3) for k in SIGNALS if rng.random() > 0.2}, "params": []}


def signal_rows(fn, tag, n=5, seed=1):
    rng, out = random.Random(seed), []
    while len(out) < n:
        r = fn(f"{tag}{len(out)}", eq(rng))
        if r["signal"] in ("BUY", "SELL"):
            sgn = 1 if r["signal"] == "BUY" else -1
            r.update(entry=100.0, now=100.0, sl=100.0 - sgn * 0.5, tp=100.0 + sgn * 1.0)
            out.append(r)
    return out


class Rig:
    def __init__(self, monkeypatch, tmp_path, mode="audit", with_audit_table=True):
        monkeypatch.setattr(intraday_enrich, "market_breadth_bias", lambda: 0.4)
        sv._last.clear()
        self.url = f"sqlite:///{tmp_path / 'book.db'}"
        self.e = create_engine(self.url)
        with self.e.begin() as c:
            c.execute(text(DDL))
        if with_audit_table:
            m.IntradaySignalAudit.__table__.create(bind=self.e)
        self.S = sessionmaker(bind=self.e)
        import database
        monkeypatch.setattr(database, "SessionLocal", self.S)
        monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", mode)
        monkeypatch.setattr(dts, "ensure_schema", lambda: None)
        monkeypatch.setattr(dts, "MODELS", {"grok": ("grok", "grok"), "kimi": ("kimi", "kimi")})
        monkeypatch.setattr(dts, "_et_now", lambda: NOW)
        monkeypatch.setattr(dts, "_quote_price", lambda sym: 100.0)
        try:                                              # the feature branch's extra Legacy gates (absent on the production build)
            import intraday_safety
            monkeypatch.setattr(intraday_safety, "tradable", lambda r: True)
            monkeypatch.setattr(dts, "_entry_allowed", lambda *a: True)
        except ImportError:
            pass
        self.rows = {"grok": signal_rows(grok.score_one, "G"), "kimi": signal_rows(kimi.score_one, "K", seed=2)}
        monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [x for x in self.rows[spec] if x["signal"] == "BUY"],
                                                          "sellers": [x for x in self.rows[spec] if x["signal"] == "SELL"], "neutral": []})

    def trades(self):
        with self.e.connect() as c:
            return [tuple(r) for r in c.execute(text("SELECT strategy, symbol, side, entry_price, stop, target, score_at_entry, status FROM demo_trades ORDER BY id"))]


def test_the_audit_only_starts_after_the_legacy_trades_are_committed(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    seen = {}
    real = sv.verify

    def spy(*a, **k):
        other = create_engine(rig.url)                    # a SECOND connection: it sees only COMMITTED data
        with other.connect() as c:
            seen["trades_visible_to_another_connection"] = c.execute(text("SELECT COUNT(*) FROM demo_trades")).scalar()
        other.dispose()
        return real(*a, **k)
    monkeypatch.setattr(sv, "verify", spy)
    dts.tick(force=True)
    assert len(rig.trades()) == 10 and seen["trades_visible_to_another_connection"] == 10


def test_a_database_failure_inside_the_audit_leaves_the_committed_trades_and_the_next_tick_working(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, with_audit_table=False)          # every audit write fails
    dts.tick(force=True)
    first = rig.trades()
    assert len(first) == 10
    dts.tick(force=True)                                              # the next cycle runs normally, nothing duplicated or altered
    assert rig.trades() == first


def test_audit_faults_do_not_stop_the_other_signals_from_being_audited(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    real, calls = sv.verify, {"n": 0}

    def flaky(strategy, row, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("one bad signal")
        return real(strategy, row, **k)
    monkeypatch.setattr(sv, "verify", flaky)
    for key in sv.STATS:
        sv.STATS[key] = 0
    dts.tick(force=True)
    assert len(rig.trades()) == 10 and sv.STATS["verify_errors"] == 1 and sv.STATS["verified"] == 9
    with rig.e.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM intraday_signal_audit")).scalar() == 9


def test_the_time_budget_bounds_the_audit(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "max_seconds", 0.0)
    for key in sv.STATS:
        sv.STATS[key] = 0
    t0 = time.monotonic()
    dts.tick(force=True)
    assert time.monotonic() - t0 < 5 and len(rig.trades()) == 10      # trading unaffected
    assert sv.STATS["budget_skips"] >= 1 and sv.STATS["verified"] == 0
    with rig.e.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM intraday_signal_audit")).scalar() == 0


def test_audit_pass_time_for_a_full_board_is_small(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    rig.rows = {"grok": signal_rows(grok.score_one, "G", n=40), "kimi": signal_rows(kimi.score_one, "K", n=40, seed=2)}
    t0 = time.monotonic()
    dts.tick(force=True)
    assert time.monotonic() - t0 < 5.0 and len(rig.trades()) == 80


def test_every_opened_trade_is_attributed_to_the_right_strategy_symbol_side_entry_and_score(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    dts.tick(force=True)
    with rig.e.connect() as c:
        rows = c.execute(text("SELECT strategy, symbol, direction, attribution_ok, legacy_opened, trade_id FROM intraday_signal_audit")).fetchall()
    assert len(rows) == 10 and all(r[3] == 1 and r[4] == 1 and r[5] for r in rows)
    rep = sv.report(rig.S, date(2026, 10, 27))
    assert rep["total"]["attribution_ok"] == 10 and rep["total"]["attribution_mismatch"] == 0 and rep["total"]["opened_by_legacy"] == 10


def test_attribution_mismatches_are_detected():
    t = SimpleNamespace(strategy="grok", symbol="AAPL", side="LONG", entry_price=100.0, score_at_entry=60.0)
    assert sv.attribution("grok", "AAPL", "BUY", 100.0, 60.0, t)["ok"] is True
    for kw, frag in ((dict(strategy="kimi"), "attributed to"), (dict(symbol="MSFT"), "symbol"), (dict(side="SHORT"), "side"),
                     (dict(entry_price=101.0), "entry"), (dict(score_at_entry=50.0), "score")):
        bad = SimpleNamespace(**{**t.__dict__, **kw})
        r = sv.attribution("grok", "AAPL", "BUY", 100.0, 60.0, bad)
        assert r["ok"] is False and frag in r["detail"], (kw, r)
    assert sv.attribution("grok", "AAPL", "BUY", 100.0, 60.0, None)["ok"] is None


def test_a_model_cannot_be_verified_as_another_model(monkeypatch):
    monkeypatch.setattr(intraday_enrich, "market_breadth_bias", lambda: 0.4)
    r = signal_rows(grok.score_one, "G", n=1)[0]
    assert sv.verify("grok", r, now=NOW, session="REGULAR")["verdict"] == "PASS"
    wrong = sv.verify("kimi", r, now=NOW, session="REGULAR")           # same parameter names, different weights
    assert wrong["verdict"] == "FAIL" and "each weight equals the configured weight" in {c["check"] for c in wrong["checks"] if not c["ok"]}


def test_staleness_is_reported_as_not_assessed_when_a_build_has_no_timestamps(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    dts.tick(force=True)
    v = sv.verify("grok", rig.rows["grok"][0], now=NOW, session="REGULAR")
    assert v["data"]["staleness"] == "NOT ASSESSED" and v["data_status"] != "DATA_OK"      # never reported as fresh / verified
    rep = sv.report(rig.S, date(2026, 10, 27))
    assert rep["total"]["stale_not_assessed"] == 10 and rep["total"]["data_ok"] == 0


def test_the_audit_can_use_its_own_database_role(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    broken = sessionmaker(bind=create_engine("sqlite:///" + str(tmp_path / "does_not_exist_dir" / "x.db")))
    import database
    own = rig.S
    monkeypatch.setattr(database, "SessionLocal", lambda: own())            # trading keeps its normal session
    monkeypatch.setenv("AUDIT_DATABASE_URL", rig.url)
    monkeypatch.setattr(sv, "_audit_sessionmaker", None)
    used = {"n": 0}
    real = sv.audit_sessionmaker

    def spy():
        used["n"] += 1
        return real()
    monkeypatch.setattr(sv, "audit_sessionmaker", spy)
    dts.tick(force=True)
    assert used["n"] >= 1 and len(rig.trades()) == 10
    with rig.e.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM intraday_signal_audit")).scalar() == 10
    monkeypatch.delenv("AUDIT_DATABASE_URL")
    monkeypatch.setattr(sv, "_audit_sessionmaker", None)
    assert sv.audit_sessionmaker() is None                                   # default: the application's normal session factory
