"""Gate-refusal alert channel (owner design 2026-11-12): source of truth = last_gate_refusal.json, cross-checked by timestamp
against the BLOCKED record and the schedule log's exit code 3; never "last record" (the hourly DRY_RUN used to mask a
refusal); cleared only by a later LIVE pass of the SAME variant; acknowledgement is grey, not a clear; webhook posts once."""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from earnings_monitor import smart_live as SL  # noqa: E402

UTC = timezone.utc
FIX = Path(__file__).resolve().parent / "fixtures" / "smart_live"
T2 = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)                           # the fixture's last successful pass


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "state" / "live"
    shutil.copytree(FIX, d)
    return d


def _refuse(d: Path, at: datetime, reason="gates failed: owner attestation valid for at least 1 more hour | runner kill switch present", source="v6_5_live_scheduled.ps1"):
    rec = {"event": "BLOCKED", "at": at.isoformat(), "mode": "LIVE", "source": source, "reason": reason}
    with open(d / "collection_runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    (d / "last_gate_refusal.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def _dry_run_record(d: Path, at: datetime):
    """What the hourly :05 reader leaves behind: its own run records and a rewritten status file (last_successful_scan_at untouched)."""
    with open(d / "collection_runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "STARTED", "at": at.isoformat(), "mode": "DRY_RUN"}) + "\n")
        fh.write(json.dumps({"event": "FINISHED", "at": (at + timedelta(minutes=1)).isoformat(), "mode": "DRY_RUN", "exit_code": 0}) + "\n")
    st = json.loads((d / "live_status.json").read_text(encoding="utf-8"))
    st.update(mode="DRY_RUN", written_at=(at + timedelta(minutes=1)).isoformat())
    (d / "live_status.json").write_text(json.dumps(st), encoding="utf-8")


def _live_success(d: Path, at: datetime):
    st = json.loads((d / "live_status.json").read_text(encoding="utf-8"))
    st.update(mode="LIVE", written_at=at.isoformat(), last_successful_scan_at=at.isoformat(), scan_ok=True)
    (d / "live_status.json").write_text(json.dumps(st), encoding="utf-8")


def _schedule_log(tmp_path: Path, at: datetime, exit_code: int, variant_sub=""):
    sd = tmp_path / "live_schedule" / variant_sub
    sd.mkdir(parents=True, exist_ok=True)
    with open(sd / "scheduled_live_runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "STARTED", "at": (at - timedelta(seconds=5)).isoformat()}) + "\n")
        fh.write(json.dumps({"event": "FINISHED", "at": (at + timedelta(seconds=20)).isoformat(), "exit_code": exit_code, "attempts": 0}) + "\n")
    (sd / ("sched_run_%s.txt" % (at - timedelta(seconds=5)).strftime("%Y%m%d_%H%M%S"))).write_text("=== 1. gates\n   FAIL  owner attestation valid\nREFUSED: gates failed (1)\n", encoding="utf-8")
    return str(tmp_path / "live_schedule")


def test_no_refusal_means_no_alert(live):
    a = SL.load_alerts(str(live), T2)
    assert a == {"configured": True, "active": False, "alerts": []}
    assert SL.load_alerts(None, T2)["configured"] is False
    assert SL.load(str(live), T2)["alerts"] == []


def test_a_refusal_is_not_masked_by_a_later_dry_run_record(live):
    """The masking case: the hourly DRY_RUN appends its own records after the refusal; the alert must stay active and the page state BLOCKED."""
    t_ref = T2 + timedelta(hours=1)
    _refuse(live, t_ref)
    _dry_run_record(live, t_ref + timedelta(minutes=40))
    now = t_ref + timedelta(hours=2)
    a = SL.load_alerts(str(live), now)
    assert a["active"] is True and len(a["alerts"]) == 1
    al = a["alerts"][0]
    assert al["variant"] == "LIVE" and al["task"] == "TRDGO-SmartMove-LIVE-6h" and al["blocked_record_confirmed"] is True and al["severity"] == "critical"
    assert al["reasons"] == ["owner attestation valid for at least 1 more hour", "runner kill switch present"] and al["consecutive"] == 1 and al["acknowledged"] is False
    assert al["next_attempt_at"] and al["minutes_ago"] == 120 and al["at_et"]
    out = SL.load(str(live), now)
    assert out["state"] == "BLOCKED" and "attestation" in out["state_text"] and out["alerts"][0]["id"] == al["id"]


def test_a_later_live_success_of_the_same_variant_clears_the_alert_but_a_dry_run_never_does(live):
    t_ref = T2 + timedelta(hours=1)
    _refuse(live, t_ref)
    _dry_run_record(live, t_ref + timedelta(hours=1))
    _dry_run_record(live, t_ref + timedelta(hours=2))
    assert SL.load_alerts(str(live), t_ref + timedelta(hours=3))["active"] is True                      # two dry runs later: still active
    _live_success(live, t_ref + timedelta(hours=5))
    a = SL.load_alerts(str(live), t_ref + timedelta(hours=5, minutes=1))
    assert a["active"] is False and a["alerts"] == [] and a["last_cleared"]["cleared"] is True and "LIVE pass succeeded" in a["last_cleared"]["cleared_by"]
    assert SL.load(str(live), t_ref + timedelta(hours=5, minutes=1))["state"] in ("OK", "STALE")         # no longer BLOCKED


def test_consecutive_refusals_are_counted_since_the_last_success(live):
    base = T2 + timedelta(hours=1)
    for k in range(3):
        _refuse(live, base + timedelta(hours=6 * k))
    a = SL.load_alerts(str(live), base + timedelta(hours=13))
    assert a["alerts"][0]["consecutive"] == 3 and a["alerts"][0]["last_success_before"] == T2.isoformat()


def test_schedule_log_cross_check_confirms_exit_code_3_and_names_the_gates_output(live, tmp_path):
    t_ref = T2 + timedelta(hours=1)
    _refuse(live, t_ref)
    sd = _schedule_log(tmp_path, t_ref, 3)
    a = SL.load_alerts(str(live), t_ref + timedelta(minutes=30), schedule_log_dir=sd)
    al = a["alerts"][0]
    assert al["exit_code"] == 3 and al["exit_code_confirmed"] is True and al["schedule_log_checked"] is True and al["gates_output"].startswith("sched_run_")
    txt = SL.gates_output_text(str(live), sd, t_ref + timedelta(minutes=30))
    assert txt["ok"] is True and "REFUSED" in txt["text"] and txt["name"] == al["gates_output"]
    # without the schedule log the alert still stands on the refusal file alone (exit code unknown, not "confirmed")
    b = SL.load_alerts(str(live), t_ref + timedelta(minutes=30))["alerts"][0]
    assert b["exit_code"] is None and b["exit_code_confirmed"] is False and b["schedule_log_checked"] is False


def test_acknowledge_turns_the_alert_grey_but_keeps_it_until_a_success(live):
    t_ref = T2 + timedelta(hours=1)
    _refuse(live, t_ref)
    res = SL.acknowledge_refusal(str(live), "owner", t_ref + timedelta(minutes=5))
    assert res["ok"] is True and res["by"] == "owner"
    a = SL.load_alerts(str(live), t_ref + timedelta(minutes=6))
    assert a["active"] is True and a["alerts"][0]["acknowledged"] is True and a["alerts"][0]["severity"] == "acknowledged" and a["alerts"][0]["acknowledged_by"] == "owner"
    _refuse(live, t_ref + timedelta(hours=6))                                                            # a NEW refusal is not covered by the old acknowledgement
    b = SL.load_alerts(str(live), t_ref + timedelta(hours=6, minutes=1))
    assert b["alerts"][0]["acknowledged"] is False and b["alerts"][0]["consecutive"] == 2
    assert SL.acknowledge_refusal(str(live), "owner", t_ref)["ok"] is True
    _live_success(live, t_ref + timedelta(hours=12))
    assert SL.load_alerts(str(live), t_ref + timedelta(hours=12, minutes=1))["active"] is False
    assert SL.acknowledge_refusal(None, "owner", t_ref)["reason"] == "NOT_CONFIGURED"


def test_monday_bmo_refusal_is_its_own_variant_and_only_its_own_success_clears_it(live):
    t_ref = T2 + timedelta(hours=1)
    _refuse(live, t_ref, source="v6_5_monday_bmo_scheduled.ps1")
    _live_success(live, t_ref + timedelta(hours=2))                                                      # the main LIVE variant succeeded: not the same variant
    a = SL.load_alerts(str(live), t_ref + timedelta(hours=3))
    assert a["active"] is True and a["alerts"][0]["variant"] == "MONDAY_BMO_FRIDAY" and a["alerts"][0]["task"] == "TRDGO-SmartMove-MondayBMO"
    sub = live / "monday_bmo_friday"
    sub.mkdir(exist_ok=True)
    (sub / "live_status.json").write_text(json.dumps({"mode": "LIVE", "last_successful_scan_at": (t_ref + timedelta(hours=4)).isoformat(), "schedule": {"live_every_minutes": 10080}}), encoding="utf-8")
    assert SL.load_alerts(str(live), t_ref + timedelta(hours=5))["active"] is False


def test_webhook_posts_a_new_refusal_once_and_is_off_without_a_url(live):
    t_ref = T2 + timedelta(hours=1)
    _refuse(live, t_ref)
    sent = []

    def post(url, payload):
        sent.append((url, payload))
        return True
    assert SL.notify_webhook_once(str(live), "", t_ref, post=post)["reason"] == "OFF" and sent == []
    r1 = SL.notify_webhook_once(str(live), "https://hooks.example/x", t_ref + timedelta(minutes=1), post=post)
    r2 = SL.notify_webhook_once(str(live), "https://hooks.example/x", t_ref + timedelta(minutes=2), post=post)
    assert r1["sent"] is True and r2["sent"] is False and r2["reason"] == "ALREADY_SENT" and len(sent) == 1
    assert sent[0][1]["type"] == "smart_move_gate_refusal" and sent[0][1]["task"] == "TRDGO-SmartMove-LIVE-6h" and "attestation" in sent[0][1]["text"]
    _refuse(live, t_ref + timedelta(hours=6))
    assert SL.notify_webhook_once(str(live), "https://hooks.example/x", t_ref + timedelta(hours=6, minutes=1), post=post)["sent"] is True and len(sent) == 2
    _live_success(live, t_ref + timedelta(hours=12))
    assert SL.notify_webhook_once(str(live), "https://hooks.example/x", t_ref + timedelta(hours=12, minutes=1), post=post)["reason"] == "NO_ACTIVE_ALERT"


def test_a_failed_post_is_retried_next_time_not_marked_as_sent(live):
    t_ref = T2 + timedelta(hours=1)
    _refuse(live, t_ref)

    def bad(url, payload):
        raise OSError("connection refused")
    r = SL.notify_webhook_once(str(live), "https://hooks.example/x", t_ref, post=bad)
    assert r["sent"] is False and r["reason"] == "POST_FAILED"
    assert SL.notify_webhook_once(str(live), "https://hooks.example/x", t_ref, post=lambda u, p: True)["sent"] is True
