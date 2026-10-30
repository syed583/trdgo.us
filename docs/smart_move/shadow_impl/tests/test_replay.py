"""Replay safeguards, synthetic data only: no later candle, no later event, SEC history rules, sealed-period dates never read, no outcome columns."""
import csv
import json
from datetime import date, datetime, timedelta, timezone

from fixtures import make_history, make_series, rows_from
from smvshadow import cutoff as co, replay as rp, scorer as sc

EVENT = date(2025, 6, 12)


def pre():
    return sc.load_prereg()


def test_sec_history_rules(tmp_path):
    f = {"AAA": {"item_2_02_filings": [
        {"filingDate": "2025-05-01", "acceptanceDateTime": "2025-05-01T11:30:00.000Z"},        # 07:30 ET -> premarket
        {"filingDate": "2025-02-04", "acceptanceDateTime": "2025-02-04T21:20:00.000Z"},        # 16:20 ET -> postmarket
        {"filingDate": "2025-02-10", "acceptanceDateTime": "2025-02-10T21:20:00.000Z"},        # re-furnished within 45 days -> dropped
        {"filingDate": "2024-11-05", "acceptanceDateTime": "2024-11-05T17:00:00.000Z"},        # 12:00 ET, during hours -> dropped
        {"filingDate": "2025-10-28", "acceptanceDateTime": "2025-10-28T21:00:00.000Z"}]}}     # after the cut -> never used
    p = tmp_path / "sec.json"
    p.write_text(json.dumps(f))
    h = rp.load_sec_history(p, date(2025, 9, 30))
    assert h["AAA"] == [(date(2025, 2, 4), "postmarket"), (date(2025, 5, 1), "premarket")]


def test_history_loader_ignores_sealed_dates(tmp_path):
    p = tmp_path / "e.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "earnings_date", "provider_label", "move_pct"])
        w.writerows([["AAA", "2025-03-01", "premarket", "1.0"], ["AAA", "2025-12-01", "premarket", "9.9"], ["AAA", "2024-09-01", "premarket", "2.0"]])
    h = rp.load_history(p, date(2025, 9, 30), date(2024, 10, 1))
    assert h == {"AAA": [(date(2025, 3, 1), "premarket")]}


def test_replay_uses_only_what_was_known_at_the_capture_time():
    p40, sha = pre()
    rows, react = make_history(EVENT, "BMO", [4, 4, 4, 4, 4, 4])
    hist = [(date.fromisoformat(r["report_date"]), r["report_time"]) for r in rows] + [(date(2025, 7, 1), "premarket")]            # a LATER event must be ignored
    ser = make_series("AAA", date(2023, 11, 1), date(2025, 9, 30), reactions=react)
    spy = make_series("SPY", date(2023, 11, 1), date(2025, 9, 30))
    cand, spyr = rows_from(ser), rows_from(spy)
    r = rp.replay_event(p40, sha, "AAA", EVENT, "BMO", hist, cand, spyr, "c" * 64, "s" * 64, [])
    plan = co.plan_event(EVENT, "BMO")
    assert r["replayed"] and r["last_session"] <= plan["cutoff_utc"].date().isoformat() and r["evidence"]["3"]["usable"] and r["evidence"]["5"]["usable"]
    assert not r["evidence"]["7"]["usable"] and r["evidence"]["7"]["missing_reason"] == "NOT_CAPTURED"
    # poisoning every candle AFTER the cutoff session must not change anything
    poisoned = [dict(x, close=x["close"] * 5) if x["date"] > r["last_session"] else x for x in cand]
    r2 = rp.replay_event(p40, sha, "AAA", EVENT, "BMO", hist, poisoned, spyr, "c" * 64, "s" * 64, [])
    assert {k: r[k] for k in r if k.startswith(("bull", "bear", "signal"))} == {k: r2[k] for k in r2 if k.startswith(("bull", "bear", "signal"))}
    # a later event row is ignored too
    r3 = rp.replay_event(p40, sha, "AAA", EVENT, "BMO", hist[:-1], cand, spyr, "c" * 64, "s" * 64, [])
    assert r3["bull_continuation"] == r["bull_continuation"]


def test_unknown_timing_is_not_replayed_and_contradicted_label_is_flagged():
    p40, sha = pre()
    r = rp.replay_event(p40, sha, "AAA", EVENT, "UNKNOWN", [], [], [], "c" * 64, "s" * 64, ["TIMING_UNKNOWN"])
    assert not r["replayed"] and r["signal_continuation"] == "NO_TRADE" and "UNKNOWN_TIMING" in r["reason_codes"]
    rows, react = make_history(EVENT, "BMO", [4] * 6)
    ser = make_series("AAA", date(2023, 11, 1), date(2025, 9, 30), reactions=react)
    spy = make_series("SPY", date(2023, 11, 1), date(2025, 9, 30))
    hist = [(date.fromisoformat(x["report_date"]), x["report_time"]) for x in rows]
    r2 = rp.replay_event(p40, sha, "AAA", EVENT, "BMO", hist, rows_from(ser), rows_from(spy), "c" * 64, "s" * 64, ["LABEL_CONTRADICTED_BY_FILING_AFTER_THE_FACT"])
    assert r2["signal_continuation"] == "NO_TRADE" and "TIMING_CONTRADICTED" in r2["reason_codes"]


def test_replay_rows_carry_no_outcome_fields():
    p40, sha = pre()
    r = rp.replay_event(p40, sha, "AAA", EVENT, "UNKNOWN", [], [], [], "c" * 64, "s" * 64, [])
    assert not any(k in r for k in ("move_pct", "direction", "outcome", "hit"))


def test_sec_tagging_excludes_ambiguous_events(tmp_path):
    def f(day, hhmm):                                                          # ET clock; January and May dates are winter/summer, so use fixed UTC offsets via the stored Z time
        return day, hhmm
    filings = [
        {"filingDate": "2024-02-06", "acceptanceDateTime": "2024-02-06T12:30:00.000Z"},       # 07:30 ET premarket, alone -> BMO_CERTAIN
        {"filingDate": "2024-05-02", "acceptanceDateTime": "2024-05-02T21:30:00.000Z"},       # 17:30 ET alone, no provider row -> AMC_UNCONFIRMED
        {"filingDate": "2024-08-01", "acceptanceDateTime": "2024-08-01T17:00:00.000Z"},       # 13:00 ET -> DURING_MARKET
        {"filingDate": "2024-11-05", "acceptanceDateTime": "2024-11-05T22:00:00.000Z"},       # 17:00 ET, provider agrees -> AMC_RECONCILED
        {"filingDate": "2025-02-04", "acceptanceDateTime": "2025-02-04T22:00:00.000Z"},       # 17:00 ET, provider says 5 Feb premarket -> PROVIDER_MISMATCH
        {"filingDate": "2025-05-20", "acceptanceDateTime": "2025-05-20T12:00:00.000Z"},       # two filings 10 days apart -> CLUSTER both
        {"filingDate": "2025-05-30", "acceptanceDateTime": "2025-05-30T12:00:00.000Z"}]
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"AAA": {"item_2_02_filings": filings}}))
    uw = {"AAA": [(date(2024, 11, 5), "postmarket"), (date(2025, 2, 5), "premarket")]}
    ok, amb = rp.load_sec_tagged(p, date(2025, 9, 30), uw)
    assert [(d.isoformat(), t) for d, _, t in ok["AAA"]] == [("2024-02-06", "BMO_CERTAIN"), ("2024-05-02", "AMC_UNCONFIRMED"), ("2024-11-05", "AMC_RECONCILED")]
    assert sorted((d.isoformat(), r) for d, r in amb["AAA"]) == [("2024-08-01", "DURING_MARKET"), ("2025-02-04", "PROVIDER_MISMATCH"), ("2025-05-20", "CLUSTER"), ("2025-05-30", "CLUSTER")]
