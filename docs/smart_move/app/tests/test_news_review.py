"""Phase 2 offline: news review workflow, association, warnings, and the disabled NEWS_GUIDANCE gate. Synthetic fixtures only."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
import smart_move_news_audit as NA  # noqa: E402
import smart_move_news_review as NR  # noqa: E402
from test_phase13_audits import CUT, EV, RET, head, rec  # noqa: E402

UTC = timezone.utc


def audited(items, company="ACME ROBOTICS INC"):
    return NA.audit([EV], [rec(items)], {"ACME": company})


def test_association_picks_the_earliest_following_cutoff_and_never_attaches_to_two_quarters():
    e1 = dict(EV, event_id="ACME|q3", cutoff_utc=CUT.isoformat())
    e2 = dict(EV, event_id="ACME|q4", cutoff_utc=(CUT + timedelta(days=3)).isoformat())
    pub = (CUT - timedelta(hours=40)).isoformat()
    a = NR.associate("ACME", pub, [e2, e1])
    assert a == {"event_id": "ACME|q3", "state": "ASSOCIATED_WITH_OTHER_CANDIDATES", "also_in_window": ["ACME|q4"]}
    assert NR.associate("ACME", (CUT - timedelta(days=9)).isoformat(), [e1])["state"] == "NO_EVENT_IN_WINDOW"          # more than 7 days before any cutoff
    assert NR.associate("ACME", (CUT + timedelta(hours=1)).isoformat(), [e1])["state"] == "NO_EVENT_IN_WINDOW"          # published after the cutoff
    assert NR.associate("OTHER", pub, [e1])["event_id"] is None and NR.associate("ACME", "garbage", [e1])["state"] == "NO_TIMESTAMP"


def test_the_queue_holds_only_reviewable_headlines_with_stable_ids():
    items = [head("Acme Robotics ships units"), head("Acme Robotics ships units"), head("Global chip makers rally"), head("Acme Robotics old news", hours_before_cut=24 * 9), head("")]
    q = NR.build_queue(audited(items))
    assert [x["audit_class"] for x in q] == ["VALID", "IRRELEVANT"]                                                      # duplicate, stale and invalid ones are not queued
    assert q == NR.build_queue(audited(items)) and len({x["item_id"] for x in q}) == 2


def test_labels_are_validated_append_only_and_revisions_keep_history(tmp_path):
    q = NR.build_queue(audited([head("Acme Robotics ships units")]))
    iid = q[0]["item_id"]
    st = NR.LabelStore(tmp_path / "labels.jsonl")
    with pytest.raises(ValueError):
        st.record(iid, "BULLISH", "ann")
    with pytest.raises(ValueError):
        st.record(iid, "POSITIVE", " ")
    with pytest.raises(KeyError):
        st.record("nope", "POSITIVE", "ann", known_items={iid})
    st.record(iid, "NEUTRAL", "ann", known_items={iid})
    st.record(iid, "POSITIVE", "ann", known_items={iid})                                                                  # a revision, not an edit
    assert len(NR.LabelStore(tmp_path / "labels.jsonl").rows) == 2 and st.current()[iid] == {"ann": "POSITIVE"} and st.consensus()[iid]["label"] == "POSITIVE"
    st.record(iid, "NEGATIVE", "bob")
    assert st.consensus()[iid]["state"] == "DISPUTED" and st.consensus()[iid]["label"] is None
    st.record(iid, "UNSURE", "bob")
    assert st.consensus()[iid] == {"state": "LABELLED", "label": "POSITIVE", "reviewers": 2}                               # UNSURE is not a vote


def test_the_guidance_gate_reports_requirements_and_can_never_enable_scoring():
    q = NR.build_queue(audited([head("Acme Robotics ships units")]))
    st = NR.LabelStore()
    g0 = NR.guidance_gate(q, st)
    assert g0["enabled"] is False and g0["status"] == "DISABLED_UNTIL_VALIDATED_AND_APPROVED" and g0["may_be_considered_for_scoring"] is False and g0["unlabelled"] == 1
    st.record(q[0]["item_id"], "POSITIVE", "ann")
    g1 = NR.guidance_gate(q, st)
    assert g1["labelled"] == 1 and g1["enabled"] is False and g1["requirements"]["enough_labelled"] is False
    big = [{"item_id": "i%d" % n, "classifier_label": "POSITIVE" if n % 2 else "NEGATIVE"} for n in range(300)]
    full = NR.LabelStore()
    for b in big:
        full.record(b["item_id"], b["classifier_label"], "ann")
    g2 = NR.guidance_gate(big, full)
    assert g2["may_be_considered_for_scoring"] is True and g2["enabled"] is False                                         # even a perfect score only makes it eligible for the owner's decision


def test_warnings_cover_missing_empty_stale_unreliable_and_collision_cases():
    w = NR.event_warnings
    assert w(NA.audit_event(EV, [], "ACME")) == ["MISSING_NEWS_NOT_COLLECTED"]
    assert w(NA.audit_event(EV, [rec([], status="FAILED")], "ACME")) == ["MISSING_NEWS_REQUEST_FAILED"]
    assert w(NA.audit_event(EV, [rec([])], "ACME")) == ["EMPTY_NEWS_RESPONSE"]
    assert w(NA.audit_event(dict(EV, cutoff_utc=None), [rec([head("x")])], "ACME")) == ["NO_CUTOFF_NEWS_CANNOT_BE_JUDGED"]
    stale = NA.audit_event(EV, [rec([head("Acme Robotics a", hours_before_cut=24 * 9), head("Acme Robotics b", hours_before_cut=24 * 10)])], "ACME ROBOTICS INC")
    assert w(stale) == ["NO_VALID_HEADLINE", "ALL_HEADLINES_STALE"]
    mixed = NA.audit_event(EV, [rec([head("Acme Robotics ships units"), head("Acme Robotics blog", src="Some Blog"), head("Chip makers rally")])], "ACME ROBOTICS INC")
    assert w(mixed) == ["UNRELIABLE_OR_UNVERIFIABLE_HEADLINES_PRESENT", "POSSIBLE_TICKER_COLLISION_OR_OFF_TOPIC"]
    old = NA.audit_event(EV, [rec([head("Acme Robotics ships units", hours_before_cut=100)])], "ACME ROBOTICS INC")
    assert w(old) == ["FRESHEST_VALID_HEADLINE_OVER_72H_OLD"]
    rep = NR.warnings_report({"events": [stale, mixed]})
    assert rep["events_with_any_warning"] == 2 and rep["by_warning"]["ALL_HEADLINES_STALE"] == 1


def test_the_review_module_never_imports_scoring_or_provider_code():
    src = (ROOT / "backend" / "smart_move_news_review.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if any(w in ln for w in ("requests", "httpx", "urllib", "socket", "uw_client", "smart_move_live", "smart_move_v2", "scorer"))]
    assert "enabled=True" not in src and '"enabled": True' not in src
