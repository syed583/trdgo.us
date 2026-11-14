"""Kit v6.7 (owner decision 2026-11-13): calendar session resolution (Finnhub hour -> Unusual Whales report time -> vendor clock ->
the symbol's own earlier calendar rows), single-vendor dates scanned and labelled, SESSION_UNKNOWN shown and never skipped.
Calendar ingestion only; the frozen engine is untouched (its own answers are kept verbatim as engine_no_trade_reasons). Synthetic
rows only; no provider, no network."""
import hashlib
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_calendar_session as CS  # noqa: E402
import smart_move_event_records as ER  # noqa: E402
import smart_move_universe as U  # noqa: E402
from smvshadow import calendar_confirm as cc  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
TODAY = date(2026, 11, 2)


def row(source, sym, d, label=None, clock=None, flag=None, at=T0 - timedelta(minutes=30), sha=None):
    return {"source": source, "symbol": sym, "date": d, "label": label, "clock": clock, "vendor_flag": flag, "raw_sha256": sha or hashlib.sha256((source + sym + d + str(label)).encode()).hexdigest(),
            "observed_at": at.isoformat(), "request_id": "req", "mode": "LIVE", "company": sym, "sector": "Technology", "marketcap": "50000000000"}


def calobs(rows):
    return [o for o, _ in sum(ER.calobs_from_rows(rows).values(), [])]


def ev_of(sym, rows, history_rows=()):
    hist = CS.history_index(list(rows) + list(history_rows))
    return CS.evaluate_event(sym, calobs([r for r in rows if r["symbol"] == sym]), T0, TODAY, hist)


# ------------------------------------------------------------------ 1. the engine's own verdicts are kept
def test_two_vendor_agreement_still_two_vendor_and_engine_untouched():
    rows = [row("UW_CALENDAR", "AAA", "2026-11-04", "premarket", flag="company"), row("FINNHUB", "AAA", "2026-11-04", "bmo")]
    ev = ev_of("AAA", rows)
    raw = cc.evaluate("AAA", calobs(rows), T0, TODAY)
    assert raw["status"] == "VENDOR_CONFIRMED" and ev["status"] == "VENDOR_CONFIRMED" and ev["eligible_for_shadow"] is True
    assert ev["confirmation"] == "two-vendor" and ev["session_source"] == "finnhub-hour" and ev["engine_no_trade_reasons"] == []


def test_vendors_that_disagree_on_the_session_stay_a_conflict_and_history_never_overrides():
    rows = [row("UW_CALENDAR", "CCC", "2026-11-04", "premarket", flag="company"), row("FINNHUB", "CCC", "2026-11-04", "amc")]
    hist = [row("UW_CALENDAR", "CCC", "2026-08-04", "premarket", at=T0 - timedelta(days=90))]
    ev = ev_of("CCC", rows, hist)
    assert ev["eligible_for_shadow"] is False and "TIMING_CONTRADICTED" in ev["no_trade_reasons"] and ev["confirmation"] == "not-eligible" and ev["session_unknown"] is False


def test_date_conflict_stale_and_moved_still_refuse():
    conflict = [row("UW_CALENDAR", "DDD", "2026-11-04", "premarket", flag="company"), row("FINNHUB", "DDD", "2026-11-09", "bmo")]
    ev = ev_of("DDD", conflict)
    assert ev["eligible_for_shadow"] is False and ev["event_date"] is None and ev["confirmation"] == "not-eligible"
    stale = [row("UW_CALENDAR", "EEE", "2026-11-04", "premarket", flag="company", at=T0 - timedelta(hours=9))]
    ev = ev_of("EEE", stale)
    assert ev["status"] == "STALE" and ev["eligible_for_shadow"] is False and ev["confirmation"] == "not-eligible"
    one_day = [row("UW_CALENDAR", "FFF", "2026-11-04", "premarket", flag="company"), row("FINNHUB", "FFF", "2026-11-05", "bmo")]
    ev = ev_of("FFF", one_day)
    assert ev["eligible_for_shadow"] is False and "DATE_DISAGREEMENT_1_DAY" in ev["reasons"]


# ------------------------------------------------------------------ 2. single-vendor dates are scanned and labelled
def test_single_uw_vendor_with_report_time_is_eligible_and_labelled_single_vendor():
    rows = [row("UW_CALENDAR", "GGG", "2026-11-04", "afterhours", flag="company")]
    ev = ev_of("GGG", rows)
    assert ev["eligible_for_shadow"] is True and ev["kind"] == "AMC" and ev["confirmation"] == "single-vendor" and ev["session_source"] == "uw-report-time"
    assert ev["no_trade_reasons"] == [] and "SINGLE_VENDOR" in ev["engine_no_trade_reasons"] and "SINGLE_VENDOR_ACCEPTED_KIT_V6_7" in ev["reasons"]
    assert ev["status"] == "ESTIMATED"                                                      # the engine's own status is preserved, only the kit's acceptance differs


def test_single_finnhub_vendor_is_accepted_but_a_vendor_estimated_date_is_not():
    ev = ev_of("HHH", [row("FINNHUB", "HHH", "2026-11-04", "bmo")])
    assert ev["eligible_for_shadow"] is True and ev["confirmation"] == "single-vendor" and ev["session_source"] == "finnhub-hour"
    est = [row("UW_CALENDAR", "III", "2026-11-04", "premarket", flag="estimation"), row("FINNHUB", "III", "2026-11-04", "bmo")]
    ev = ev_of("III", est)                                                                 # a date the vendor itself marks as ITS OWN ESTIMATE is still refused (unchanged from kit v6.6)
    assert ev["eligible_for_shadow"] is False and ev["confirmation"] == "not-eligible" and "SOURCE_ESTIMATION" in ev["no_trade_reasons"]


def test_two_vendor_date_with_the_session_from_one_vendor_is_accepted():
    rows = [row("UW_CALENDAR", "JJJ", "2026-11-04", "premarket", flag="company"), row("FINNHUB", "JJJ", "2026-11-04", None)]      # Finnhub has the date, no hour
    ev = ev_of("JJJ", rows)
    assert ev["eligible_for_shadow"] is True and ev["kind"] == "BMO" and ev["confirmation"] == "two-vendor-date" and ev["session_source"] == "uw-report-time"


def test_finnhub_hour_wins_over_uw_when_both_known():
    rows = [row("UW_CALENDAR", "KKK", "2026-11-04", "afterhours", flag="company"), row("FINNHUB", "KKK", "2026-11-04", "amc")]
    assert ev_of("KKK", rows)["session_source"] == "finnhub-hour"
    clock = [row("FINVIZ", "LLL", "2026-11-04", clock="4:30 PM")]
    ev = ev_of("LLL", clock)
    assert ev["kind"] == "AMC" and ev["session_source"] == "vendor-clock" and ev["eligible_for_shadow"] is True


# ------------------------------------------------------------------ 3. the symbol's own earlier calendar rows
def test_history_last_four_majority_resolves_the_session():
    rows = [row("UW_CALENDAR", "MMM", "2026-11-04", None, flag="company")]
    hist = [row("UW_CALENDAR", "MMM", d, "afterhours", at=T0 - timedelta(days=n)) for n, d in ((80, "2026-08-13"), (170, "2026-05-14"), (260, "2026-02-12"), (350, "2025-11-13"))]
    ev = ev_of("MMM", rows, hist)
    assert ev["kind"] == "AMC" and ev["session_source"] == "history-last4" and ev["eligible_for_shadow"] is True and ev["confirmation"] == "single-vendor"
    assert "SESSION_FROM_HISTORY" in ev["reasons"] and ev["session_unknown"] is False


def test_history_three_of_four_resolves_mixed_history_does_not_and_a_vendor_label_wins():
    rows = [row("UW_CALENDAR", "NNN", "2026-11-04", None, flag="company")]
    three = [row("UW_CALENDAR", "NNN", "2026-08-13", "premarket", at=T0 - timedelta(days=80)), row("UW_CALENDAR", "NNN", "2026-05-14", "premarket", at=T0 - timedelta(days=170)),
             row("UW_CALENDAR", "NNN", "2026-02-12", "afterhours", at=T0 - timedelta(days=260)), row("UW_CALENDAR", "NNN", "2025-11-13", "premarket", at=T0 - timedelta(days=350))]
    assert ev_of("NNN", rows, three)["kind"] == "BMO"
    mixed = three[:2] + [row("UW_CALENDAR", "NNN", "2026-02-12", "afterhours", at=T0 - timedelta(days=260)), row("UW_CALENDAR", "NNN", "2025-11-13", "afterhours", at=T0 - timedelta(days=350))]
    ev = ev_of("NNN", rows, mixed)
    assert ev["kind"] == "UNKNOWN" and ev["session_unknown"] is True and ev["confirmation"] == "session-unknown" and ev["eligible_for_shadow"] is False
    labelled = [row("UW_CALENDAR", "OOO", "2026-11-04", "afterhours", flag="company")]
    bmo_hist = [row("UW_CALENDAR", "OOO", "2026-08-13", "premarket", at=T0 - timedelta(days=80))]
    assert ev_of("OOO", labelled, bmo_hist)["session_source"] == "uw-report-time"                                  # history is only consulted when no vendor answers
    assert CS.history_session("NNN", date(2026, 11, 4), CS.history_index(three[:2]))[0] == "BMO"                   # two known, both BMO
    assert CS.history_session("NNN", date(2026, 11, 4), CS.history_index(three[:1]))[0] == "BMO"                   # one known
    assert CS.history_session("ZZZ", date(2026, 11, 4), {})[0] is None


def test_history_only_uses_earlier_dates_and_the_history_index_drops_a_date_whose_rows_disagree():
    future = [row("UW_CALENDAR", "PPP", "2027-02-10", "afterhours", at=T0 - timedelta(minutes=5))]
    assert CS.history_session("PPP", date(2026, 11, 4), CS.history_index(future))[0] is None
    disagree = [row("UW_CALENDAR", "QQQ", "2026-08-13", "premarket", at=T0 - timedelta(days=80)), row("FINNHUB", "QQQ", "2026-08-13", "amc", at=T0 - timedelta(days=80))]
    assert CS.history_index(disagree).get("QQQ", []) == []


# ------------------------------------------------------------------ 4. SESSION_UNKNOWN is shown, never skipped
def test_no_session_anywhere_is_session_unknown_in_the_event_record_and_the_universe_table(tmp_path):
    rows = [row("UW_CALENDAR", "RRR", "2026-11-04", None, flag="company"), row("FINNHUB", "RRR", "2026-11-04", None),
            row("UW_CALENDAR", "SSS", "2026-11-04", "afterhours", flag="company")]
    recs = ER.records_from_saved(rows, T0)
    by = {r["symbol"]: r for r in recs}
    assert by["RRR"]["session"] == "UNKNOWN" and by["RRR"]["session_unknown"] is True and by["RRR"]["confirmation"] == "session-unknown" and by["RRR"]["prediction_eligible"] is False
    assert "UNKNOWN_TIMING" in by["RRR"]["ineligible_reasons"]
    assert by["SSS"]["prediction_eligible"] is True and by["SSS"]["confirmation"] == "single-vendor" and by["SSS"]["session_source"] == "uw-report-time" and by["SSS"]["session"] == "AMC"

    class Store:
        def read(self, what):
            return rows if what == "cal_obs" else []

    class Svc:
        store = Store()
        cfg = None

        def _latest(self, *a, **k):
            return None

    table = U.build(Svc(), T0, horizon_days=10, slot_state={"slots": {}, "symbol_index": {}}, ledger_path=None)
    t = {x["symbol"]: x for x in table["rows"]}
    assert t["RRR"]["collection_status"] == "SESSION_UNKNOWN" and "never skipped" in t["RRR"]["status_note"] and t["RRR"]["confirmation"] == "session-unknown"
    assert t["SSS"]["collection_status"] != "SESSION_UNKNOWN" and t["SSS"]["confirmation"] == "single-vendor" and t["SSS"]["session_source"] == "uw-report-time"


def test_calendar_fields_shape():
    ev = ev_of("TTT", [row("UW_CALENDAR", "TTT", "2026-11-04", "premarket", flag="company")])
    f = CS.calendar_fields(ev)
    assert set(f) == {"confirmation", "session_source", "session_unknown", "engine_no_trade_reasons"} and f["confirmation"] == "single-vendor" and f["engine_no_trade_reasons"]


# ------------------------------------------------------------------ 5. the v6.7 live-pass regression (2026-11-14)
def test_history_session_survives_a_caller_passing_the_wrong_shape():
    """The v6.7 live pass died on its second symbol with AttributeError: 'list' object has no attribute 'get'.
    smart_move_live.score_all held the calendar history in a variable called `hist`, and further down the same loop
    `hist` is reassigned to that symbol's earnings ROWS - so symbol two onwards passed a list where a dict belongs.
    The variable is renamed, and this guard means no caller can ever take a live pass down that way again."""
    assert CS.history_session("AAA", date(2026, 11, 4), []) == (None, "no history index")
    assert CS.history_session("AAA", date(2026, 11, 4), None)[0] is None
    assert CS.history_session("AAA", date(2026, 11, 4), [("2026-08-01", "BMO")])[0] is None


def test_evaluate_event_ignores_a_wrong_shaped_history_instead_of_raising():
    rows = [row("UW_CALENDAR", "AAA", "2026-11-04", None, flag="company")]      # no session: history would be consulted
    ev = CS.evaluate_event("AAA", calobs(rows), T0, TODAY, [])                   # a list, as the broken caller passed
    assert ev["session_unknown"] is True and ev["eligible_for_shadow"] is False


def test_the_live_scorer_does_not_reuse_the_earnings_rows_variable_for_the_calendar_history():
    """Pins the rename. Inside score_all's loop the earnings ROWS are held in `hist`; the calendar history must not
    share that name, or symbol two onwards gets a list where a dict belongs - the 2026-11-14 live-pass failure."""
    src = (ROOT / "backend" / "smart_move_live.py").read_text(encoding="utf-8")
    import re
    # anchored to the line start: "cal_hist = ..." contains "hist = ..." as a substring
    assert not re.search(r"^\s+hist = self\.?s?\.?cal_history\(\)", src, re.M), "the calendar history is back on the shadowed name"
    assert src.count("cal_hist = self") == 2                      # score_all and eligible_events
    assert src.count("date(), cal_hist)") == 2                    # both pass the renamed dict
    assert "hist, sha_h = self._rows_of(hist_rec)" in src         # the earnings rows still use `hist`
