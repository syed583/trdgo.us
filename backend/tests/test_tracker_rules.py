"""The pure-intraday paper tracker: entry window, fresh-quote entries, $10k sizing, breakeven trail, the 15:50 ET close,
observed (gap-aware) fills, prior-day cleanup, retired models never trading, and a whole simulated trading day.
Everything is mocked: no provider call, no production database (SQLite built here)."""
import json
import time
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import demo_trade_service as dts
import paper_fills as pf
import tab_settings as cfg
from models_demo_trades import DemoTrade

ET = ZoneInfo("America/New_York")
DAY = (2026, 11, 5)                       # a Thursday, regular 16:00 close
EARLY = (2026, 11, 27)                    # the day after Thanksgiving: 13:00 close


def et(hh, mm=0, d=DAY):
    return datetime(*d, hh, mm, tzinfo=ET)


# ------------------------------------------------------------------------------------------------------ clock rules ---
@pytest.mark.parametrize("now, expect", [
    (et(9, 59), False), (et(10, 0), True), (et(12, 30), True), (et(14, 59), True), (et(15, 0), False),
    (et(16, 30), False), (et(4, 30), False), (et(8, 0), False),
    (datetime(2026, 11, 7, 11, 0, tzinfo=ET), False),        # Saturday
    (datetime(2026, 11, 26, 11, 0, tzinfo=ET), False),       # Thanksgiving
    (et(10, 30, EARLY), True), (et(12, 0, EARLY), False),    # early close 13:00 -> entries end at 12:00
])
def test_entries_open_only_10_to_15_et_on_a_trading_day(now, expect):
    assert dts._can_open(now) is expect


def test_the_eod_cutoff_is_ten_minutes_before_the_official_close():
    assert dts.eod_cutoff(date(*DAY)) == et(15, 50)
    assert dts.eod_cutoff(date(*EARLY)) == et(12, 50, EARLY)
    assert dts.eod_cutoff(date(2026, 11, 7)) is None
    t = types.SimpleNamespace(session_date=date(*DAY))
    assert dts._eod_due(t, et(15, 49)) is False and dts._eod_due(t, et(15, 50)) is True
    assert dts._eod_due(types.SimpleNamespace(session_date=date(2026, 11, 4)), et(10, 0)) is True   # a leftover is always due


# ---------------------------------------------------------------------------------------------------------- sizing ---
@pytest.mark.parametrize("price, shares", [(100.0, 100), (33.33, 300), (1268.43, 7), (10001.0, 0), (0.0, 0)])
def test_ten_thousand_dollar_notional_floored_to_whole_shares(price, shares):
    assert dts.shares_for(price) == shares


def test_levels_are_rebased_on_the_real_entry_price():
    sl, tp = dts.rebase_levels("LONG", 100.0, 99.0, 102.0, 100.5)
    assert (sl, tp) == (99.5, 102.5)
    sl, tp = dts.rebase_levels("SHORT", 100.0, 101.0, 98.0, 99.0)
    assert (sl, tp) == (100.0, 97.0)
    assert dts.rebase_levels("LONG", None, None, None, 50.0) == (49.5, 51.0)      # the 1% / 2% default


# ------------------------------------------------------------------------------------------------- breakeven trail ---
def test_breakeven_trail_moves_the_stop_to_the_entry_at_half_the_target_distance():
    assert dts.breakeven_stop("LONG", 100.0, 99.0, 102.0, 100.99) is None       # 0.99 of 2.0: not yet
    assert dts.breakeven_stop("LONG", 100.0, 99.0, 102.0, 101.0) == 100.0        # exactly 50 %
    assert dts.breakeven_stop("LONG", 100.0, 100.0, 102.0, 101.5) is None        # already at breakeven
    assert dts.breakeven_stop("SHORT", 100.0, 101.0, 98.0, 99.0) == 100.0
    assert dts.breakeven_stop("SHORT", 100.0, 101.0, 98.0, 99.5) is None
    assert dts.breakeven_stop("LONG", 100.0, 99.0, None, 150.0) is None          # no target: no trail


# ------------------------------------------------------------------------------------------------------------ fills ---
def test_stops_and_targets_are_booked_at_the_observed_crossing_price():
    assert pf.fill_price("LONG", "STOP", 99.0, 102.0, 97.4, gap=False, model="observed") == 97.4
    assert pf.fill_price("LONG", "TARGET", 99.0, 102.0, 102.6, gap=False, model="observed") == 102.6
    assert pf.fill_price("LONG", "STOP", 99.0, 102.0, 97.4, gap=False, model="level") == 99.0
    assert cfg.PAPER_EXECUTION["fill_model"] == "observed"
    assert pf.gap_effect("LONG", "STOP", 99.0, 102.0, 97.4, 100) == -160.0


# ------------------------------------------------------------------------------------------------ the fresh quote ---
def test_a_stale_or_untimed_quote_blocks_an_entry(monkeypatch):
    import swr
    swr._values.pop("demo:q:AAA", None)
    monkeypatch.setattr(dts, "_quote", lambda sym: {"price": 100.0, "quote_age_s": 61.0, "fetched_at": time.time()})
    q, why = dts._fresh_quote("AAA")
    assert q is None and "old" in why
    monkeypatch.setattr(dts, "_quote", lambda sym: {"price": 100.0, "quote_age_s": None, "fetched_at": time.time()})
    assert dts._fresh_quote("AAA")[0] is None
    monkeypatch.setattr(dts, "_quote", lambda sym: {"price": 100.0, "quote_age_s": 5.0, "fetched_at": time.time() - 70})
    assert dts._fresh_quote("AAA")[0] is None                                    # fresh when fetched, held too long since
    monkeypatch.setattr(dts, "_quote", lambda sym: {"price": 100.0, "quote_age_s": 5.0, "fetched_at": time.time()})
    assert dts._fresh_quote("AAA")[0]["price"] == 100.0


# ----------------------------------------------------------------------------------------- a simulated trading day ---
class Sim:
    """A paper book on SQLite, mocked boards and quotes, a clock the test moves."""

    def __init__(self, monkeypatch):
        engine = create_engine("sqlite://")
        DemoTrade.__table__.create(bind=engine)
        self.Session = sessionmaker(bind=engine)
        import database
        monkeypatch.setattr(database, "SessionLocal", self.Session)
        monkeypatch.setattr(dts, "ensure_schema", lambda: None)
        monkeypatch.setattr(dts, "MODELS", {"grok": ("Grok Intraday", "x:y"), "kimi": ("Kimi Intraday", "x:z")})
        monkeypatch.setattr(dts, "_last_tick", 0.0)
        monkeypatch.setattr(dts, "_entry_allowed", lambda sym, sig, et: True)      # the 15-minute gate is proven elsewhere
        import intraday_safety
        monkeypatch.setattr(intraday_safety, "tradable", lambda r: r.get("signal") in ("BUY", "SELL") and not r.get("wait"))
        self.quotes: dict = {}
        self.now = et(9, 0)
        monkeypatch.setattr(dts, "_et_now", lambda: self.now)
        monkeypatch.setattr(dts, "_quote", lambda sym: dict(self.quotes[sym], fetched_at=time.time()) if sym in self.quotes else None)
        self.boards: dict = {"grok": [], "kimi": []}
        monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [r for r in self.boards["grok" if spec == "x:y" else "kimi"] if r.get("signal") == "BUY"],
                                                           "sellers": [r for r in self.boards["grok" if spec == "x:y" else "kimi"] if r.get("signal") == "SELL"],
                                                           "neutral": [r for r in self.boards["grok" if spec == "x:y" else "kimi"] if r.get("signal") not in ("BUY", "SELL")]})
        self.m = monkeypatch

    def quote(self, sym, price, bid=None, ask=None, age=3.0):
        bid = price - 0.02 if bid is None else bid
        ask = price + 0.02 if ask is None else ask
        self.quotes[sym] = {"price": price, "bid": bid, "ask": ask, "spread": round(ask - bid, 4),
                            "spread_pct": round((ask - bid) / ((ask + bid) / 2) * 100, 4), "quote_age_s": age, "quote_epoch": 1.0}

    def signal(self, strat, sym, sig, price, score=70.0, sl=None, tp=None):
        sl = sl if sl is not None else (price - 1.0 if sig == "BUY" else price + 1.0)
        tp = tp if tp is not None else (price + 2.0 if sig == "BUY" else price - 2.0)
        self.boards[strat] = [r for r in self.boards[strat] if r["symbol"] != sym] + [
            {"symbol": sym, "signal": sig, "score": score, "entry": price, "now": price, "sl": sl, "tp": tp,
             "coverage": 92.5, "params": [{"name": "options_flow", "bias": 1.0, "points": 34.0}],
             "data": {"ok": True, "price_age_s": 4, "signals_age_s": 30, "phase": "rth"}}]

    def price(self, strat, sym, price, score=None):
        for r in self.boards[strat]:
            if r["symbol"] == sym:
                r["now"] = price
                if score is not None:
                    r["score"] = score
        if sym in self.quotes:
            self.quotes[sym]["price"] = price

    def tick(self, hh, mm=0, d=DAY):
        self.now = et(hh, mm, d)
        dts.tick(force=True)

    def rows(self):
        def _utc(v):                                      # SQLite hands back naive datetimes: they were stored as UTC
            return v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v
        with self.Session() as s:
            return [types.SimpleNamespace(**{c.name: _utc(getattr(t, c.name)) for c in DemoTrade.__table__.columns})
                    for t in s.query(DemoTrade).order_by(DemoTrade.id).all()]

    def put_open(self, sym="OLD", strategy="grok", opened=None, entry=100.0, stop=99.0, target=102.0, shares=100):
        opened = opened or datetime(2026, 11, 4, 14, 0, tzinfo=ET)
        with self.Session() as s:
            t = DemoTrade(strategy=strategy, symbol=sym, side="LONG", shares=shares, session_date=opened.date(),
                          entry_time=opened.astimezone(timezone.utc), entry_price=entry, stop=stop, target=target,
                          score_at_entry=70.0, last_price=entry, status="OPEN")
            s.add(t)
            s.commit()
            return t.id


@pytest.fixture
def sim(monkeypatch):
    return Sim(monkeypatch)


def test_no_entry_before_ten_or_after_three_and_entries_inside_the_window(sim):
    sim.quote("AAA", 100.0)
    sim.signal("grok", "AAA", "BUY", 100.0)
    sim.tick(9, 45)
    assert sim.rows() == []                                   # pre-10:00: nothing opens
    sim.tick(10, 0)
    rows = sim.rows()
    assert len(rows) == 1 and rows[0].status == "OPEN" and rows[0].entry_time.astimezone(ET).hour == 10
    sim.quote("BBB", 50.0)
    sim.signal("kimi", "BBB", "SELL", 50.0)
    sim.tick(15, 0)
    assert [r.symbol for r in sim.rows()] == ["AAA"]           # 15:00: the window is shut


def test_entry_is_priced_at_the_fresh_quote_sized_to_ten_thousand_with_evidence(sim):
    sim.quote("AAA", 101.25, bid=101.20, ask=101.30, age=2.0)
    sim.signal("grok", "AAA", "BUY", 100.0, sl=99.0, tp=102.0, score=71.5)
    sim.tick(10, 30)
    r = sim.rows()[0]
    assert r.entry_price == 101.25 and r.shares == 98                          # floor(10000 / 101.25)
    assert (r.stop, r.target) == (100.25, 103.25)                              # the board's 1.0 / 2.0 distances, re-based
    assert r.entry_bid == 101.20 and r.entry_ask == 101.30 and r.entry_quote_age_s == pytest.approx(2.0, abs=0.5)
    ev = json.loads(r.entry_evidence)
    assert ev["coverage_pct"] == 92.5 and ev["score"] == 71.5 and ev["params"][0]["name"] == "options_flow"
    assert ev["quote"]["bid"] == 101.20 and ev["sizing"]["shares"] == 98 and ev["sizing"]["notional_usd"] == 10000.0
    assert r.be_moved is False


def test_a_stale_quote_blocks_the_entry_but_a_fresh_one_later_opens_it(sim):
    sim.quote("AAA", 100.0, age=90.0)
    sim.signal("grok", "AAA", "BUY", 100.0)
    sim.tick(10, 30)
    assert sim.rows() == []
    sim.quote("AAA", 100.0, age=1.0)
    sim.tick(10, 31)
    assert len(sim.rows()) == 1


def test_retired_models_never_open_a_trade(sim, monkeypatch):
    assert "chatgpt" not in dts.MODELS and "claude_pro" not in dts.MODELS
    assert dts.strategy_label("chatgpt") == "ChatGPT Intraday (retired)"
    assert dts.strategy_label("grok") == "Grok Intraday"


def test_breakeven_trail_then_stop_at_entry_books_the_observed_price(sim):
    sim.quote("AAA", 100.0)
    sim.signal("grok", "AAA", "BUY", 100.0, sl=99.0, tp=102.0)
    sim.tick(10, 30)
    sim.price("grok", "AAA", 101.0)                           # half-way to the target
    sim.tick(10, 35)
    r = sim.rows()[0]
    assert r.status == "OPEN" and r.stop == 100.0 and r.be_moved is True
    sim.price("grok", "AAA", 99.97)                           # back through the breakeven stop
    sim.tick(10, 40)
    r = sim.rows()[0]
    assert r.status == "STOP" and r.exit_price == 99.97 and r.profit == pytest.approx(-3.0, abs=0.01)


def test_a_gap_through_the_stop_costs_what_it_cost(sim):
    sim.quote("AAA", 100.0)
    sim.signal("grok", "AAA", "BUY", 100.0, sl=99.0, tp=102.0)
    sim.tick(10, 30)
    sim.price("grok", "AAA", 96.5)                            # gapped well past 99.0
    sim.tick(10, 45)
    r = sim.rows()[0]
    assert r.status == "STOP" and r.exit_price == 96.5 and r.profit == -350.0 and r.shares == 100


def test_everything_open_is_closed_at_15_50_and_nothing_opens_after(sim):
    sim.quote("AAA", 100.0)
    sim.quote("BBB", 40.0)
    sim.signal("grok", "AAA", "BUY", 100.0)
    sim.signal("kimi", "BBB", "SELL", 40.0)
    sim.tick(11, 0)
    assert sorted(r.symbol for r in sim.rows()) == ["AAA", "BBB"]
    sim.price("grok", "AAA", 100.8)
    sim.price("kimi", "BBB", 39.8)
    sim.tick(15, 49)
    assert all(r.status == "OPEN" for r in sim.rows())
    sim.tick(15, 50)
    rows = sim.rows()
    assert all(r.status == "EOD_CLOSE" for r in rows)
    a = next(r for r in rows if r.symbol == "AAA")
    assert a.exit_price == 100.8 and a.profit == pytest.approx(80.0) and a.exit_time.astimezone(ET).time().strftime("%H:%M") == "15:50"
    b = next(r for r in rows if r.symbol == "BBB")
    assert b.shares == 250 and b.profit == pytest.approx(50.0)


def test_early_close_day_closes_at_12_50(sim):
    sim.quote("AAA", 100.0)
    sim.signal("grok", "AAA", "BUY", 100.0)
    sim.tick(10, 30, EARLY)
    sim.tick(12, 49, EARLY)
    assert sim.rows()[0].status == "OPEN"
    sim.tick(12, 50, EARLY)
    assert sim.rows()[0].status == "EOD_CLOSE"


def test_prior_day_leftovers_are_closed_as_cleanup_at_the_current_price(sim):
    tid = sim.put_open("OLD", entry=100.0)
    sim.quote("OLD", 97.3)
    sim.tick(10, 5)
    r = next(x for x in sim.rows() if x.id == tid)
    assert r.status == "CLEANUP" and r.exit_price == 97.3 and r.profit == -270.0
    assert r.exit_time.astimezone(ET).date() == date(*DAY)


def test_a_leftover_with_no_price_is_not_invented(sim):
    tid = sim.put_open("OLD")
    sim.tick(10, 5)                                           # no quote at all for OLD
    r = next(x for x in sim.rows() if x.id == tid)
    assert r.status == "OPEN" and r.exit_price is None


def test_a_full_simulated_day(sim):
    """09:30 signals ignored, 10:00 entries, trail, a target, the 15:50 sweep, no entry after 15:00."""
    for sym, px in (("AAA", 100.0), ("BBB", 200.0), ("CCC", 25.0)):
        sim.quote(sym, px)
    sim.signal("grok", "AAA", "BUY", 100.0, sl=99.0, tp=102.0)
    sim.signal("grok", "BBB", "SELL", 200.0, sl=202.0, tp=196.0)
    sim.tick(9, 35)
    assert sim.rows() == []
    sim.tick(10, 1)
    assert sorted((r.symbol, r.shares) for r in sim.rows()) == [("AAA", 100), ("BBB", 50)]
    sim.price("grok", "BBB", 195.5)                           # target crossed (observed 195.5, beyond the 196 level)
    sim.price("grok", "AAA", 101.2)                           # breakeven reached
    sim.tick(11, 0)
    rows = {r.symbol: r for r in sim.rows()}
    assert rows["BBB"].status == "TARGET" and rows["BBB"].exit_price == 195.5 and rows["BBB"].profit == 225.0
    assert rows["AAA"].status == "OPEN" and rows["AAA"].stop == 100.0 and rows["AAA"].be_moved
    sim.signal("kimi", "CCC", "BUY", 25.0)
    sim.tick(15, 10)                                          # too late: CCC never opens
    assert "CCC" not in {r.symbol for r in sim.rows()}
    sim.price("grok", "AAA", 101.0)
    sim.tick(15, 50)
    rows = {r.symbol: r for r in sim.rows()}
    assert rows["AAA"].status == "EOD_CLOSE" and rows["AAA"].exit_price == 101.0 and rows["AAA"].profit == 100.0
    sim.tick(16, 30)
    assert all(r.status != "OPEN" for r in sim.rows())
    for r in sim.rows():
        assert r.entry_time.astimezone(ET).time() >= datetime.min.time().replace(hour=10)
        assert r.exit_time.astimezone(ET).time() <= datetime.min.time().replace(hour=15, minute=50)


def test_the_blotter_reports_sizing_quote_fields_and_rules(sim):
    sim.quote("AAA", 100.0, bid=99.98, ask=100.02)
    sim.signal("grok", "AAA", "BUY", 100.0)
    sim.tick(10, 30)
    sim.m.setattr(dts, "tick", lambda *a, **k: None)
    b = dts.blotter("all", str(date(*DAY)))
    r = b["rows"][0]
    assert b["status"] == "OK" and r["notional"] == 10000.0 and r["shares"] == 100
    assert r["exit_reason_label"] == "Open" and r["bid"] == 99.98 and r["ask"] == 100.02 and r["spread_pct"] == pytest.approx(0.04, abs=0.001)
    assert {s["key"] for s in b["strategies"]} == set(dts.MODELS) | set(dts.RETIRED)
    assert {s["label"] for s in b["strategies"]} >= {"ChatGPT Intraday (retired)", "Claude Pro Intraday (retired)"}
    assert b["rules"]["entry_window_et"] == ["10:00", "15:00"] and b["rules"]["eod_close_et"] == "15:50"
    assert b["total_pct"] == 0.0


def test_eod_close_happens_before_any_board_is_rebuilt(sim):
    """2026-11-09/10: the 15:50 tick reached the exits at 16:01 because the four board rebuilds ran first. At the cutoff
    the due positions are priced from a direct quote and closed before a single board is built."""
    sim.quote("AAA", 100.0)
    sim.signal("grok", "AAA", "BUY", 100.0)
    sim.tick(11, 0)
    assert sim.rows()[0].status == "OPEN"
    sim.quote("AAA", 100.5)
    real_board = dts._board
    seen = {}

    def slow_board(spec):
        with sim.Session() as s:
            seen.setdefault("open_when_boards_built", s.query(DemoTrade).filter_by(status="OPEN").count())
        return real_board(spec)
    sim.m.setattr(dts, "_board", slow_board)
    sim.tick(15, 50)
    r = sim.rows()[0]
    assert r.status == "EOD_CLOSE" and r.exit_price == 100.5 and r.exit_time.astimezone(ET).time().strftime("%H:%M") == "15:50"
    assert seen["open_when_boards_built"] == 0            # already closed when the boards were first touched


def test_eod_fast_close_keeps_stop_and_target_precedence_and_skips_unquoted(sim):
    sim.quote("AAA", 100.0)
    sim.quote("BBB", 50.0)
    sim.signal("grok", "AAA", "BUY", 100.0, sl=99.0, tp=102.0)
    sim.signal("kimi", "BBB", "SELL", 50.0)
    sim.tick(11, 0)
    sim.quote("AAA", 103.0)                                 # through the target at the cutoff
    del sim.quotes["BBB"]                                   # no quote at all: left for the normal path
    sim.price("kimi", "BBB", 49.5)                          # ...which prices it from the board
    sim.tick(15, 50)
    rows = {r.symbol: r for r in sim.rows()}
    assert rows["AAA"].status == "TARGET" and rows["AAA"].exit_price == 103.0     # observed fill, as everywhere else
    assert rows["BBB"].status == "EOD_CLOSE" and rows["BBB"].exit_price == 49.5


def test_tracker_loop_wakes_exactly_at_the_cutoff(sim):
    sim.now = et(15, 49, DAY).replace(second=30)
    assert 30.0 <= dts.sleep_seconds() <= 32.0
    sim.now = et(12, 0, DAY)
    assert dts.sleep_seconds() == 60.0
    sim.now = et(15, 50, DAY).replace(second=5)
    assert dts.sleep_seconds() == 60.0
