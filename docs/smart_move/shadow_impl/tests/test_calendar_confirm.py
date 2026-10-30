"""Calendar verification (vendor level), synthetic observations only."""
from datetime import date, datetime, timedelta, timezone

import pytest

from smvshadow import calendar_confirm as cc

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
TODAY = date(2026, 11, 2)
D = date(2026, 11, 4)                        # 2 days ahead -> 6 h freshness limit


def uw(sym="AAA", d=D, label="premarket", flag="company", age_h=1, sha="u1", source="UW_CALENDAR"):
    row = {"symbol": sym, "report_date": d.isoformat(), "report_time": label, "date_confirmed": True}      # the app's hard-coded field is present and must be ignored
    if flag is not None:
        row["source"] = flag
    return cc.from_uw_row(row, NOW - timedelta(hours=age_h), sha, source)


def fz(sym="AAA", d=D, raw=None, age_h=1, sha="f1"):
    raw = raw or "%d/%d/%d 7:00:00 AM" % (d.month, d.day, d.year)
    return cc.from_finviz(sym, raw, NOW - timedelta(hours=age_h), sha)


def fh(sym="AAA", d=D, hour="bmo", age_h=1, sha="h1"):
    return cc.from_finnhub({"symbol": sym, "date": d.isoformat(), "hour": hour}, NOW - timedelta(hours=age_h), sha)


def ev(obs, **kw):
    return cc.evaluate("AAA", obs, NOW, TODAY, **kw)


def test_uw_flag_preserved_verbatim_and_app_date_confirmed_ignored():
    assert uw(flag="company").vendor_flag == "company" and uw(flag="estimation").vendor_flag == "estimation"
    o = uw(flag=None)                                                        # row has date_confirmed True but no source
    assert o.vendor_flag is None
    r = ev([o, fz()])
    assert r["status"] != cc.VENDOR_CONFIRMED and not r["eligible_for_shadow"] and "UW_FLAG_MISSING" in r["no_trade_reasons"]


def test_vendor_confirmed_requires_company_flag_second_vendor_date_timing_fresh_and_documented_dependency():
    r = ev([uw(), fz()])
    assert r["status"] == cc.VENDOR_CONFIRMED and r["eligible_for_shadow"] and r["kind"] == "BMO" and r["no_trade_reasons"] == []
    assert "VENDOR_AGREEMENT_NOT_COMPANY_CONFIRMATION" in r["reasons"] and r["status"] != cc.COMPANY_VERIFIED
    assert r["dependency"]["status"] == "UNPROVEN_INDEPENDENCE" and r["dependency"]["note"]
    assert ev([uw(), fh()])["status"] == cc.VENDOR_CONFIRMED                   # Finnhub partner works too
    assert ev([uw(label="postmarket"), fz(raw="11/4/2026 4:30:00 PM")])["kind"] == "AMC"


@pytest.mark.parametrize("obs,reason", [
    ([uw(flag="estimation"), fz()], "SOURCE_ESTIMATION"),
    ([uw()], "SINGLE_VENDOR"),
    ([fz()], "SINGLE_VENDOR"),
    ([uw(source="UW_CALENDAR", sha="a"), uw(source="UW_ROW", sha="b")], "SINGLE_VENDOR"),          # two Unusual Whales feeds are one vendor
    ([uw(sha="same"), fz(sha="same")], "SHARED_PAYLOAD"),                                           # identical bytes cannot be two vendors
    ([uw(), fz(raw="11/4/2026 11:00:00 AM")], "UNKNOWN_TIMING"),                                     # clock inside trading hours
    ([uw(), fz(raw="11/4/2026")], "UNKNOWN_TIMING"),                                                 # date without time
    ([uw(label="premarket"), fz(raw="11/4/2026 4:30:00 PM")], "TIMING_CONTRADICTED"),
    ([uw(label=None), fz()], "UNKNOWN_TIMING"),
])
def test_not_vendor_confirmed_cases_record_a_no_trade_reason(obs, reason):
    r = ev(obs)
    assert r["status"] not in cc.ELIGIBLE and not r["eligible_for_shadow"] and reason in r["no_trade_reasons"]


def test_date_conflicts():
    one = ev([uw(), fz(d=D + timedelta(days=1), raw="11/5/2026 7:00:00 AM")])
    assert one["status"] == cc.ESTIMATED and "DATE_DISAGREEMENT_1_DAY" in one["no_trade_reasons"]
    many = ev([uw(), fz(d=D + timedelta(days=3), raw="11/7/2026 7:00:00 AM")])
    assert many["status"] == cc.UNKNOWN and many["no_trade_reasons"] == ["DATE_CONFLICT_OVER_1_DAY"] and many["event_date"] is None


def test_freshness_both_observations_must_be_fresh_calendar_clock_only():
    r = ev([uw(age_h=1), fz(age_h=30)])                                       # partner stale (limit 6 h inside 4 days)
    assert r["status"] == cc.ESTIMATED and "PARTNER_OBSERVATION_STALE" in r["no_trade_reasons"]
    s = ev([uw(age_h=30), fz(age_h=30)])
    assert s["status"] == cc.STALE and "STALE_CALENDAR" in s["no_trade_reasons"]
    far = date(2026, 11, 20)                                                  # 18 days ahead: 24 h limit
    assert ev([uw(d=far, age_h=20), fz(d=far, raw="11/20/2026 7:00:00 AM", age_h=20)])["status"] == cc.VENDOR_CONFIRMED
    assert ev([uw(d=far, age_h=26), fz(d=far, raw="11/20/2026 7:00:00 AM", age_h=26)])["status"] == cc.STALE


def test_calendar_observed_at_is_separate_from_prediction_snapshot_at():
    old_snapshot = NOW - timedelta(days=8)
    r = ev([uw(), fz()], prediction_snapshot_at=old_snapshot)
    assert r["status"] == cc.VENDOR_CONFIRMED and r["eligible_for_shadow"]       # an 8-day-old prediction snapshot does not make the calendar stale
    assert r["prediction_snapshot_at"] == old_snapshot and r["calendar_observed_at"] == NOW - timedelta(hours=1) and r["calendar_observed_at"] != r["prediction_snapshot_at"]
    s = ev([uw(age_h=30), fz(age_h=30)], prediction_snapshot_at=NOW - timedelta(minutes=5))
    assert s["status"] == cc.STALE                                              # a fresh prediction snapshot does not rescue a stale calendar


def test_moved_events():
    prior = D - timedelta(days=2)
    est = ev([uw(flag="estimation"), fz()], prior_event_date=prior)
    assert est["status"] == cc.MOVED and "MOVED_UNCONFIRMED" in est["no_trade_reasons"] and est["moved_from"] == prior
    ok = ev([uw(), fz()], prior_event_date=prior, prior_prediction_exists=True)
    assert ok["status"] == cc.VENDOR_CONFIRMED and ok["moved_from"] == prior and "MOVED_AFTER_PREDICTION" in ok["reasons"]


def test_undocumented_dependency_blocks_vendor_confirmation():
    r = ev([uw(), fz()], dependency={})
    assert r["status"] == cc.ESTIMATED and "DEPENDENCY_UNDOCUMENTED" in r["no_trade_reasons"]
    r2 = ev([uw(), fz()], dependency={frozenset(["UNUSUAL_WHALES", "FINVIZ"]): {"status": "SHARED_UPSTREAM_SUSPECTED", "note": "documented"}})
    assert r2["status"] == cc.VENDOR_CONFIRMED and r2["dependency"]["status"] == "SHARED_UPSTREAM_SUSPECTED"        # documented shared dependence is carried, not hidden


def test_dependency_evidence_never_claims_independence():
    same = cc.dependency_evidence([("A", D, {"UW": D, "FZ": D})] * 50)
    assert same["disagreements"] == 0 and "UNPROVEN" in same["reading"]
    diff = cc.dependency_evidence([("A", D, {"UW": D, "FZ": D + timedelta(days=1)})])
    assert diff["disagreements"] == 1


def test_company_verified_is_separate_and_contradicting_company_evidence_wins():
    good = {"date": D, "session": "BMO", "sha256": "c" * 64, "retrieved_at": NOW - timedelta(hours=2), "url": "synthetic"}
    r = ev([uw(flag="estimation")], company_evidence=good)
    assert r["status"] == cc.COMPANY_VERIFIED and r["eligible_for_shadow"] and r["confidence"] == cc.COMPANY_VERIFIED
    bad = dict(good, date=D + timedelta(days=1))
    r2 = ev([uw(), fz()], company_evidence=bad)
    assert r2["status"] == cc.UNKNOWN and "COMPANY_EVIDENCE_CONTRADICTS_VENDORS" in r2["no_trade_reasons"]
    r3 = ev([uw(label="postmarket"), fz(raw="11/4/2026 4:30:00 PM")], company_evidence=good)       # company says BMO, vendors say AMC
    assert not r3["eligible_for_shadow"] and "TIMING_CONTRADICTED" in r3["no_trade_reasons"]


def test_every_ineligible_result_has_a_reason_and_eligible_has_none():
    cases = [[], [uw()], [uw(flag="estimation"), fz()], [uw(age_h=40), fz(age_h=40)], [uw(), fz(raw="11/4/2026 4:30:00 PM")], [uw(), fz()]]
    for obs in cases:
        r = ev(obs)
        assert (r["eligible_for_shadow"] and r["no_trade_reasons"] == []) or (not r["eligible_for_shadow"] and r["no_trade_reasons"]), r


def test_observation_log_is_append_only_idempotent_and_tamper_evident():
    log = cc.ObservationLog()
    a, b = uw(), fz()
    assert log.append(a) and log.append(b) and not log.append(a) and len(log) == 2
    assert log.verify() and not hasattr(log, "update") and not hasattr(log, "delete")
    head = log.head()
    log._rows[0] = (cc.CalObs(**{**a.__dict__, "vendor_flag": "estimation"}), log._rows[0][1])             # tamper with the stored flag
    assert not log.verify() and head != "0" * 64
