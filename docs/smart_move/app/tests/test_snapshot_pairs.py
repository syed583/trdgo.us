"""Snapshot A/B pairing bound to event records (Phase 13, Priority 2). Synthetic fixtures only; no provider, no network."""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_event_records as ER  # noqa: E402
import smart_move_snapshot_pairs as SP  # noqa: E402

UTC = timezone.utc
EVENT = "2026-11-12"                                                      # AMC: release 21:00Z, window opens 11-10 21:00Z, cutoff 11-11 21:00Z
B_AT = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
A_AT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)                          # 14 days before B, inside the A range (release-67 d .. release-9 d)
SHA = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def cal(sym, date, at):
    return [{"source": "UW_CALENDAR", "symbol": sym, "date": date, "label": "postmarket", "clock": None, "vendor_flag": "company", "raw_sha256": SHA("u" + sym + date), "observed_at": at.isoformat(), "request_id": "r1", "mode": "LIVE"},
            {"source": "FINNHUB", "symbol": sym, "date": date, "label": "amc", "clock": None, "vendor_flag": None, "raw_sha256": SHA("f" + sym + date), "observed_at": at.isoformat(), "request_id": "r2", "mode": "LIVE"}]


def records(sym="AAA", date=EVENT, quarter="2026-09-30", observed=B_AT - timedelta(minutes=20), payload_at=None):
    st = ER.EventRecords()
    rows = cal(sym, date, observed - timedelta(minutes=5))
    er = [{"source": "company", "report_date": date, "ending_fiscal_quarter": quarter, "report_time": "postmarket"}] if quarter else None
    st.observe(ER.build_observation(sym, ER.calobs_from_rows(rows)[sym], observed, earnings_rows=er, earnings_retrieved_at=payload_at or observed - timedelta(hours=1), earnings_sha256=SHA("earn") if er else None))
    return st


def est(sym, at, eps, rev, quarter="2026-09-30", status="OK", tag=None):
    sha = SHA("est%s%s%s%s" % (sym, at.isoformat(), eps, tag))
    return {"kind": "uw_estimates", "symbol": sym, "retrieved_at": at.isoformat(), "raw_sha256": sha, "status": status, "_rows": [
        {"horizon": "fiscal quarter", "date": quarter, "eps_estimate_average": str(eps), "revenue_estimate_average": str(rev)},
        {"horizon": "fiscal year", "date": "2026-12-31", "eps_estimate_average": "9", "revenue_estimate_average": "9"}]}


def loader(recs):
    m = {r["raw_sha256"]: r["_rows"] for r in recs}
    return lambda sha: m.get(sha)


def run(recs_est, st=None, as_of=B_AT + timedelta(hours=1), sym="AAA"):
    st = st or records(sym)
    return SP.pair_event(st, st.event_ids()[0], recs_est, loader(recs_est), as_of)


# ---------------------------------------------------------------- the pairing
def test_a_valid_pair_is_bound_to_the_event_version_quarter_and_provider_values():
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    v = run([a, b])
    assert v["state"] == "PAIRED" and v["reasons"] == [] and v["ending_fiscal_quarter"] == "2026-09-30" and v["event_version"] == 1
    assert v["snapshot_a"]["raw_sha256"] == a["raw_sha256"] and v["snapshot_b"]["raw_sha256"] == b["raw_sha256"] and v["snapshot_a"]["period"] == v["snapshot_b"]["period"] == "2026-09-30"
    assert v["gap_days"] == 14 and v["eps_revision_pct"] == 10.0 and v["revenue_revision_pct"] == 5.0 and v["event_version_at_b"] == 1
    assert v["release_time_verified"] is False and v["release_basis"] == "NOMINAL_AMC" and v["provider_fields"]["eps"] == "eps_estimate_average"


def test_the_fiscal_year_row_is_never_used_only_the_event_quarter_row():
    a = est("AAA", A_AT, 1.00, 100, quarter="2026-06-30")                                   # a different fiscal quarter
    b = est("AAA", B_AT, 1.10, 105)
    v = run([a, b])
    assert v["state"] == "UNPAIRED" and v["reasons"] == ["NO_SNAPSHOT_A_IN_RANGE"] and {"reason": "NO_ROW_FOR_EVENT_QUARTER", "raw_sha256": a["raw_sha256"], "retrieved_at": a["retrieved_at"]} in v["excluded_payloads"]


def test_snapshot_b_must_be_inside_the_window_and_before_the_cutoff():
    a = est("AAA", A_AT, 1.00, 100)
    early = est("AAA", datetime(2026, 11, 10, 20, 0, tzinfo=UTC), 1.1, 105)                 # before the window opens
    late = est("AAA", datetime(2026, 11, 11, 22, 0, tzinfo=UTC), 1.2, 106)                  # after the cutoff
    v = run([a, early, late], as_of=datetime(2026, 11, 12, 10, 0, tzinfo=UTC))
    assert v["state"] == "UNPAIRED" and v["reasons"] == ["NO_SNAPSHOT_B_IN_WINDOW"] and any(x["reason"] == "RETRIEVED_AFTER_CUTOFF_OR_AS_OF" for x in v["excluded_payloads"])


def test_nothing_retrieved_after_as_of_is_visible():
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    assert run([a, b], as_of=B_AT - timedelta(minutes=1))["reasons"] == ["NO_SNAPSHOT_B_IN_WINDOW"]


def test_gap_and_range_rules_for_snapshot_a():
    b = est("AAA", B_AT, 1.10, 105)
    assert run([est("AAA", B_AT - timedelta(days=3), 1.0, 100), b])["reasons"] == ["NO_SNAPSHOT_A_IN_RANGE"]            # 3 days before B is inside release-9 d: not an A
    assert run([est("AAA", B_AT - timedelta(days=62), 1.0, 100), b])["reasons"] == ["GAP_A_TO_B_NOT_7_TO_60_DAYS"]      # inside the A range but 62 days before B (> 60)
    assert run([est("AAA", B_AT - timedelta(days=70), 1.0, 100), b])["reasons"] == ["NO_SNAPSHOT_A_IN_RANGE"]            # older than the A lookback
    assert run([b])["reasons"] == ["NO_SNAPSHOT_A_IN_RANGE"]
    near, far = est("AAA", B_AT - timedelta(days=8), 1.05, 101), est("AAA", B_AT - timedelta(days=20), 1.0, 100)
    assert run([far, near, b])["snapshot_a"]["raw_sha256"] == near["raw_sha256"]                                         # the latest A that still leaves a measurable gap


def test_failed_and_duplicate_payloads_are_excluded_not_paired():
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    failed = est("AAA", A_AT + timedelta(days=1), 9, 9, status="FAILED")
    dup = dict(a, retrieved_at=(A_AT + timedelta(days=2)).isoformat())
    v = run([a, failed, dup, b])
    reasons = {x["reason"] for x in v["excluded_payloads"]}
    assert v["state"] == "PAIRED" and v["snapshot_a"]["raw_sha256"] == a["raw_sha256"] and {"STATUS_FAILED", "DUPLICATE_PAYLOAD"} <= reasons


# ---------------------------------------------------------------- eligibility of the event itself
def test_unknown_ambiguous_or_late_quarters_are_not_pairable():
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    unknown = run([a, b], st=records(quarter=None))
    assert unknown["state"] == "NOT_PAIRABLE" and "QUARTER_NOT_KNOWN:QUARTER_UNKNOWN" in unknown["reasons"]
    late = records(payload_at=datetime(2026, 11, 11, 23, 0, tzinfo=UTC), observed=datetime(2026, 11, 11, 23, 30, tzinfo=UTC))        # quarter payload retrieved after the cutoff
    v = SP.pair_event(late, late.event_ids()[0], [a, b], loader([a, b]), datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
    assert v["state"] == "NOT_PAIRABLE" and v["reasons"]


def test_an_event_that_was_not_known_eligible_when_b_was_taken_is_not_paired():
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    st = records(observed=B_AT + timedelta(minutes=30), payload_at=B_AT)                    # the event record only exists after B was retrieved
    v = SP.pair_event(st, st.event_ids()[0], [a, b], loader([a, b]), B_AT + timedelta(hours=2))
    assert v["state"] == "UNPAIRED" and v["reasons"] == ["EVENT_NOT_PAIRING_ELIGIBLE_WHEN_B_WAS_TAKEN"]


def test_a_removed_or_ambiguous_event_is_not_pairable():
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    st = records()
    st.observe_snapshot([], observed=B_AT + timedelta(minutes=5), window=(datetime(2026, 11, 1).date(), datetime(2026, 11, 30).date()), complete=True)
    v = SP.pair_event(st, st.event_ids()[0], [a, b], loader([a, b]), B_AT + timedelta(hours=1))
    assert v["state"] == "NOT_PAIRABLE" and "EVENT_REMOVED" in v["reasons"]
    assert SP.pair_event(st, "ZZZ|2026-09-30", [], lambda s: None, B_AT)["reasons"] == ["EVENT_UNKNOWN_AT_AS_OF"]


def test_the_version_at_a_and_at_b_are_both_recorded_after_a_reschedule():
    st = records(observed=A_AT - timedelta(hours=1), payload_at=A_AT - timedelta(hours=2))
    rows = cal("AAA", "2026-11-12", B_AT - timedelta(minutes=25))
    rows += [dict(r, observed_at=(B_AT - timedelta(minutes=5)).isoformat(), raw_sha256=SHA(r["raw_sha256"] + "x")) for r in rows]   # unchanged content, newer observation: no new version
    st.observe(ER.build_observation("AAA", ER.calobs_from_rows(rows)["AAA"], B_AT - timedelta(minutes=2), earnings_rows=[{"source": "company", "report_date": "2026-11-12", "ending_fiscal_quarter": "2026-09-30"}],
                                    earnings_retrieved_at=A_AT - timedelta(hours=2), earnings_sha256=SHA("earn")))
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    v = SP.pair_event(st, st.event_ids()[0], [a, b], loader([a, b]), B_AT + timedelta(hours=1))
    assert v["state"] == "PAIRED" and v["event_version_at_b"] == v["event_version"] and v["event_version_at_a"] is not None


# ---------------------------------------------------------------- store and boundaries
def test_pair_store_is_append_only_idempotent_and_hashed(tmp_path):
    a, b = est("AAA", A_AT, 1.00, 100), est("AAA", B_AT, 1.10, 105)
    st = records()
    ps = SP.PairStore(tmp_path / "pairs.jsonl")
    out = SP.pair_all(st, [a, b], loader([a, b]), B_AT + timedelta(hours=1), ps)
    assert out["counts"] == {"PAIRED": 1}
    again = SP.pair_all(st, [a, b], loader([a, b]), B_AT + timedelta(hours=1), ps)
    assert again["counts"] == {"PAIRED": 1} and len(SP.PairStore(tmp_path / "pairs.jsonl").rows) == 1
    row = json.loads((tmp_path / "pairs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["pair_hash"] == SP.pair_hash(row)


def test_the_module_imports_no_scoring_provider_or_network_code_and_frozen_files_are_unchanged():
    src = (ROOT / "backend" / "smart_move_snapshot_pairs.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if any(w in ln for w in ("smart_move_v2 ", "scorer", "uw_client", "requests", "httpx", "urllib", "socket", "smart_move_live", "smart_move_outcomes"))]
    assert "smart_move_v2_collect" not in src
    assert hashlib.sha256((ROOT / "backend" / "smart_move_v2.py").read_bytes()).hexdigest().startswith("de2d8f9612934338")


# ---------------------------------------------------------------- audit corrections
def test_a_pair_without_any_measurable_revision_is_not_reported_as_paired():
    b = est("AAA", B_AT, 1.10, 105)
    zero = est("AAA", A_AT, 0, 0)                                                            # baseline of zero: no percentage exists
    none = dict(est("AAA", A_AT + timedelta(days=1), 1, 1, tag="n"), _rows=[{"horizon": "fiscal quarter", "date": "2026-09-30", "eps_estimate_average": None, "revenue_estimate_average": None}])
    assert run([zero, b])["reasons"] == ["NO_MEASURABLE_REVISION"] and run([none, b])["state"] == "UNPAIRED"
    assert run([zero, est("AAA", A_AT + timedelta(days=1), 1.0, 100, tag="ok"), b])["state"] == "PAIRED"   # a later usable A is preferred over an unusable one


def test_conflicting_rows_for_the_event_quarter_exclude_the_payload():
    b = est("AAA", B_AT, 1.10, 105)
    a = est("AAA", A_AT, 1.0, 100)
    a["_rows"] = a["_rows"] + [{"horizon": "fiscal quarter", "date": "2026-09-30", "eps_estimate_average": "2.0", "revenue_estimate_average": "100"}]
    v = run([a, b])
    assert v["state"] == "UNPAIRED" and any(x["reason"] == "CONFLICTING_ROWS_FOR_EVENT_QUARTER" for x in v["excluded_payloads"])
