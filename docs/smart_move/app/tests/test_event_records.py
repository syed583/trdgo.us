"""Earnings event records (Phase 13, Priority 1): identity, versioning, vendor evidence, fiscal quarter, release basis, point-in-time views. Synthetic fixtures only; no provider, no network."""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_event_records as ER  # noqa: E402
import smart_move_event_registry as REG  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)               # Monday 11:00 ET; the calendar was retrieved just before


def row(source, sym, date, label=None, clock=None, flag=None, at=T0 - timedelta(minutes=30), sha=None):
    return {"source": source, "symbol": sym, "date": date, "label": label, "clock": clock, "vendor_flag": flag, "raw_sha256": sha or hashlib.sha256((source + sym + date).encode()).hexdigest(),
            "observed_at": at.isoformat(), "request_id": "req-%s-%s" % (source, sym), "mode": "LIVE"}


def confirmed(sym, date, kind="BMO", at=T0 - timedelta(minutes=30)):
    """Two vendors that agree on date and session, UW flagged 'company': VENDOR_CONFIRMED."""
    lab, hr = ("premarket", "bmo") if kind == "BMO" else ("postmarket", "amc")
    return [row("UW_CALENDAR", sym, date, label=lab, flag="company", at=at), row("FINNHUB", sym, date, label=hr, at=at)]


def obs(sym, rows, as_of=T0, **kw):
    return ER.build_observation(sym, ER.calobs_from_rows(rows)[sym], as_of, **kw)


def payload(*rows):
    return list(rows)


def erow(report_date, quarter, src="company"):
    return {"source": src, "report_date": report_date, "ending_fiscal_quarter": quarter, "report_time": "premarket"}


# ------------------------------------------------------------------ 1. confirmed events
def test_a_confirmed_event_is_eligible_with_vendor_evidence_and_a_nominal_release_time():
    o = obs("AAA", confirmed("AAA", "2026-11-04"))
    assert o["vendor_status"] == "VENDOR_CONFIRMED" and o["session"] == "BMO" and o["prediction_eligible"] is True and o["ineligible_reasons"] == []
    assert o["release_basis"] == "NOMINAL_BMO" and o["release_time_verified"] is False and o["nominal_release_utc"] == "2026-11-04T11:00:00+00:00" and o["release_utc"] is None   # 06:00 EST = 11:00 UTC
    assert o["cutoff_utc"] == "2026-11-03T11:00:00+00:00" and {v["source"] for v in o["vendors"]} == {"UW_CALENDAR", "FINNHUB"} and all(v["raw_sha256"] and v["observed_at"] for v in o["vendors"])
    amc = obs("BBB", confirmed("BBB", "2026-11-03", "AMC"))
    assert amc["session"] == "AMC" and amc["nominal_release_utc"] == "2026-11-03T21:00:00+00:00"                      # 16:00 EST = 21:00 UTC


# ------------------------------------------------------------------ 2. conflicting vendor dates
def test_conflicting_vendor_dates_are_preserved_and_the_event_is_not_eligible():
    rows = [row("UW_CALENDAR", "CCC", "2026-11-04", "premarket", flag="company"), row("FINNHUB", "CCC", "2026-11-09", "bmo")]
    o = obs("CCC", rows)
    assert o["vendor_conflict"] is True and o["prediction_eligible"] is False and o["event_date"] is None and o["vendor_dates_seen"] == ["2026-11-04", "2026-11-09"]
    assert {(v["source"], v["date"]) for v in o["vendors"]} == {("UW_CALENDAR", "2026-11-04"), ("FINNHUB", "2026-11-09")} and "DATE_CONFLICT_OVER_1_DAY" in o["ineligible_reasons"]
    one_day = obs("DDD", [row("UW_CALENDAR", "DDD", "2026-11-04", "premarket", flag="company"), row("FINNHUB", "DDD", "2026-11-05", "bmo")])
    assert one_day["prediction_eligible"] is False and one_day["vendor_conflict"] is True and "DATE_DISAGREEMENT_1_DAY" in one_day["ineligible_reasons"]


def test_estimated_unknown_timing_and_stale_calendars_are_never_eligible():
    est = obs("EEE", [row("UW_CALENDAR", "EEE", "2026-11-04", "premarket", flag="estimation"), row("FINNHUB", "EEE", "2026-11-04", "bmo")])
    assert est["prediction_eligible"] is False and est["ineligible_reasons"]
    single = obs("FFF", [row("UW_CALENDAR", "FFF", "2026-11-04", "premarket", flag="company")])
    assert single["prediction_eligible"] is True and single["confirmation"] == "single-vendor" and single["ineligible_reasons"] == []   # kit v6.7 (owner decision
    assert "SINGLE_VENDOR_ACCEPTED_KIT_V6_7" in single["vendor_reasons"]                       # 2026-11-13): a single-vendor date is scanned and LABELLED, never skipped
    notime = obs("GGG", [row("UW_CALENDAR", "GGG", "2026-11-04", None, flag="company"), row("FINNHUB", "GGG", "2026-11-04", None)])
    assert notime["session"] == "UNKNOWN" and notime["prediction_eligible"] is False and notime["release_basis"] == "UNKNOWN" and "UNKNOWN_TIMING" in notime["ineligible_reasons"]
    stale = obs("HHH", confirmed("HHH", "2026-11-04", at=T0 - timedelta(hours=9)))
    assert stale["prediction_eligible"] is False and stale["vendor_status"] == "STALE"                                   # the strict 6 h freshness rule


# ------------------------------------------------------------------ 3 and 4. fiscal quarter
def test_unknown_quarter_states_are_explicit_and_never_inferred_from_dates():
    base = confirmed("AAA", "2026-11-04")
    none = obs("AAA", base)
    assert none["quarter_state"] == "QUARTER_UNKNOWN" and none["ending_fiscal_quarter"] == ER.QUARTER_UNKNOWN and none["quarter_reason"] == "NO_EARNINGS_PAYLOAD"
    assert none["prediction_eligible"] is True and none["analyst_pairing_eligible"] is False and none["pairing_blockers"] == ["QUARTER_UNKNOWN:NO_EARNINGS_PAYLOAD"]
    other_date = obs("AAA", base, earnings_rows=payload(erow("2026-08-05", "2026-06-30")), earnings_retrieved_at=T0 - timedelta(hours=1))
    assert other_date["quarter_reason"] == "NO_ROW_FOR_REPORT_DATE" and other_date["ending_fiscal_quarter"] == ER.QUARTER_UNKNOWN          # a plausible-looking quarter of ANOTHER report is not borrowed
    empty = obs("AAA", base, earnings_rows=payload(erow("2026-11-04", None)), earnings_retrieved_at=T0 - timedelta(hours=1))
    assert empty["quarter_reason"] == "QUARTER_FIELD_EMPTY" and empty["quarter_state"] == "QUARTER_UNKNOWN"


def test_a_quarter_is_known_only_from_an_explicit_saved_field_with_full_provenance():
    o = obs("AAA", confirmed("AAA", "2026-11-04"), earnings_rows=payload(erow("2026-11-04", "2026-09-30"), erow("2026-08-05", "2026-06-30")), earnings_retrieved_at=T0 - timedelta(hours=2), earnings_sha256="ab" * 32)
    assert o["quarter_state"] == "KNOWN" and o["ending_fiscal_quarter"] == "2026-09-30" and o["analyst_pairing_eligible"] is True and o["pairing_blockers"] == []
    s = o["quarter_source"]
    assert s["provider_field"] == "ending_fiscal_quarter" and "not a calendar quarter" in s["field_meaning"] and s["retrieved_at"] == (T0 - timedelta(hours=2)).isoformat() and s["payload_sha256"] == "ab" * 32
    assert s["report_date_row"] == "2026-11-04" and s["days_before_report"] == 35 and "fiscal_year" not in o and "fiscal_quarter_number" not in o       # no fiscal-year or quarter number is supplied, so none is invented


@pytest.mark.parametrize("rows,reason", [([erow("2026-11-04", "2026-09-30"), erow("2026-11-04", "2026-06-30")], "CONFLICTING_QUARTER_VALUES"), ([erow("2026-11-04", "not-a-date")], "INVALID_QUARTER_VALUE"),
                                         ([erow("2026-11-04", "2026-11-04")], "QUARTER_NOT_BEFORE_REPORT"), ([erow("2026-11-04", "2026-12-31")], "QUARTER_NOT_BEFORE_REPORT"),
                                         ([erow("2026-11-04", "2026-01-31")], "QUARTER_TOO_OLD_FOR_THIS_REPORT")])
def test_ambiguous_or_implausible_quarters_are_rejected_not_repaired(rows, reason):
    q = ER.extract_quarter(rows, "2026-11-04", retrieved_at=T0 - timedelta(hours=1), as_of=T0)
    assert q["state"] == "AMBIGUOUS" and q["reason"] == reason and q["ending_fiscal_quarter"] == ER.QUARTER_UNKNOWN and q["source"] is None


def test_a_payload_retrieved_after_the_as_of_time_cannot_describe_the_earlier_moment():
    q = ER.extract_quarter([erow("2026-11-04", "2026-09-30")], "2026-11-04", retrieved_at=T0 + timedelta(hours=1), as_of=T0)
    assert q["state"] == "QUARTER_UNKNOWN" and q["reason"] == "PAYLOAD_RETRIEVED_AFTER_AS_OF"


def test_earnings_payloads_are_selected_point_in_time_from_saved_evidence():
    ev = [{"kind": "earnings", "symbol": "AAA", "retrieved_at": (T0 - timedelta(days=2)).isoformat(), "raw_sha256": "old", "status": "OK"},
          {"kind": "earnings", "symbol": "AAA", "retrieved_at": (T0 - timedelta(hours=3)).isoformat(), "raw_sha256": "new", "status": "OK"},
          {"kind": "earnings", "symbol": "AAA", "retrieved_at": (T0 + timedelta(hours=3)).isoformat(), "raw_sha256": "future", "status": "OK"},
          {"kind": "ohlc", "symbol": "AAA", "retrieved_at": T0.isoformat(), "raw_sha256": "x", "status": "OK"}]
    out = ER.earnings_payloads(ev, lambda sha: {"data": [erow("2026-11-04", "2026-09-30")]} if sha != "future" else {"data": []}, T0)
    assert out["AAA"][2] == "new"                                                                                          # the newest payload retrieved at or before as_of; the future one is invisible


# ------------------------------------------------------------------ 5. rescheduling and identity
def test_a_reschedule_of_the_same_quarter_is_one_event_with_a_new_current_version():
    st = ER.EventRecords()
    q = dict(earnings_rows=payload(erow("2026-11-04", "2026-09-30"), erow("2026-11-12", "2026-09-30")), earnings_retrieved_at=T0 - timedelta(hours=2))
    a = st.observe(obs("AAA", confirmed("AAA", "2026-11-04"), **q))
    later = T0 + timedelta(hours=2)
    b = st.observe(obs("AAA", confirmed("AAA", "2026-11-12", at=later - timedelta(minutes=10)), as_of=later, **q))
    assert a["action"] == "NEW" and b["action"] == "RESCHEDULED" and a["event_id"] == b["event_id"] == "AAA|2026-09-30"
    h = st.history("AAA|2026-09-30")
    assert [v["status"] for v in h] == [ER.CURRENT, ER.RESCHEDULED, ER.CURRENT] and h[-1]["original_date"] == "2026-11-04" and h[-1]["event_date"] == "2026-11-12" and h[-1]["change_reasons"] == ["DATE"]
    assert list(st.current()) == ["AAA|2026-09-30"]


def test_a_session_change_is_a_reschedule_too():
    st = ER.EventRecords()
    st.observe(obs("AAA", confirmed("AAA", "2026-11-04", "BMO")))
    r = st.observe(obs("AAA", confirmed("AAA", "2026-11-04", "AMC", at=T0 + timedelta(hours=1, minutes=-5)), as_of=T0 + timedelta(hours=1)))
    assert r["action"] == "RESCHEDULED" and st.latest(r["event_id"])["session"] == "AMC" and st.latest(r["event_id"])["change_reasons"] == ["SESSION"]


def test_unknown_quarter_events_link_within_the_window_but_never_across_quarters():
    st = ER.EventRecords()
    a = st.observe(obs("AAA", confirmed("AAA", "2026-11-04")))
    near = T0 + timedelta(hours=2)
    b = st.observe(obs("AAA", confirmed("AAA", "2026-11-10", at=near - timedelta(minutes=5)), as_of=near))                  # 6 days later: the same event moved
    assert a["event_id"] == b["event_id"] == "AAA|UNKNOWN|2026-11-04" and b["action"] == "RESCHEDULED"
    far = T0 + timedelta(hours=3)
    c = st.observe(obs("AAA", confirmed("AAA", "2027-02-10", at=far - timedelta(minutes=5)), as_of=far))                      # three months later: the NEXT quarter's report
    assert c["action"] == "NEW" and c["event_id"] == "AAA|UNKNOWN|2027-02-10" and len(st.current()) == 2


def test_two_known_quarters_are_distinct_events_even_when_their_dates_are_close():
    st = ER.EventRecords()
    one = obs("AAA", confirmed("AAA", "2026-11-04"), earnings_rows=payload(erow("2026-11-04", "2026-09-30")), earnings_retrieved_at=T0 - timedelta(hours=2))
    t2 = T0 + timedelta(hours=1)
    two = obs("AAA", confirmed("AAA", "2026-11-12", at=t2 - timedelta(minutes=5)), as_of=t2, earnings_rows=payload(erow("2026-11-12", "2026-10-31")), earnings_retrieved_at=T0 - timedelta(hours=2))
    a, b = st.observe(one), st.observe(two)
    assert a["event_id"] == "AAA|2026-09-30" and b["event_id"] == "AAA|2026-10-31" and b["action"] == "NEW"               # 8 days apart, different quarters: never merged


def test_learning_the_quarter_later_updates_the_same_event_without_changing_its_identity():
    st = ER.EventRecords()
    a = st.observe(obs("AAA", confirmed("AAA", "2026-11-04")))
    later = T0 + timedelta(hours=1)
    b = st.observe(obs("AAA", confirmed("AAA", "2026-11-04", at=later - timedelta(minutes=5)), as_of=later, earnings_rows=payload(erow("2026-11-04", "2026-09-30")), earnings_retrieved_at=later - timedelta(minutes=2)))
    assert b["action"] == "UPDATED" and b["event_id"] == a["event_id"] and "QUARTER_LEARNED" in st.latest(a["event_id"])["change_reasons"]
    assert [v["status"] for v in st.history(a["event_id"])] == [ER.CURRENT, ER.SUPERSEDED, ER.CURRENT]


# ------------------------------------------------------------------ 6. removed and occurred
def test_an_event_missing_from_a_complete_window_is_removed_but_kept_and_outside_events_are_untouched():
    st = ER.EventRecords()
    st.observe_snapshot([obs("AAA", confirmed("AAA", "2026-11-04")), obs("BBB", confirmed("BBB", "2026-11-05")), obs("CCC", confirmed("CCC", "2026-12-20"))], observed=T0)
    t2 = T0 + timedelta(hours=3)
    res = st.observe_snapshot([obs("AAA", confirmed("AAA", "2026-11-04", at=t2 - timedelta(minutes=5)), as_of=t2)], observed=t2, window=(datetime(2026, 11, 3).date(), datetime(2026, 11, 10).date()), complete=True)
    assert res["REMOVED"] == ["BBB|UNKNOWN|2026-11-05"] and res["UNCHANGED"] == ["AAA|UNKNOWN|2026-11-04"]
    assert st.latest("BBB|UNKNOWN|2026-11-05")["status"] == ER.REMOVED and len(st.history("BBB|UNKNOWN|2026-11-05")) == 2                  # still auditable
    assert st.latest("CCC|UNKNOWN|2026-12-20")["status"] == ER.CURRENT                                                         # outside the snapshot window: not touched
    assert "BBB|UNKNOWN|2026-11-05" not in st.current()


def test_an_event_whose_date_has_passed_is_marked_occurred_not_removed():
    st = ER.EventRecords()
    st.observe(obs("AAA", confirmed("AAA", "2026-11-03", "AMC")))
    t2 = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
    res = st.observe_snapshot([], observed=t2)
    assert res["OCCURRED"] == ["AAA|UNKNOWN|2026-11-03"] and st.latest("AAA|UNKNOWN|2026-11-03")["status"] == ER.OCCURRED and st.current() == {}


# ------------------------------------------------------------------ 7. duplicates
def test_duplicate_observations_do_not_create_versions_and_snapshot_duplicates_are_reported():
    st = ER.EventRecords()
    o = obs("AAA", confirmed("AAA", "2026-11-04"))
    assert st.observe(o)["action"] == "NEW"
    again = obs("AAA", confirmed("AAA", "2026-11-04", at=T0 - timedelta(minutes=10)), as_of=T0 + timedelta(minutes=20))        # a later re-observation of the same facts
    assert st.observe(again)["action"] == "UNCHANGED" and len(st.versions) == 1
    r = st.observe_snapshot([o, o], observed=T0 + timedelta(minutes=30))
    assert r["duplicates"] == ["AAA"] and len(st.versions) == 1


# ------------------------------------------------------------------ 8. nominal versus verified release times
def test_only_an_explicit_verified_timestamp_makes_the_release_time_verified():
    nominal = obs("AAA", confirmed("AAA", "2026-11-04"))
    assert nominal["release_time_verified"] is False and nominal["release_basis"].startswith("NOMINAL")
    verified = obs("AAA", confirmed("AAA", "2026-11-04"), verified_release_utc=datetime(2026, 11, 4, 12, 31, tzinfo=UTC))
    assert verified["release_time_verified"] is True and verified["release_basis"] == "VERIFIED_TIMESTAMP" and verified["release_utc"] == "2026-11-04T12:31:00+00:00" and verified["nominal_release_utc"] is None
    assert ER.release_info(None, "UNKNOWN", None)["release_basis"] == "UNKNOWN"


# ------------------------------------------------------------------ 9. point in time
def test_point_in_time_views_hide_later_knowledge_and_the_quarter_learned_later():
    st = ER.EventRecords()
    st.observe(obs("AAA", confirmed("AAA", "2026-11-04")))
    later = T0 + timedelta(hours=1)
    st.observe(obs("AAA", confirmed("AAA", "2026-11-04", at=later - timedelta(minutes=5)), as_of=later, earnings_rows=payload(erow("2026-11-04", "2026-09-30")), earnings_retrieved_at=later - timedelta(minutes=1)))
    eid = "AAA|UNKNOWN|2026-11-04"
    assert st.as_of(eid, T0 - timedelta(minutes=1)) is None                                                                   # before the first observation nothing was known
    assert st.as_of(eid, T0 + timedelta(minutes=5))["quarter_state"] == "QUARTER_UNKNOWN"                                    # between the two observations the quarter was still unknown
    assert st.as_of(eid, later + timedelta(minutes=5))["ending_fiscal_quarter"] == "2026-09-30"
    assert [v["symbol"] for v in st.eligible(T0 + timedelta(minutes=5))] == ["AAA"] and st.eligible(T0 + timedelta(minutes=5), pairing=True) == []
    assert len(st.eligible(later + timedelta(minutes=5), pairing=True)) == 1
    assert len(st.eligible(T0 + timedelta(minutes=5), window_open=True)) == 1                                                 # 16:05 UTC on 2 Nov is inside the 4 Nov BMO window (opens 11:00 UTC, cutoff 3 Nov 11:00 UTC)
    assert st.eligible(datetime(2026, 11, 3, 12, 0, tzinfo=UTC), window_open=True) == []                                      # after the cutoff the window is closed


def test_a_status_relabel_is_invisible_before_its_own_time():
    st = ER.EventRecords()
    st.observe(obs("AAA", confirmed("AAA", "2026-11-04")))
    later = T0 + timedelta(hours=2)
    st.observe(obs("AAA", confirmed("AAA", "2026-11-09", at=later - timedelta(minutes=5)), as_of=later))
    eid = "AAA|UNKNOWN|2026-11-04"
    assert st.as_of(eid, T0 + timedelta(minutes=30))["status"] == ER.CURRENT and st.as_of(eid, T0 + timedelta(minutes=30))["event_date"] == "2026-11-04"      # the reschedule was not yet known
    assert st.as_of(eid, later + timedelta(minutes=1))["event_date"] == "2026-11-09" and st.as_of(eid, later + timedelta(minutes=1))["version"] == 2


def test_a_quarter_whose_payload_was_retrieved_after_the_view_time_is_hidden_even_inside_one_version():
    st = ER.EventRecords()
    rec = obs("AAA", confirmed("AAA", "2026-11-04"), as_of=T0 + timedelta(hours=2), earnings_rows=payload(erow("2026-11-04", "2026-09-30")), earnings_retrieved_at=T0 + timedelta(hours=1))
    rec["observed_at"] = (T0 + timedelta(minutes=1)).isoformat()                                                                # a (mis)stamped record: observed earlier than its payload
    st.observe(rec)
    v = st.as_of(st.event_ids()[0], T0 + timedelta(minutes=30))
    assert v["quarter_state"] == "QUARTER_UNKNOWN" and v["quarter_reason"] == "PAYLOAD_RETRIEVED_AFTER_AS_OF" and v["analyst_pairing_eligible"] is False


# ------------------------------------------------------------------ integrity, persistence, registry, summary
def test_records_are_append_only_persisted_hashed_and_reload_identically(tmp_path):
    p = tmp_path / "event_records.jsonl"
    st = ER.EventRecords(p)
    st.observe(obs("AAA", confirmed("AAA", "2026-11-04")))
    st.observe(obs("AAA", confirmed("AAA", "2026-11-09", at=T0 + timedelta(minutes=55)), as_of=T0 + timedelta(hours=1)))
    before = p.read_bytes()
    again = ER.EventRecords(p)
    assert again.versions == st.versions and len(before.splitlines()) == 3
    assert all(v["record_hash"] == ER.record_hash(v) for v in again.versions) and all(v["schema"] == ER.SCHEMA for v in again.versions)
    again.observe(obs("AAA", confirmed("AAA", "2026-11-09", at=T0 + timedelta(minutes=55)), as_of=T0 + timedelta(hours=2)))      # a duplicate adds nothing
    assert p.read_bytes() == before


def test_the_existing_event_registry_is_fed_from_the_records_without_losing_distinct_quarters():
    st = ER.EventRecords()
    st.observe(obs("AAA", confirmed("AAA", "2026-11-04"), earnings_rows=payload(erow("2026-11-04", "2026-09-30")), earnings_retrieved_at=T0 - timedelta(hours=1)))
    st.observe(obs("BBB", [row("UW_CALENDAR", "BBB", "2026-11-04", "premarket", flag="estimation")]))
    reg = REG.EventRegistry(None)
    r = ER.sync_registry(reg, st, T0)
    cur = reg.current()
    assert sorted(r["added"]) == ["AAA", "BBB"] and cur["AAA"]["confirmed"] is True and cur["AAA"]["fiscal_period"] == "2026-09-30" and cur["BBB"]["confirmed"] is False and cur["BBB"]["fiscal_period"] is None
    s = ER.summarize(st)
    assert s["events_current"] == 2 and s["prediction_eligible"] == 1 and s["pairing_eligible"] == 1 and s["release_time_verified"] == 0 and s["quarter_state"] == {"KNOWN": 1, "QUARTER_UNKNOWN": 1}


def test_records_from_saved_rows_use_saved_payloads_only():
    rows = confirmed("AAA", "2026-11-04") + confirmed("BBB", "2026-11-04")
    recs = ER.records_from_saved(rows, T0, {"AAA": ([erow("2026-11-04", "2026-09-30")], (T0 - timedelta(hours=1)).isoformat(), "cd" * 32)})
    by = {r["symbol"]: r for r in recs}
    assert by["AAA"]["quarter_state"] == "KNOWN" and by["BBB"]["quarter_state"] == "QUARTER_UNKNOWN" and by["BBB"]["quarter_reason"] == "NO_EARNINGS_PAYLOAD"


# ------------------------------------------------------------------ 10. frozen V1/V2 compatibility
def test_frozen_scoring_files_and_preregistrations_are_unchanged():
    h = lambda p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()  # noqa: E731
    assert h("docs/smart_move/baseline/prereg_baseline_v1.json").startswith("9316348ba0d29034") and h("docs/smart_move/shadow/prereg_score40_v1.json").startswith("b25bd8d788946156")
    assert h("docs/smart_move/shadow_impl/smvshadow/scorer.py").startswith("62e09aa0ed785e2a") and h("backend/smart_move_v2.py").startswith("de2d8f9612934338")
    import smart_move_v2 as V2
    assert V2.CONFIG_HASH == "16b9b2e149d99bac886c776b0b836f917878a23800a50d82d76387b443965863" and V2.VERSION == "v2-exp-2"


def test_the_event_record_module_does_not_import_scoring_or_provider_code():
    src = (ROOT / "backend" / "smart_move_event_records.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    text = " ".join(imports)
    for banned in ("scorer", "smart_move_v2", "unusualwhales_service", "smart_move_live", "InfraFetcher", "urllib", "requests", "grader"):
        assert banned not in text, banned
    assert "NEWS_GUIDANCE" not in src


# ------------------------------------------------------------------ Priority 1 audit: regression tests for the confirmed defects
def known_obs(sym, date, quarter, at, kind="BMO", retrieved=None):
    return obs(sym, confirmed(sym, date, kind, at=at), as_of=at + timedelta(minutes=10), earnings_rows=[erow(date, quarter)], earnings_retrieved_at=retrieved or at, earnings_sha256="c" * 64)


def test_quarter_known_before_the_cutoff_makes_pairing_possible():
    o = known_obs("AAA", "2026-11-04", "2026-09-30", T0)                                                               # cutoff 2026-11-03 11:00Z; payload retrieved 11-02 16:00Z
    assert o["quarter_known_before_cutoff"] is True and o["analyst_pairing_eligible"] is True and o["observed_after_cutoff"] is False


def test_quarter_first_known_after_the_cutoff_never_makes_the_earlier_prediction_eligible():
    late = T0 + timedelta(days=2)                                                                                        # 2026-11-04 16:00Z: the cutoff (11-03 11:00Z) has passed
    o = known_obs("AAA", "2026-11-04", "2026-09-30", late)
    assert o["quarter_state"] == "KNOWN" and o["quarter_known_before_cutoff"] is False
    assert o["analyst_pairing_eligible"] is False and "QUARTER_LEARNED_AFTER_CUTOFF" in o["pairing_blockers"]
    assert o["observed_after_cutoff"] is True and o["prediction_eligible"] is False and "OBSERVED_AFTER_CUTOFF" in o["ineligible_reasons"]
    st = ER.EventRecords()
    st.observe(obs("AAA", confirmed("AAA", "2026-11-04")))                                                               # observed before the cutoff, quarter unknown
    st.observe(o)
    before = T0 + timedelta(hours=12)                                                                                    # 11-03 04:00Z, before the cutoff
    assert st.eligible(before, pairing=True) == [] and [v["event_id"] for v in st.eligible(before)] == ["AAA|UNKNOWN|2026-11-04"]
    assert st.as_of("AAA|UNKNOWN|2026-11-04", before)["analyst_pairing_eligible"] is False


def test_missing_empty_and_late_payloads_get_distinct_reasons():
    ev = [{"kind": "earnings", "symbol": s, "retrieved_at": r.isoformat(), "raw_sha256": s, "status": "OK"} for s, r in (("LATE", T0 + timedelta(hours=2)), ("EMPTY", T0 - timedelta(hours=2)))]
    pl = ER.earnings_payloads(ev, lambda sha: {"data": [erow("2026-11-04", "2026-09-30")] if sha == "LATE" else []}, T0)
    assert set(pl) == {"LATE", "EMPTY"}                                                                                  # a payload retrieved after as_of is still reported, not silently dropped
    rows = confirmed("LATE", "2026-11-04") + confirmed("EMPTY", "2026-11-04") + confirmed("NONE", "2026-11-04")
    got = {r["symbol"]: r["quarter_reason"] for r in ER.records_from_saved(rows, T0, pl)}
    assert got == {"LATE": "PAYLOAD_RETRIEVED_AFTER_AS_OF", "EMPTY": "EMPTY_EARNINGS_PAYLOAD", "NONE": "NO_EARNINGS_PAYLOAD"}


def test_conflicting_earnings_payloads_for_the_same_report_date_are_ambiguous():
    ev = [{"kind": "earnings", "symbol": "AAA", "retrieved_at": (T0 - timedelta(days=1)).isoformat(), "raw_sha256": "old"},
          {"kind": "earnings", "symbol": "AAA", "retrieved_at": (T0 - timedelta(hours=2)).isoformat(), "raw_sha256": "new"}]
    pl = ER.earnings_payloads(ev, lambda sha: {"data": [erow("2026-11-04", "2026-09-30" if sha == "old" else "2026-06-30")]}, T0)
    r = ER.records_from_saved(confirmed("AAA", "2026-11-04"), T0, pl)[0]
    assert r["quarter_state"] == "AMBIGUOUS" and r["quarter_reason"] == "CONFLICTING_EARNINGS_PAYLOADS" and r["ending_fiscal_quarter"] == "QUARTER_UNKNOWN" and r["analyst_pairing_eligible"] is False
    same = ER.earnings_payloads(ev, lambda sha: {"data": [erow("2026-11-04", "2026-09-30")]}, T0)                        # agreeing payloads are not a conflict
    assert ER.records_from_saved(confirmed("AAA", "2026-11-04"), T0, same)[0]["quarter_state"] == "KNOWN"


def test_reschedule_inside_and_beyond_the_21_day_window():
    st = ER.EventRecords()
    st.observe(obs("AAA", confirmed("AAA", "2026-11-10")))
    r1 = st.observe(obs("AAA", confirmed("AAA", "2026-11-30", at=T0 + timedelta(hours=1)), as_of=T0 + timedelta(hours=2)))    # 20 days: linked, lineage kept
    assert r1["action"] == "RESCHEDULED" and r1["event_id"] == "AAA|UNKNOWN|2026-11-10"
    assert st.latest(r1["event_id"])["identity_basis"] == "DATE_PROXIMITY_HEURISTIC" and st.latest(r1["event_id"])["original_date"] == "2026-11-10"
    r2 = st.observe(obs("AAA", confirmed("AAA", "2027-01-05", at=T0 + timedelta(hours=3)), as_of=T0 + timedelta(hours=4)))     # 36 days from 11-30: a separate event
    assert r2["action"] == "NEW" and r2["event_id"] == "AAA|UNKNOWN|2027-01-05" and len(st.event_ids("AAA")) == 2


def test_two_distinct_known_quarters_close_together_are_never_merged_and_an_unknown_between_is_ambiguous():
    st = ER.EventRecords()
    st.observe(known_obs("CCC", "2026-11-20", "2026-09-30", T0))
    st.observe(known_obs("CCC", "2026-12-05", "2026-10-31", T0 + timedelta(hours=1)))
    assert st.event_ids("CCC") == ["CCC|2026-09-30", "CCC|2026-10-31"]
    amb = obs("CCC", confirmed("CCC", "2026-11-28", at=T0 + timedelta(hours=2)), as_of=T0 + timedelta(hours=3))           # within 21 days of BOTH
    r = st.observe(amb)
    assert r["action"] == "NEW" and r["event_id"] == "CCC|UNKNOWN|2026-11-28"
    v = st.latest(r["event_id"])
    assert v["identity_state"] == "AMBIGUOUS" and v["identity_candidates"] == ["CCC|2026-09-30", "CCC|2026-10-31"]
    assert v["prediction_eligible"] is False and "IDENTITY_AMBIGUOUS" in v["ineligible_reasons"] and v["analyst_pairing_eligible"] is False
    assert [len(st.history(i)) for i in ("CCC|2026-09-30", "CCC|2026-10-31")] == [1, 1]                                  # neither known event was touched
    assert st.observe(amb)["action"] == "UNCHANGED"                                                                      # repeating it does not churn versions


def test_a_known_quarter_is_not_silently_dropped_when_the_date_moves_and_the_new_row_is_missing():
    st = ER.EventRecords()
    st.observe(known_obs("BBB", "2026-11-10", "2026-09-30", T0))
    moved = obs("BBB", confirmed("BBB", "2026-11-17", at=T0 + timedelta(hours=1)), as_of=T0 + timedelta(hours=2), earnings_rows=[erow("2026-11-10", "2026-09-30")],
                earnings_retrieved_at=T0, earnings_sha256="c" * 64)
    r = st.observe(moved)
    v = st.latest(r["event_id"])
    assert r["action"] == "RESCHEDULED" and r["event_id"] == "BBB|2026-09-30" and v["quarter_state"] == "QUARTER_UNKNOWN" and v["analyst_pairing_eligible"] is False
    assert v["prior_version_quarter"] == "2026-09-30" and st.history("BBB|2026-09-30")[0]["ending_fiscal_quarter"] == "2026-09-30"
    again = known_obs("BBB", "2026-11-17", "2026-09-30", T0 + timedelta(hours=3))                                        # the provider later lists the new date: same event, quarter restored
    assert st.observe(again)["event_id"] == "BBB|2026-09-30"


def test_several_events_for_one_symbol_stay_separate_and_the_registry_is_only_a_projection(tmp_path):
    st = ER.EventRecords(tmp_path / "ev.jsonl")
    st.observe(known_obs("AAA", "2026-11-04", "2026-09-30", T0))
    st.observe(known_obs("AAA", "2027-02-10", "2026-12-31", T0 + timedelta(hours=1), kind="AMC"))
    reg = REG.EventRegistry(tmp_path / "reg.jsonl")
    ER.sync_registry(reg, st, T0 + timedelta(hours=2))
    assert st.event_ids("AAA") == ["AAA|2026-09-30", "AAA|2026-12-31"] and set(st.current()) == {"AAA|2026-09-30", "AAA|2026-12-31"}
    before = (tmp_path / "ev.jsonl").read_bytes()
    ER.sync_registry(reg, st, T0 + timedelta(hours=3))                                                                   # the projection never writes to the event store
    assert (tmp_path / "ev.jsonl").read_bytes() == before
    st.observe_snapshot([], observed=T0 + timedelta(days=5))                                                             # the first event's date passed: closed but kept
    assert st.latest("AAA|2026-09-30")["status"] == "OCCURRED" and st.latest("AAA|2026-12-31")["status"] == "CURRENT" and len(st.history("AAA|2026-09-30")) == 2
    st2 = ER.EventRecords()
    st2.observe(known_obs("ZZZ", "2026-11-12", "2026-09-30", T0))
    st2.observe_snapshot([], observed=T0 + timedelta(hours=4), window=(datetime(2026, 11, 1).date(), datetime(2026, 11, 30).date()), complete=True)
    assert st2.latest("ZZZ|2026-09-30")["status"] == "REMOVED" and st2.history("ZZZ|2026-09-30")[0]["status"] == "CURRENT"


def test_an_event_that_learned_its_quarter_is_matched_again_by_that_quarter_not_duplicated():
    """Found by the real-store report: unknown -> quarter learned (id stays SYMBOL|UNKNOWN|date) -> the next observation with the same known quarter must be the SAME event."""
    st = ER.EventRecords()
    r1 = st.observe(obs("AAA", confirmed("AAA", "2026-11-04")))
    r2 = st.observe(known_obs("AAA", "2026-11-04", "2026-09-30", T0 + timedelta(hours=1)))
    r3 = st.observe(known_obs("AAA", "2026-11-04", "2026-09-30", T0 + timedelta(hours=2)))
    assert r1["event_id"] == r2["event_id"] == r3["event_id"] == "AAA|UNKNOWN|2026-11-04" and r2["action"] == "UPDATED" and r3["action"] == "UNCHANGED"
    assert st.event_ids("AAA") == ["AAA|UNKNOWN|2026-11-04"] and st.latest(r1["event_id"])["ending_fiscal_quarter"] == "2026-09-30"
