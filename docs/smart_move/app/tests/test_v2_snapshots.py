"""Analyst-consensus snapshot history: exact period, append-only/idempotent ledger, point-in-time reads, revision only from two real snapshots, readiness."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_live as L  # noqa: E402
import smart_move_v2 as V  # noqa: E402
import smart_move_v2_collect as C  # noqa: E402
import smart_move_v2_snapshots as SN  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


def est(eps, rev, quarter="2026-09-30"):
    base = {"horizon": "fiscal quarter", "eps_estimate_analyst_count": 20.0, "eps_estimate_revision_up_last_week": None, "eps_estimate_revision_down_last_week": None}
    return {"data": {"ticker": "A", "estimates": [dict(base, date="2026-12-31", eps_estimate_average=9.9, revenue_estimate_average=999.0),               # the NEXT quarter: must never be picked
                                                  dict(base, date=quarter, eps_estimate_average=eps, revenue_estimate_average=rev),
                                                  {"date": "2027-09-30", "horizon": "fiscal year", "eps_estimate_average": 5.0, "revenue_estimate_average": 5.0}]}}


def save(st, symbol, body, at):
    raw = json.dumps(body).encode()
    st.append("v2_evidence", {"id": "x%s" % at.isoformat(), "kind": "uw_estimates", "symbol": symbol, "retrieved_at": at.isoformat(), "raw_sha256": st.put(raw), "status": "OK", "rows": 3, "params": {}})


def test_ending_quarter_and_exact_row_never_fall_back_to_a_neighbouring_period():
    er = [{"report_date": "2026-11-02", "ending_fiscal_quarter": "2026-09-30"}, {"report_date": "2026-07-30", "ending_fiscal_quarter": "2026-06-30"}]
    q = SN.ending_quarter(er, "2026-11-02")
    assert q == "2026-09-30" and SN.ending_quarter(er, "2026-12-01") is None
    row = SN.quarter_row(est(1.5, 100.0)["data"]["estimates"], q)
    assert row["eps_estimate_average"] == 1.5                                       # not 9.9 (next quarter) and not the fiscal-year row
    assert SN.quarter_row(est(1.5, 100.0)["data"]["estimates"], "2026-03-31") is None and SN.quarter_row([], q) is None


def test_ledger_is_append_only_idempotent_and_point_in_time(tmp_path):
    st = L.LiveStore(tmp_path)
    save(st, "A", est(1.00, 100.0), T0)
    save(st, "A", est(1.05, 103.0), T0 + timedelta(days=8))
    assert SN.rebuild(st, {"A": "2026-09-30"}) == {"added": 2, "skipped_no_quarter": 0, "skipped_no_exact_row": 0}
    assert SN.rebuild(st, {"A": "2026-09-30"})["added"] == 0 and len(st.read(SN.LEDGER)) == 2         # a second pass adds nothing
    assert SN.rebuild(st, {})["skipped_no_quarter"] == 2
    assert len(SN.history(st, "A", "2026-09-30", T0 + timedelta(days=1))) == 1                         # the later snapshot is invisible before it existed
    rev = SN.revision(st, "A", "2026-09-30", T0 + timedelta(days=9))
    assert rev["eps_revision_pct"] == 5.0 and rev["revenue_revision_pct"] == 3.0
    assert SN.revision(st, "A", "2026-09-30", T0 + timedelta(days=1)) is None                          # one observation never makes a trend


def test_a_revision_needs_a_prior_snapshot_in_the_7_to_60_day_band_of_the_same_period(tmp_path):
    st = L.LiveStore(tmp_path)
    save(st, "A", est(1.0, 100.0), T0)
    save(st, "A", est(1.1, 110.0), T0 + timedelta(days=3))                                              # only 3 days apart
    SN.rebuild(st, {"A": "2026-09-30"})
    assert SN.revision(st, "A", "2026-09-30", T0 + timedelta(days=4)) is None
    save(st, "A", est(1.2, 120.0), T0 + timedelta(days=90))                                             # 90 days later: the first is too old, the 3-day-old one is too close
    SN.rebuild(st, {"A": "2026-09-30"})
    assert SN.revision(st, "A", "2026-09-30", T0 + timedelta(days=91)) is None
    other = est(1.0, 100.0, quarter="2026-06-30")                                                       # a snapshot of ANOTHER period never mixes in
    save(st, "B", other, T0)
    save(st, "B", est(2.0, 200.0), T0 + timedelta(days=8))
    SN.rebuild(st, {"B": "2026-09-30"})
    assert SN.revision(st, "B", "2026-09-30", T0 + timedelta(days=9)) is None


def test_readiness_states_and_the_scorer_uses_the_exact_quarter(tmp_path):
    st = L.LiveStore(tmp_path)
    save(st, "A", est(1.0, 100.0), T0)
    SN.rebuild(st, {"A": "2026-09-30"})
    rd = SN.readiness(st, ["A", "Z"], {"A": "2026-09-30", "Z": "2026-09-30"}, T0 + timedelta(days=1))
    assert rd["by_symbol"]["A"]["state"] == "WAITING_FOR_SECOND_SNAPSHOT" and rd["by_symbol"]["A"]["second_snapshot_useful_from"].startswith("2026-11-08") and rd["by_symbol"]["Z"]["state"] == "NO_SNAPSHOT"
    save(st, "A", est(1.1, 105.0), T0 + timedelta(days=8))
    SN.rebuild(st, {"A": "2026-09-30"})
    assert SN.readiness(st, ["A"], {"A": "2026-09-30"}, T0 + timedelta(days=9))["by_symbol"]["A"]["state"] == "REVISION_MEASURABLE"
    # the V2 input loader with the exact quarter feeds the SAME numbers to the scorer; without the quarter the legacy mapping would have used the next quarter (9.9)
    inp = C.load_inputs(st, "A", T0 + timedelta(days=9), ending_quarter="2026-09-30")
    assert [s["eps"] for s in inp["est_snapshots"]] == [1.0, 1.1]
    r = V.analyst(inp["est_snapshots"], None, T0 + timedelta(days=9), "continuation")
    assert r["available"] and r["eps_revision_pct"] == 10.0 and r["bull"] >= 5
    legacy = C.load_inputs(st, "A", T0 + timedelta(days=9))
    assert [s["eps"] for s in legacy["est_snapshots"]] == [9.9, 9.9]                                    # documents the defect the exact mapping removes
    assert C.load_inputs(st, "A", T0 + timedelta(days=9), ending_quarter="2026-03-31")["est_snapshots"] == []   # unknown quarter row: no snapshot, no guess


def test_calendar_history_reads_the_saved_calendar_payloads_stamps_each_point_and_never_fills_gaps():
    obs = [{"symbol": "A", "date": "2026-11-02", "source": "UW_CALENDAR", "raw_sha256": "u1", "observed_at": "2026-10-30T15:13:00+00:00"},
           {"symbol": "A", "date": "2026-11-02", "source": "UW_CALENDAR", "raw_sha256": "u2", "observed_at": "2026-11-01T10:38:00+00:00"},
           {"symbol": "A", "date": "2026-11-02", "source": "FINNHUB", "raw_sha256": "f1", "observed_at": "2026-10-30T15:14:00+00:00"},
           {"symbol": "B", "date": "2026-11-02", "source": "UW_CALENDAR", "raw_sha256": "u1", "observed_at": "2026-10-30T15:13:00+00:00"},
           {"symbol": "A", "date": "2026-11-09", "source": "UW_CALENDAR", "raw_sha256": "u2", "observed_at": "2026-11-01T10:38:00+00:00"}]
    bodies = {"u1": {"data": [{"symbol": "A", "report_date": "2026-11-02", "street_mean_est": "1.50"}, {"symbol": "B", "report_date": "2026-11-02", "street_mean_est": None}]},
              "u2": {"data": [{"symbol": "A", "report_date": "2026-11-02", "street_mean_est": "1.52"}]}, "f1": {"earningsCalendar": [{"symbol": "A", "date": "2026-11-02", "epsEstimate": 1.5}]}}
    h = SN.calendar_history(obs, bodies.get, "2026-11-02")
    assert h["by_symbol"]["A"]["UW_CALENDAR"] == [("2026-10-30T15:13:00+00:00", 1.5), ("2026-11-01T10:38:00+00:00", 1.52)] and "B" not in h["by_symbol"]       # a null consensus is not a value
    assert h["by_symbol"]["A"]["FINNHUB"] == [("2026-10-30T15:14:00+00:00", 1.5)] and h["max_span_hours"] == 43.4 and h["series_with_a_change"] == 1
    assert h["usable_now"] is False                                                                                                         # 43 h is below the 7-day band: seen, never scored
