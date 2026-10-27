"""Comparison-harness checks that are about lifecycle rather than P&L: pre-market carry, separate regular-session events, the official
close (normal and early-close days), PENDING_RECON, and the regular-session opening period. Plus two regressions found by the replay."""
import json
from datetime import timedelta

import intraday_execution as ex
import intraday_workflow as wf
import market_calendar as mc
import models_demo_trades as m
import tab_settings as cfg
from wf_harness import TUE, bar, book, et, falling_bars, open_via_premarket, quote, rising_bars, row  # noqa: F401

EARLY = (2026, 11, 27)                                   # the Friday after Thanksgiving: closes 13:00 ET


def hold(book, strategy, symbol, now, price=100.0, signal="BUY", score=56.0):
    """Keep the board (and the quote) unchanged, as a calm market does."""
    book.quotes[symbol] = quote(price)
    book.set_board(strategy, row(strategy, symbol, now, signal=signal, price=price, score=score))


# ------------------------------------------------------------------------------ 5. carry and separate regular events ---
def test_premarket_position_is_still_open_when_regular_trading_begins_and_regular_signals_are_separate(book):
    now, t0 = open_via_premarket(book, et(*TUE, 7, 15))
    assert t0 and t0.entry_session == "PREMARKET"
    for hh, mm, ss in [(9, 29, 30), (9, 30, 30), (9, 45, 0)]:
        n = et(*TUE, hh, mm, ss)
        hold(book, "chatgpt", "NVDA", n)
        book.tick(n)
        tr = book.trades(strategy="chatgpt", symbol="NVDA")
        assert len(tr) == 1 and tr[0].id == t0.id and tr[0].status == "OPEN"                  # carried across the 09:30 boundary
    # the same strategy keeps showing BUY in the regular session: recorded as its own REGULAR event, no second trade
    ev = {e.session_type: e for e in book.events(symbol="NVDA")}
    assert set(ev) == {"PREMARKET", "REGULAR"} and ev["PREMARKET"].id != ev["REGULAR"].id
    assert ev["PREMARKET"].eligibility_status == "TRADED" and ev["REGULAR"].eligibility_status != "TRADED"
    assert len(book.trades(strategy="chatgpt", symbol="NVDA")) == 1
    assert json.loads(ev["REGULAR"].reject_reasons)[-1]["code"] == ex.WAIT_DUPLICATE
    assert ev["PREMARKET"].trade_id == t0.id and ev["REGULAR"].trade_id is None


# ------------------------------------------------------------------------------------------ 7. the official close ---
def _open_two(book, day, t_open):
    n0 = et(*day, *t_open)
    now, t = open_via_premarket(book, n0, symbol="NVDA")
    return now, t


def test_everything_is_closed_or_non_tradable_at_the_official_close(book):
    now, t = _open_two(book, TUE, (7, 15))
    for hh, mm, ss in [(15, 59, 0), (15, 59, 50)]:
        n = et(*TUE, hh, mm, ss)
        hold(book, "chatgpt", "NVDA", n)
        book.tick(n)
        assert book.trades()[0].status == "OPEN"                                              # before the bell nothing is closed
    n = et(*TUE, 16, 0, 5)
    hold(book, "chatgpt", "NVDA", n, price=100.4)
    book.tick(n)
    t = book.trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_price == 100.4 and t.profit is not None and "official close" in t.fill_assumption
    assert not [x for x in book.trades() if x.status == "OPEN"]


def test_early_close_day_closes_at_1300_and_never_at_1600(book):
    assert mc.close_datetime(et(*EARLY).date()).hour == 13
    now, t = open_via_premarket(book, et(*EARLY, 7, 15), symbol="NVDA")
    n = et(*EARLY, 12, 59, 30)
    hold(book, "chatgpt", "NVDA", n)
    book.tick(n)
    assert book.trades()[0].status == "OPEN"
    n = et(*EARLY, 13, 0, 5)
    hold(book, "chatgpt", "NVDA", n, price=100.2)
    book.tick(n)
    t = book.trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_price == 100.2
    for k in range(3):                                                                         # no new entry after the early bell
        n = et(*EARLY, 13, 10) + timedelta(minutes=3 * k)
        hold(book, "chatgpt", "AMD", n, price=50.0)
        book.tick(n)
    assert len(book.trades()) == 1 and not book.events(symbol="AMD")


def test_no_price_at_all_at_the_close_goes_to_pending_recon_with_an_exception_and_no_invented_price(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    book.quotes.clear()
    book.boards.clear()                                                                        # the data feed is gone
    for n in (et(*TUE, 16, 1), et(*TUE, 16, 7)):                                               # inside, then past, the 5-minute grace
        book.tick(n)
    t = book.trades()[0]
    assert t.status == "PENDING_RECON" and t.exit_price is None and t.profit is None
    ex_rows = book.exceptions()
    assert len(ex_rows) == 1 and ex_rows[0].kind == "CLOSE_PRICE_MISSING" and ex_rows[0].resolved_at is None


# ---------------------------------------------------------------------------------------------- 8. PENDING_RECON ---
def _pending(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    book.quotes.clear()
    book.boards.clear()
    book.tick(et(*TUE, 16, 7))
    assert book.trades()[0].status == "PENDING_RECON"
    return t


def test_pending_recon_is_not_exposure_is_never_managed_and_is_not_carried_into_the_next_session(book):
    t = _pending(book)
    with book.Session() as s:
        assert wf._open_positions(s) == []                                                     # no exposure for the risk layer
    # next session, a price that would trigger a stop and a fresh signal on the same name
    n = et(2026, 10, 28, 9, 45)
    book.quotes["NVDA"] = quote(10.0)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=10.0, score=10.0))
    book.tick(n)
    p = book.trades()[0]
    assert p.status == "PENDING_RECON" and p.exit_price is None and p.profit is None            # untouched: no stop, no score exit, no P&L
    assert not [x for x in book.trades() if x.status == "OPEN"]


def test_pending_recon_is_reconciled_at_its_own_dates_close_never_todays_price(book):
    t = _pending(book)
    book.final_close[("NVDA", et(*TUE).date())] = 101.5
    wf._last_recon_try.clear()
    n = et(2026, 10, 28, 9, 45)
    book.quotes["NVDA"] = quote(55.0)                                                           # today's price is irrelevant
    book.tick(n)
    p = book.trades()[0]
    assert p.status == "MARKET_CLOSE" and p.exit_price == 101.5 and p.exit_price_source == "regular_close_candle"
    assert book.exceptions()[0].resolved_at is not None


def test_pending_recon_never_blocks_nor_is_counted_by_a_new_legitimate_entry(book):
    t = _pending(book)
    base = et(2026, 10, 28, 7, 15)
    book.quotes["NVDA"] = quote(100.0)
    for k in range(2):
        n = base + timedelta(minutes=3 * k)
        book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="BUY", price=100.0, score=56.0))
        book.tick(n)
    st = sorted(x.status for x in book.trades())
    assert st == ["OPEN", "PENDING_RECON"]                                                      # a new, separate position; the old one stays inert


# ----------------------------------------------------------------------- 6. the regular-session opening period -----
def opening_probe(prior_bars=True, minutes=range(1, 126)):
    """For a clean up-trend and a BUY, what do the candle readings say minute by minute after the open?"""
    d = et(*TUE).date()
    rows = []
    prior = rising_bars(et(2026, 10, 26, 16, 0), 24) if prior_bars else []
    px0 = prior[-1]["close"] if prior else 100.0
    today5 = [bar(et(*TUE, 9, 30) + timedelta(minutes=5 * i), px0 + 0.05 * i, px0 + 0.05 * i + 0.06, px0 + 0.05 * i - 0.01, px0 + 0.05 * (i + 1)) for i in range(40)]
    today15 = [bar(et(*TUE, 9, 30) + timedelta(minutes=15 * i), px0 + 0.15 * i, px0 + 0.15 * i + 0.18, px0 + 0.15 * i - 0.01, px0 + 0.15 * (i + 1)) for i in range(14)]
    prior15 = [bar(et(2026, 10, 26, 10, 0) + timedelta(minutes=15 * i), 100 + 0.05 * i, 100.1 + 0.05 * i, 99.99 + 0.05 * i, 100.05 + 0.05 * i) for i in range(24)] if prior_bars else []
    for k in minutes:
        now = et(*TUE, 9, 30) + timedelta(minutes=k)
        r5 = ex.candle_reading(prior + today5, 300, now, d)
        r15 = ex.candle_reading(prior15 + today15, 900, now, d)
        c = ex.confirm_candles("BUY", ex.REGULAR, r5, r15, now)
        rows.append({"t": now.strftime("%H:%M"), "five": r5["status"], "fifteen": r15["status"], "ok": c["ok"]})
    return rows


def test_opening_period_exact_behaviour():
    rows = {r["t"]: r for r in opening_probe(True)}
    assert rows["09:34"]["five"] == "WARMUP" and not rows["09:34"]["ok"]                       # first 5m candle still forming / one closed
    assert rows["09:40"]["five"] == "OK" and rows["09:40"]["fifteen"] == "WARMUP" and rows["09:40"]["ok"]   # 5m alone decides
    assert rows["09:59"]["fifteen"] == "WARMUP" and rows["09:59"]["ok"]
    assert rows["10:00"]["fifteen"] == "OK" and rows["10:00"]["ok"]                              # 15m takes over once supported
    no_prior = {r["t"]: r for r in opening_probe(False)}
    assert no_prior["09:40"]["five"] == "INVALID" and not no_prior["09:40"]["ok"]               # no warm-up history -> refused, never guessed
    assert no_prior["11:15"]["five"] == "INVALID" and no_prior["11:20"]["five"] == "OK"         # 22 closed candles today are needed on their own


# ---------------------------------------------------------------- regressions found by the replay harness ---------
def test_strategies_without_a_live_engine_are_judged_on_their_equity_signal_registry_in_the_regular_session(book):
    for strat in ("grok", "kimi", "meta", "gemini"):
        assert ex.registry_for(strat, ex.REGULAR) is ex.REGISTRY[strat]["equity"]
        r = row(strat, "NVDA", et(*TUE, 11, 0), signal="BUY", session="REGULAR", score=60.0)
        dv = ex.evaluate_data(strat, "BUY", "REGULAR", r, now=et(*TUE, 11, 0), bounds=mc.session_bounds(et(*TUE).date()),
                              prev_close=wf.last_close_before(et(*TUE, 11, 0)), engine_ok=True)
        assert dv["coverage"]["eligible_pct"] == 100.0, strat                                   # not "parameter not in the registry"


def test_the_live_engines_own_rebuild_counts_as_new_evidence_but_an_unchanged_one_does_not(book):
    n0 = et(*TUE, 11, 0)
    fps = []
    for k, age in enumerate([30.0, 30.0 + 180.0, 30.0 + 360.0, 30.0]):                          # engine age grows while NOT rebuilt, then is rebuilt
        n = n0 + timedelta(minutes=3 * k)
        r = row("claude_pro", "NVDA", n, signal="BUY", session="REGULAR", price=100.0, score=56.0, engine_age=age)
        meta = wf._age_adjusted_meta(r, n)
        meta["@engine"] = {"age_s": age}
        dv = ex.evaluate_data("claude_pro", "BUY", "REGULAR", dict(r, signal_meta=meta), now=n, bounds=mc.session_bounds(n.date()),
                              prev_close=wf.last_close_before(n), engine_ok=True)
        fps.append(ex.fingerprint(dv["classified"], meta, n))
    assert fps[0] == fps[1] == fps[2] and fps[3] != fps[0]
