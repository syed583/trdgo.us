"""Phase 13 offline audits: per-event news quality and earnings-outcome validation. Synthetic fixtures only; no provider, no network, no file outside tmp_path."""
import copy
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_news_audit as NA  # noqa: E402
import smart_move_outcome_audit as OA  # noqa: E402

UTC = timezone.utc
CUT = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)                           # BMO 2026-11-04: cutoff = release (11:00Z) - 24 h
RET = CUT - timedelta(hours=10)


def head(text, hours_before_cut=30, src="GlobeNewswire", **kw):
    return dict({"headline": text, "created_at": (CUT - timedelta(hours=hours_before_cut)).isoformat().replace("+00:00", "Z"), "source": src, "sentiment": "neutral", "tickers": ["ACME"]}, **kw)


def rec(items, at=RET, rid="r1", status="OK", sha=None):
    return {"record_id": rid, "symbol": "ACME", "retrieved_at": at.isoformat(), "raw_sha256": sha or hashlib.sha256(rid.encode()).hexdigest(), "status": status, "rows": len(items), "headlines": items}


EV = {"event_id": "ACME|2026-09-30", "symbol": "ACME", "event_date": "2026-11-04", "session": "BMO", "cutoff_utc": CUT.isoformat(), "ending_fiscal_quarter": "2026-09-30", "quarter_state": "KNOWN"}


def one(h, at=RET, company="ACME ROBOTICS INC", ev=EV):
    r = NA.audit_event(ev, [rec([h], at)], company)
    return r["headlines"][0]


# ---------------------------------------------------------------- news: the six classes
def test_a_fresh_pre_cutoff_trusted_headline_naming_the_company_is_valid():
    c = one(head("Acme Robotics announces new warehouse robot line"))
    assert c["class"] == "VALID" and c["reason"].startswith("PRE_CUTOFF")


def test_a_headline_older_than_seven_days_at_the_cutoff_is_stale():
    assert one(head("Acme Robotics expands plant", hours_before_cut=24 * 8))["class"] == "STALE"
    assert one(head("Acme Robotics expands plant", hours_before_cut=24 * 6))["class"] == "VALID"


def test_repeats_are_duplicates_of_the_earliest_retrieved_copy_across_records():
    a = rec([head("Acme Robotics wins a contract")], RET - timedelta(hours=5), "r1")
    b = rec([head("ACME ROBOTICS wins a contract!")], RET, "r2")                      # same text after normalisation, retrieved later
    r = NA.audit_event(EV, [b, a], "ACME ROBOTICS INC")
    cls = {h["record_id"]: h["class"] for h in r["headlines"]}
    assert cls == {"r1": "VALID", "r2": "DUPLICATE"} and r["counts"]["DUPLICATE"] == 1 and r["headlines"][1]["duplicate_of"]["retrieved_at"] == a["retrieved_at"]


def test_ticker_collisions_and_wrong_company_associations_are_irrelevant():
    other = one(head("Acmeco Holdings raises dividend"))                              # a similar but different company name
    assert other["class"] == "IRRELEVANT"
    nothing = one(head("Global chip makers rally on demand"))                          # neither the name nor the cashtag
    assert nothing["class"] == "IRRELEVANT" and "ENTITY_MISMATCH" in nothing["reason"]
    excl = one(head("Acme Robotics opens an office", tickers=["XYZ"]))                # the provider's own ticker list excludes the symbol
    assert excl["class"] == "IRRELEVANT" and excl["reason"] == "PROVIDER_TICKERS_EXCLUDE_SYMBOL"
    assert one(head("Insider revealed transactions at Acme Robotics"))["class"] == "IRRELEVANT"
    assert one(head("Acme Robotics shares hit record high"))["class"] == "IRRELEVANT"


def test_unverifiable_availability_or_source_is_unverified_never_valid():
    assert one(head("Acme Robotics ships units"), at=CUT + timedelta(hours=2))["reason"] == "PAGE_RETRIEVED_AFTER_CUTOFF"      # published before the cutoff, seen only after it
    assert one(head("Acme Robotics ships units", src="Some Blog"))["reason"] == "UNKNOWN_SOURCE"
    assert one(head("Acme Robotics ships units", src="Blockchain News"))["reason"] == "LOW_CREDIBILITY_SOURCE"
    assert one(head("Acme Robotics ships units"), company=None)["class"] == "UNVERIFIED"                                       # name unknown and no cashtag: not checkable
    assert one(head("$ACME ships units"), company=None)["class"] == "VALID"


def test_invalid_headlines_are_never_used():
    assert one(head(""))["reason"] == "BLANK_HEADLINE"
    assert one(dict(head("Acme Robotics x"), created_at="not a time"))["reason"] == "MISSING_OR_UNPARSEABLE_TIMESTAMP"
    assert one(head("Acme Robotics x", hours_before_cut=-1))["reason"] == "PUBLISHED_AT_OR_AFTER_CUTOFF"
    assert one(head("Acme Robotics x", hours_before_cut=5), at=CUT - timedelta(hours=8))["reason"] == "PUBLISHED_AFTER_IT_WAS_RETRIEVED"


# ---------------------------------------------------------------- news: missing vs empty, quarter relevance, integrity
def test_missing_news_failed_news_and_empty_responses_are_distinct_states():
    assert NA.audit_event(EV, [], "ACME")["news_state"] == "NOT_COLLECTED"
    assert NA.audit_event(EV, [rec([], status="FAILED")], "ACME")["news_state"] == "FAILED_RESPONSES_ONLY"
    assert NA.audit_event(EV, [rec([])], "ACME")["news_state"] == "EMPTY_PROVIDER_RESPONSE"
    assert NA.audit_event(EV, [rec([head("Global chip makers rally")])], "ACME ROBOTICS INC")["news_state"] == "NO_VALID_HEADLINES"
    assert NA.audit_event(EV, [rec([head("Acme Robotics ships units")])], "ACME ROBOTICS INC")["news_state"] == "HAS_VALID_HEADLINES"
    assert NA.audit_event(dict(EV, cutoff_utc=None), [rec([head("x")])], "ACME")["news_state"] == "NO_CUTOFF_FOR_EVENT"


def test_quarter_relevance_never_derives_a_quarter_number_from_dates():
    known = one(head("Acme Robotics to report Third Quarter 2026 results"))["flags"]
    assert known["relevance"] == "YEAR_COMPATIBLE_WITH_EVENT_PERIOD"
    old = one(head("Acme Robotics reports Q3 2024 results"))["flags"]
    assert old["relevance"] == "YEAR_DIFFERS_FROM_EVENT_PERIOD"
    unk = one(head("Acme Robotics to report Third Quarter 2026 results"), ev=dict(EV, quarter_state="QUARTER_UNKNOWN", ending_fiscal_quarter="QUARTER_UNKNOWN"))["flags"]
    assert unk["relevance"] == "NOT_CHECKABLE_QUARTER_UNKNOWN"
    assert one(head("Acme Robotics hires a new CFO"))["flags"]["relevance"] == "NO_PERIOD_MENTION"


def test_the_audit_does_not_modify_its_inputs_or_use_provider_sentiment():
    records = [rec([head("Acme Robotics ships units"), head("Acme Robotics ships units")])]
    before = copy.deepcopy(records)
    out = NA.audit({"event_id": "x"} and [EV], records, {"ACME": "ACME ROBOTICS INC"})
    assert records == before and out["summary"]["provider_sentiment"] == {"neutral": 2}
    assert out["summary"]["by_class"] == {"VALID": 1, "DUPLICATE": 1} and out["events"][0]["valid"] == 1
    assert out["summary"]["news_records_for_symbols_with_no_event"] == []
    assert "NEWS_GUIDANCE" not in json.dumps({k: v for k, v in out["summary"].items() if k != "note"})


# ---------------------------------------------------------------- outcomes: rules against an independent oracle
def test_session_rules_agree_with_the_independent_oracle_across_holidays_weekends_and_early_closes():
    r = OA.rule_audit()
    assert r["session_mismatches"] == [] and r["passed"] is True and r["days_checked"] > 200 and all(e["is_13_00_ET"] for e in r["early_closes"]) and len(r["early_closes"]) >= 2


def test_specific_sessions_around_thanksgiving_weekends_and_year_end():
    cases = {(date(2026, 11, 25), "AMC"): (date(2026, 11, 25), date(2026, 11, 27)),       # Thanksgiving 11-26 closed: reaction is the early-close Friday
             (date(2026, 11, 27), "AMC"): (date(2026, 11, 27), date(2026, 11, 30)),       # Friday AMC -> Monday
             (date(2026, 11, 28), "AMC"): (date(2026, 11, 27), date(2026, 11, 30)),       # a Saturday date: last session before, next session after
             (date(2026, 11, 27), "BMO"): (date(2026, 11, 25), date(2026, 11, 27)),
             (date(2026, 12, 24), "AMC"): (date(2026, 12, 24), date(2026, 12, 28)),       # 12-25 closed, 12-26/27 weekend
             (date(2026, 11, 2), "BMO"): (date(2026, 10, 30), date(2026, 11, 2))}         # Monday BMO: base is Friday
    for (d, k), exp in cases.items():
        assert OA.oracle_sessions(d, k) == exp
        from smvshadow import cutoff as co
        assert (co.base_session(d, k), co.reaction_session(d, k)) == exp


def test_the_flat_band_is_half_a_percent_inclusive_and_splits_are_quarantined_not_graded():
    r = OA.rule_audit()
    assert r["flat_band"]["grader_flat_band"] == 0.5 and r["flat_band"]["boundary"] == {"0.5": "FLAT", "-0.5": "FLAT", "0.5001": "UP", "-0.5001": "DOWN", "0.0": "FLAT"}
    assert r["split_quarantine_examples"]["2_for_1"]["kind"] == "SUSPECTED_FORWARD_SPLIT_1_FOR_2" and r["split_quarantine_examples"]["ordinary_move"] is None


# ---------------------------------------------------------------- outcomes: per-event validation
def bars(rows):
    return [{"retrieved_at": at.isoformat(), "raw_sha256": hashlib.sha256(str(i).encode()).hexdigest(), "rows": [
        {"date": d, "open": o, "high": o, "low": o, "close": c, "volume": 1000, "market_time": "r"} for d, o, c in rs]} for i, (at, rs) in enumerate(rows)]


NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
OEV = {"event_id": "ACME|2026-09-30", "symbol": "ACME", "event_date": "2026-11-04", "session": "BMO", "cutoff_utc": "2026-11-03T11:00:00+00:00"}       # BMO: base 11-03, reaction 11-04
AFTER = datetime(2026, 11, 4, 22, 0, tzinfo=UTC)


def pred(at):
    return {"symbol": "ACME", "earnings_date": "2026-11-04", "captured_at": at.isoformat(), "engine": {"continuation": {"signal": "BUY"}}}


def test_outcome_is_computed_only_from_saved_bars_and_classified_with_the_frozen_band():
    for close, cls in ((101.0, "UP"), (100.4, "FLAT"), (98.0, "DOWN"), (100.5, "FLAT")):
        r = OA.audit_event(OEV, bars([(AFTER, [("2026-11-03", 100.0, 100.0), ("2026-11-04", 100.0, close)])]), [pred(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))], NOW)
        assert r["state"] == "OUTCOME_COMPUTABLE" and r["outcome_class"] == cls and r["sessions_match_oracle"] is True and r["prediction_validity"] == "PREDICTION_AT_OR_BEFORE_CUTOFF"


def test_missing_pending_and_premature_bars_are_never_turned_into_outcomes():
    assert OA.audit_event(OEV, [], [], NOW)["state"] == "NO_PRICE_EVIDENCE_SAVED"
    only_base = bars([(AFTER, [("2026-11-03", 100.0, 100.0)])])
    r = OA.audit_event(OEV, only_base, [], NOW)
    assert r["state"] == "MISSING_BARS" and r["missing"] == ["reaction"] and r["outcome_class"] is None
    early = bars([(datetime(2026, 11, 4, 15, 0, tzinfo=UTC), [("2026-11-03", 100.0, 100.0), ("2026-11-04", 100.0, 110.0)])])           # retrieved before the 21:00Z close + 30 min
    assert OA.audit_event(OEV, early, [], NOW)["state"] == "BARS_RETRIEVED_BEFORE_REACTION_SESSION_CLOSED"
    assert OA.audit_event(OEV, only_base, [], datetime(2026, 11, 4, 12, 0, tzinfo=UTC))["state"] == "PENDING_REACTION_SESSION_NOT_CLOSED"


def test_a_split_shaped_move_is_quarantined_and_has_no_class():
    r = OA.audit_event(OEV, bars([(AFTER, [("2026-11-03", 100.0, 100.0), ("2026-11-04", 50.0, 50.0)])]), [], NOW)
    assert r["state"] == "QUARANTINED_CORPORATE_ACTION" and r["outcome_class"] is None and r["raw_move_pct"] == -50.0


def test_a_prediction_captured_after_the_cutoff_cannot_support_accuracy_measurement():
    b = bars([(AFTER, [("2026-11-03", 100.0, 100.0), ("2026-11-04", 100.0, 103.0)])])
    late = OA.audit_event(OEV, b, [pred(datetime(2026, 11, 4, 18, 0, tzinfo=UTC))], NOW)
    none = OA.audit_event(OEV, b, [], NOW)
    ok = OA.audit_event(OEV, b, [pred(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))], NOW)
    assert (late["prediction_validity"], none["prediction_validity"], ok["prediction_validity"]) == ("PREDICTION_CAPTURED_AFTER_CUTOFF", "NO_PREDICTION", "PREDICTION_AT_OR_BEFORE_CUTOFF")
    out = OA.audit([OEV, dict(OEV, event_id="B|x", symbol="B")], {"ACME": b}, {("ACME", "2026-11-04"): [pred(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))]}, NOW)
    assert out["summary"]["outcome_computable"] == 1 and out["summary"]["usable_for_accuracy_measurement"] == 1


def test_unknown_timing_uses_agreeing_saved_vendor_labels_only_for_reading_the_outcome():
    ev = dict(OEV, session="UNKNOWN", cutoff_utc=None, vendors=[{"label": "premarket", "clock": None}, {"label": "bmo", "clock": None}, {"label": None, "clock": "8:30 AM"}])
    b = bars([(AFTER, [("2026-11-03", 100.0, 100.0), ("2026-11-04", 100.0, 102.0)])])
    r = OA.audit_event(ev, b, [pred(datetime(2026, 11, 5, 12, 0, tzinfo=UTC))], NOW)
    assert r["state"] == "OUTCOME_COMPUTABLE" and r["timing_basis"] == "VENDOR_LABEL_ONLY" and r["prediction_validity"] == "PREDICTION_CAPTURED_AFTER_CUTOFF"
    out = OA.audit([ev], {"ACME": b}, {("ACME", "2026-11-04"): [pred(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))]}, NOW)
    assert out["summary"]["usable_for_accuracy_measurement"] == 0                                                  # vendor-label timing never qualifies a prediction
    conflict = dict(ev, vendors=[{"label": "premarket"}, {"label": "postmarket"}])
    assert OA.audit_event(conflict, b, [], NOW)["state"] == "UNKNOWN_TIMING_NO_OUTCOME_RULE"
    assert OA.vendor_session_hint([{"label": None, "clock": "4:05 PM"}]) == "AMC" and OA.vendor_session_hint([]) is None


# ---------------------------------------------------------------- boundaries
def test_frozen_files_are_unchanged_and_the_audit_modules_import_no_writer_or_provider_code():
    h = lambda p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # noqa: E731
    assert h("backend/smart_move_outcomes.py").startswith("0572074c4d0cf81f") and h("docs/smart_move/shadow_impl/smvshadow/grader.py").startswith("70078fb2996fc7a6")
    assert h("backend/smart_move_news.py").startswith("1121048777e55273")
    for name in ("smart_move_news_audit.py", "smart_move_outcome_audit.py"):
        src = (ROOT / "backend" / name).read_text(encoding="utf-8")
        assert not [ln for ln in src.splitlines() if ln.startswith(("import ", "from ")) and any(w in ln for w in ("requests", "httpx", "urllib", "socket", "uw_client", "smart_move_live", "smart_move_v2 "))]
        assert "O.record(" not in src and "write_text" not in src and ".open(\"a\"" not in src
