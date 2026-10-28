"""Audit-only signal verification on the PRODUCTION build (36094e8 + this change): the verifier agrees with the six real scoring
functions of THIS build, catches tampering, and can never change, block, roll back or interrupt a paper trade."""
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import chatgpt_intraday_service as chatgpt
import claude_pro_intraday_service as claude_pro
import demo_trade_service as dts
import gemini_intraday_service as gemini
import intraday_enrich
import intraday_grok_service as grok
import kimi_intraday_service as kimi
import meta_intraday_service as meta
import models_demo_trades as m
import signal_verification as sv
import tab_settings as cfg

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 27, 11, 0, tzinfo=ET)
SIGNALS = ["options_flow", "unusual_activity", "volume_pcr", "ema_trend", "price_action", "key_levels", "daily_oi_change", "flow_by_expiry",
           "implied_volatility", "expected_move", "rsi", "disparity", "event_radar", "dividend_trend", "earnings_results", "funding_activity",
           "insider_activity", "merger_activity", "oi_positioning"]
BIAS_NAMES = ["options_positioning", "unusual_options", "price_action", "disparity", "short_insider"]
REAL = {"grok": grok.score_one, "kimi": kimi.score_one, "meta": meta.score_one, "gemini": gemini.score_one,
        "chatgpt": chatgpt._score_equity, "claude_pro": claude_pro._score_equity}


def random_eq(rng):
    return {"signals": {k: round(rng.uniform(-1, 1), 3) for k in SIGNALS if rng.random() > 0.25},
            "params": [{"name": n, "bias": round(rng.uniform(-1, 1), 3)} for n in BIAS_NAMES if rng.random() > 0.3]}


def with_entry(r):
    r = dict(r)
    sgn = 1 if r["signal"] == "BUY" else -1
    r.update(entry=100.0, now=100.0, sl=100.0 - sgn * 0.5, tp=100.0 + sgn * 1.0)
    return r


@pytest.mark.parametrize("strategy", list(REAL))
def test_the_verifier_agrees_with_this_builds_real_scoring_code(strategy, monkeypatch):
    monkeypatch.setattr(intraday_enrich, "market_breadth_bias", lambda: 0.4)
    rng, seen = random.Random(11), {"BUY": 0, "SELL": 0, "NEUTRAL": 0}
    for i in range(400):
        r = REAL[strategy](f"S{i}", random_eq(rng))
        seen[r["signal"]] += 1
        v = sv.verify(strategy, with_entry(r), now=NOW, session="REGULAR")
        assert v["integrity"], (strategy, r["score"], [c for c in v["checks"] if not c["ok"]])
    assert seen["BUY"] and seen["SELL"]


def a_buy(strategy="grok"):
    rng = random.Random(3)
    while True:
        r = REAL[strategy]("X", random_eq(rng))
        if r["signal"] == "BUY":
            return with_entry(r)


def failed(v):
    return {c["check"] for c in v["checks"] if not c["ok"]}


def test_tampering_is_detected(monkeypatch):
    monkeypatch.setattr(intraday_enrich, "market_breadth_bias", lambda: 0.4)
    assert sv.verify("grok", a_buy(), now=NOW, session="REGULAR")["verdict"] == "PASS"
    r = a_buy()
    r["params"][0] = dict(r["params"][0], max=r["params"][0]["max"] + 1)
    assert "each weight equals the configured weight" in failed(sv.verify("grok", r, now=NOW, session="REGULAR"))
    r = a_buy()
    r["score"] = round(r["score"] - 4, 1)
    assert "final score = sum of the weighted contributions" in failed(sv.verify("grok", r, now=NOW, session="REGULAR"))
    r = a_buy()
    r["score"], r["signal"] = 54.9, "BUY"
    assert "signal matches BUY >= 55 / SELL <= 45" in failed(sv.verify("grok", r, now=NOW, session="REGULAR"))
    r = a_buy()
    r["entry"] = None
    assert "entry price present and inside its own stop / target bracket" in failed(sv.verify("grok", r, now=NOW, session="REGULAR"))


def test_this_build_has_no_timestamps_so_staleness_is_unjudgeable_but_missing_evidence_is_counted(monkeypatch):
    monkeypatch.setattr(intraday_enrich, "market_breadth_bias", lambda: 0.4)
    v = sv.verify("chatgpt", a_buy("chatgpt"), now=NOW, session="REGULAR")
    assert v["data_status"] in ("DATA_UNVERIFIED", "DATA_THIN") and "staleness cannot be judged" in " ".join(v["data"]["reasons"])
    assert "eligible_coverage_pct" in v["data"] and v["data"]["no_source_weight"] == 36


# ----------------------------------------------------------------------------------------- the real tick, audit on / off --
RELEASE_DDL = """CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, strategy VARCHAR(24) NOT NULL, symbol VARCHAR(16) NOT NULL,
    side VARCHAR(5) NOT NULL, shares INTEGER NOT NULL, session_date DATE NOT NULL, entry_time DATETIME NOT NULL, entry_price FLOAT NOT NULL,
    stop FLOAT, target FLOAT, score_at_entry FLOAT, last_price FLOAT, profit FLOAT, profit_pct FLOAT, status VARCHAR(16) NOT NULL,
    exit_time DATETIME, exit_price FLOAT, updated_at DATETIME, CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date))"""


class Rig:
    def __init__(self, monkeypatch, mode, with_audit_table=True):
        monkeypatch.setattr(intraday_enrich, "market_breadth_bias", lambda: 0.4)
        self.e = create_engine("sqlite://")
        with self.e.begin() as c:
            c.execute(text(RELEASE_DDL))
        if with_audit_table:
            m.IntradaySignalAudit.__table__.create(bind=self.e)
        self.S = sessionmaker(bind=self.e)
        import database
        monkeypatch.setattr(database, "SessionLocal", self.S)
        monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", mode)
        monkeypatch.setattr(dts, "ensure_schema", lambda: None)
        monkeypatch.setattr(dts, "MODELS", {"grok": ("grok", "grok"), "kimi": ("kimi", "kimi")})
        self.now = NOW
        monkeypatch.setattr(dts, "_et_now", lambda: self.now)
        monkeypatch.setattr(dts, "_quote_price", lambda sym: 100.0)
        rng = random.Random(5)
        self.rows = {"grok": [], "kimi": []}
        for st in self.rows:
            n = 0
            while n < 6:
                r = REAL[st](f"S{st[0]}{n}", random_eq(rng))
                if r["signal"] in ("BUY", "SELL"):
                    self.rows[st].append(with_entry(r))
                    n += 1
        monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [x for x in self.rows[spec] if x["signal"] == "BUY"],
                                                          "sellers": [x for x in self.rows[spec] if x["signal"] == "SELL"], "neutral": []})

    def trades(self):
        with self.e.connect() as c:
            return [tuple(r) for r in c.execute(text(
                "SELECT strategy, symbol, side, entry_price, stop, target, score_at_entry, status FROM demo_trades ORDER BY id"))]

    def audit(self):
        with self.e.connect() as c:
            return [tuple(r) for r in c.execute(text(
                "SELECT strategy, symbol, direction, verdict, data_status, legacy_opened FROM intraday_signal_audit ORDER BY id"))]


def test_audit_on_leaves_the_paper_trades_identical_and_records_every_signal(monkeypatch):
    off = Rig(monkeypatch, "off")
    dts.tick(force=True)
    base = off.trades()
    assert len(base) == 12
    on = Rig(monkeypatch, "audit")
    dts.tick(force=True)
    assert on.trades() == base                                                                   # identical to the cent
    rows = on.audit()
    assert len(rows) == 12 and all(r[3] == "PASS" and r[5] for r in rows)                        # every signal audited, all opened by the tracker


def test_a_crashing_verifier_changes_nothing(monkeypatch):
    base = Rig(monkeypatch, "off")
    dts.tick(force=True)
    expect = base.trades()
    bad = Rig(monkeypatch, "audit")
    monkeypatch.setattr(sv, "verify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    dts.tick(force=True)
    assert bad.trades() == expect and bad.audit() == []


def test_a_failing_audit_write_changes_nothing_and_does_not_interrupt_trading(monkeypatch):
    base = Rig(monkeypatch, "off")
    dts.tick(force=True)
    expect = base.trades()
    bad = Rig(monkeypatch, "audit", with_audit_table=False)                                       # the audit table does not exist: every write fails
    dts.tick(force=True)
    assert bad.trades() == expect
    bad.now = NOW.replace(minute=5)                                                               # the next tick still runs normally
    dts.tick(force=True)
    assert bad.trades() == expect and "intraday_signal_audit" not in inspect(bad.e).get_table_names()


def test_a_signal_that_fails_verification_is_still_traded_exactly_as_before(monkeypatch):
    rig = Rig(monkeypatch, "audit")
    r = rig.rows["grok"][0]
    r["params"][0] = dict(r["params"][0], max=r["params"][0]["max"] + 1)                          # tampered weight, the board still says BUY/SELL
    dts.tick(force=True)
    assert len(rig.trades()) == 12
    assert any(x[3] == "FAIL" for x in rig.audit())


def test_mode_defaults_to_off_and_only_off_and_audit_exist(monkeypatch):
    for mode, expect in (("off", False), ("audit", True), ("enforce", False), ("block", False), ("", False)):
        monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", mode)
        assert sv.enabled() is expect


def test_the_table_is_created_alone_and_only_when_audit_is_on(monkeypatch):
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text(RELEASE_DDL))
    import database
    monkeypatch.setattr(database, "engine", e)
    monkeypatch.setattr(dts, "_audit_table_done", False)
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "off")
    dts._ensure_audit_table()
    assert set(inspect(e).get_table_names()) == {"demo_trades"}
    monkeypatch.setattr(dts, "_audit_table_done", False)
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "audit")
    dts._ensure_audit_table()
    assert set(inspect(e).get_table_names()) == {"demo_trades", "intraday_signal_audit"}
