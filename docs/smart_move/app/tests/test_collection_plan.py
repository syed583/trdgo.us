"""Collection plan (news passes, budgets, snapshot schedule), stored-headline quality and the news validation gate. Planning and fixtures only: no provider, no store write."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_collection_plan as CP  # noqa: E402
import smart_move_collection_plan_run as RUN  # noqa: E402
import smart_move_news_quality as NQ  # noqa: E402

UTC = timezone.utc
CUT = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)
WS = CUT - timedelta(hours=24)               # window: cutoff - 24 h ... cutoff (release - 48 h ... release - 24 h)
EV = {"symbol": "AAA", "window_start_utc": WS.isoformat(), "cutoff_utc": CUT.isoformat()}


# ------------------------------------------------------------------ news passes
def test_pass_boundaries_never_reach_the_cutoff():
    assert CP.news_pass(WS, CUT, WS - timedelta(minutes=1)) is None
    assert CP.news_pass(WS, CUT, WS) == "INITIAL" and CP.news_pass(WS, CUT, CUT - timedelta(hours=6, minutes=1)) == "INITIAL"
    assert CP.news_pass(WS, CUT, CUT - timedelta(hours=6)) == "FINAL" and CP.news_pass(WS, CUT, CUT - timedelta(hours=1)) == "FINAL"
    assert CP.news_pass(WS, CUT, CUT - timedelta(minutes=59)) is None and CP.news_pass(WS, CUT, CUT + timedelta(minutes=1)) is None


def rec(sym, when, status="OK", page=0):
    return {"symbol": sym, "kind": "uw_news", "status": status, "retrieved_at": when.isoformat(), "params": {"page": page} if page else {"ticker": sym}}


def test_each_pass_is_requested_once_and_failures_are_retried():
    t = WS + timedelta(hours=1)
    assert [x["pass"] for x in CP.news_tasks_due([EV], [], t)] == ["INITIAL"]
    assert CP.news_tasks_due([EV], [rec("AAA", t)], t + timedelta(hours=2)) == []                                   # initial already done
    assert [x["pass"] for x in CP.news_tasks_due([EV], [rec("AAA", t, status="INVALID")], t + timedelta(hours=2))] == ["INITIAL"]   # a failed record does not count
    late = CUT - timedelta(hours=3)
    assert [x["pass"] for x in CP.news_tasks_due([EV], [rec("AAA", t)], late)] == ["FINAL"]                         # the early record does not satisfy the final pass
    assert CP.news_tasks_due([EV], [rec("AAA", t), rec("AAA", late - timedelta(hours=1))], late) == []
    assert CP.news_tasks_due([EV], [], CUT - timedelta(minutes=30)) == []                                           # too close to the cutoff
    assert CP.news_tasks_due([EV], [rec("AAA", t, page=1)], t + timedelta(hours=1))[0]["pass"] == "INITIAL"       # a page-1 record alone is not a completed pass


def test_task_ids_are_stable_and_distinct_per_pass():
    a = CP.news_tasks_due([EV], [], WS + timedelta(hours=1))[0]["id"]
    b = CP.news_tasks_due([EV], [], CUT - timedelta(hours=3))[0]["id"]
    assert a != b and a == CP.news_tasks_due([EV], [], WS + timedelta(hours=2))[0]["id"]


def test_budget_uses_the_actual_event_count_and_page_cap():
    b = CP.news_budget(116)
    assert (b["floor"], b["expected"], b["ceiling"]) == (232, 267, 696) and b["minutes_at_2s_spacing"]["floor"] == 7.7
    assert CP.news_budget(0)["ceiling"] == 0


def test_retrieval_and_publication_after_the_cutoff_are_rejected_not_dropped_silently():
    assert CP.accept_retrieval(CUT - timedelta(seconds=1), CUT)["accepted"] and CP.accept_retrieval(CUT + timedelta(seconds=1), CUT)["reason"] == "RETRIEVED_AFTER_CUTOFF"
    h = lambda hrs, text="Acme item": {"created_at": (CUT - timedelta(hours=hrs)).isoformat().replace("+00:00", "Z"), "source": "Tradex", "headline": text}  # noqa: E731
    out = CP.pre_cutoff_headlines([h(2), h(2), h(-1), h(24 * 8), {"headline": "x", "created_at": "bad"}, h(5, "Other")], CUT)
    assert len(out["kept"]) == 2 and out["rejected"] == {"PUBLISHED_AFTER_CUTOFF": 1, "OUTSIDE_LOOKBACK": 1, "NO_TIMESTAMP": 1, "DUPLICATE": 1}


# ------------------------------------------------------------------ analyst snapshots
def test_one_snapshot_request_per_stock_and_day_and_no_invented_period():
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    ledger = [{"symbol": "A", "period": "2026-12-31", "retrieved_at": "2026-11-09T01:00:00+00:00"}, {"symbol": "B", "period": "2026-12-31", "retrieved_at": "2026-11-08T23:00:00+00:00"}]
    due = CP.snapshots_due([{"symbol": "A", "period": "2026-12-31"}, {"symbol": "B", "period": "2026-12-31"}, {"symbol": "C", "period": None}], ledger, now)
    assert [d["symbol"] for d in due] == ["B"] and due[0]["id"] == "uw_estimates-B-20261109"


def test_revision_becomes_measurable_only_after_seven_days_and_never_uses_later_snapshots():
    led = [{"symbol": "A", "period": "Q", "retrieved_at": "2026-11-01T10:00:00+00:00"}, {"symbol": "A", "period": "Q", "retrieved_at": "2026-11-09T10:00:00+00:00"}]
    early = CP.revision_eligibility(led, "A", "Q", datetime(2026, 11, 5, tzinfo=UTC))
    assert early["state"] == "WAITING" and early["eligible_from"].startswith("2026-11-08") and early["snapshots"] == 1                # the 9 Nov snapshot is invisible on 5 Nov
    assert CP.revision_eligibility(led, "A", "Q", datetime(2026, 11, 10, tzinfo=UTC))["state"] == "REVISION_MEASURABLE"
    assert CP.revision_eligibility(led, "A", "OTHER_QUARTER", datetime(2026, 11, 10, tzinfo=UTC))["state"] == "NO_SNAPSHOT"            # a different fiscal quarter never counts
    stale = [{"symbol": "A", "period": "Q", "retrieved_at": "2026-08-01T00:00:00+00:00"}, {"symbol": "A", "period": "Q", "retrieved_at": "2026-11-09T10:00:00+00:00"}]
    assert CP.revision_eligibility(stale, "A", "Q", datetime(2026, 11, 10, tzinfo=UTC))["state"] == "WAITING"                           # older than 60 days: not a valid comparison


def test_snapshot_budget_is_one_request_per_tracked_stock_per_day():
    b = CP.snapshot_budget(800)
    assert b["requests_per_day"] == 800 and b["minutes_per_day_at_2s_spacing"] == 26.7 and CP.snapshot_budget(800, 7)["requests"] == 5600


# ------------------------------------------------------------------ headline quality and the validation gate
def item(text, source="Tradex", sym="AAA", company="Acme Corp", hours_before=5, sentiment="neutral"):
    cap = datetime(2026, 11, 2, 12, tzinfo=UTC)
    return {"symbol": sym, "company": company, "headline": text, "source": source, "tickers": [sym], "sentiment": sentiment, "created_at": (cap - timedelta(hours=hours_before)).isoformat().replace("+00:00", "Z"),
            "retrieved_at": cap.isoformat()}


def test_review_flags_collisions_unreliable_sources_analyst_items_and_guidance_without_trusting_provider_sentiment():
    items = [item("Acme Corp lowers full-year guidance after weak demand", "Business Wire"), item("AAA Price Prediction: momentum at pivot", "Blockchain News"),
             item("New Book from AAA equips physician leaders", "PR NewsWire"), item("BROKER CUTS ACME PRICE TARGET TO $10 FROM $12 $AAA"), item("Acme Corp announces a new plant", "Business Wire"),
             item("Acme Corp announces a new plant", "Business Wire")]
    r = NQ.review(items)
    assert r["headlines"] == 5 and r["duplicates_removed"] == 1 and r["provider_sentiment"] == {"neutral": 5}
    assert r["ticker_collisions"] == 1 and r["unreliable_source"] == 1 and r["explicit_guidance_change"] == 1 and r["analyst_target_or_rating"] >= 1
    by = {x["headline"][:20]: x for x in r["rows"]}
    assert by["Acme Corp lowers ful"]["research_label"] == "NEGATIVE" and by["AAA Price Prediction"]["research_label"] == "UNVERIFIED" and by["New Book from AAA eq"]["research_label"] == "UNVERIFIED"
    assert by["BROKER CUTS ACME PRI"]["research_label"] != "NEGATIVE"                          # an analyst target cut is not company guidance


def test_labelling_sheet_has_a_blank_human_label_and_never_overwrites(tmp_path):
    rows = NQ.review([item("Acme Corp announces a new plant", "Business Wire")])["rows"]
    p = NQ.labelling_sheet(rows, tmp_path / "sheet.csv")
    text = p.read_text(encoding="utf-8")
    assert "human_label" in text.splitlines()[0] and text.splitlines()[1].count(",") >= 10
    with pytest.raises(FileExistsError):
        NQ.labelling_sheet(rows, p)


def test_validation_gate_stays_closed_without_enough_independent_labels():
    few = [{"research_label": "NEGATIVE", "human_label": "NEGATIVE"}] * 10
    g = NQ.validation_gate(few)
    assert g["may_be_considered_for_scoring"] is False and g["requirements"]["enough_labelled"] is False and "enables nothing" in g["approval"]
    many = [{"research_label": "POSITIVE", "human_label": "POSITIVE"}] * 100 + [{"research_label": "NEGATIVE", "human_label": "NEGATIVE"}] * 100 + [{"research_label": "NEUTRAL", "human_label": "NEUTRAL"}] * 50
    assert NQ.validation_gate(many)["may_be_considered_for_scoring"] is True
    wrong = [{"research_label": "POSITIVE", "human_label": "NEGATIVE"}] * 150 + [{"research_label": "NEGATIVE", "human_label": "POSITIVE"}] * 100
    assert NQ.validation_gate(wrong)["requirements"]["directional_agreement"] is False


# ------------------------------------------------------------------ saved data
@pytest.mark.skipif(not all(p.exists() for p in RUN.VERIFY), reason="verification stores not on this machine")
def test_live_msft_page_one_continues_after_page_zero_without_overlap():
    r = RUN.pagination(RUN.VERIFY[0], RUN.VERIFY[1])
    assert r["page1_params"]["page"] == 1 and r["overlap"] == 0 and r["duplicates_inside_page1"] == 0 and r["page1_entirely_older_than_page0"] and r["page1_tagged_with_MSFT"] == 25
