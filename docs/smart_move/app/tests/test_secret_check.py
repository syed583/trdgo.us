"""Token source, rotation check, missing-token handling and redaction. Fixtures use obviously fake tokens; nothing contacts the provider and no real token is read."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_secret_check as SC  # noqa: E402
import test_pilot_preflight as T  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

OLD = "FAKEOLDTOKEN0123456789abcdefghijkl"
NEW = "FAKENEWTOKEN9876543210zyxwvutsrqpo"


def envfile(tmp_path, value=OLD, name=".env", extra=""):
    f = tmp_path / name
    f.write_text("DATABASE_URL=postgresql://x\n%sUNUSUAL_WHALES_API_KEY=%s\nAPP_ENV=development\n" % (extra, value))
    return f


def test_the_file_is_the_source_when_no_process_variable_exists_and_only_a_fingerprint_is_reported(tmp_path):
    r = SC.inspect(envfile(tmp_path), env={}, backups=())
    assert r["source"] == "ENV_FILE" and r["length"] == len(OLD) and r["fingerprint"] == SC.fingerprint(OLD) and r["shape_problems"] == []
    assert OLD not in json.dumps(r) and OLD[-6:] not in json.dumps(r)


def test_a_process_variable_wins_over_the_file_and_a_mismatch_is_flagged(tmp_path):
    r = SC.inspect(envfile(tmp_path), env={SC.ENV_VAR: NEW}, backups=())
    assert r["source"] == "PROCESS_ENVIRONMENT" and r["fingerprint"] == SC.fingerprint(NEW) and any("DIFFERENT" in n for n in r["notes"]) and r["ready_for_connectivity_test"] is False
    same = SC.inspect(envfile(tmp_path), env={SC.ENV_VAR: OLD}, backups=())
    assert same["notes"] == [] and same["ready_for_connectivity_test"] is True


def test_rotation_is_detected_by_fingerprint_and_the_old_token_blocks_readiness(tmp_path):
    old_fp = SC.fingerprint(OLD)
    still_old = SC.inspect(envfile(tmp_path), env={}, backups=(), old_fingerprint=old_fp)
    assert still_old["rotated_from_old"] is False and still_old["ready_for_connectivity_test"] is False
    rotated = SC.inspect(envfile(tmp_path, NEW, "new.env"), env={}, backups=(), old_fingerprint=old_fp)
    assert rotated["rotated_from_old"] is True and rotated["ready_for_connectivity_test"] is True


def test_stale_backup_copies_of_the_old_token_are_reported(tmp_path):
    bak = envfile(tmp_path, OLD, ".env.bak")
    r = SC.inspect(envfile(tmp_path, NEW, ".env"), env={}, backups=(bak, tmp_path / "missing.bak"), old_fingerprint=SC.fingerprint(OLD))
    assert len(r["backup_copies"]) == 1 and r["backup_copies"][0]["same_as_old"] is True and r["backup_copies"][0]["same_as_active"] is False


def test_missing_and_malformed_tokens_are_reported_and_never_ready(tmp_path):
    assert SC.inspect(tmp_path / "none.env", env={}, backups=())["source"] == "MISSING"
    r = SC.inspect(envfile(tmp_path, "short"), env={}, backups=())
    assert "TOO_SHORT" in r["shape_problems"] and r["ready_for_connectivity_test"] is False
    f = tmp_path / "spaces.env"
    f.write_text("UNUSUAL_WHALES_API_KEY=abc def ghi jkl mno pqr stu\n")
    assert "CONTAINS_WHITESPACE" in SC.inspect(f, env={}, backups=())["shape_problems"]


def test_redaction_removes_the_active_token_bearer_headers_and_key_value_secrets(tmp_path):
    f = envfile(tmp_path)
    text = "error calling provider with %s and Authorization: Bearer abcdefghijklmnop1234 and api_key=%s and token: zzzzzzzzzzzz" % (OLD, NEW)
    out = SC.redact(text, f, env={}, extra=[NEW])
    assert OLD not in out and NEW not in out and "abcdefghijklmnop1234" not in out and "zzzzzzzzzzzz" not in out and out.count("[REDACTED]") >= 4
    assert SC.redact("nothing secret here", f, env={}) == "nothing secret here"


def test_the_attempt_log_redacts_error_text_that_contains_a_secret(tmp_path, monkeypatch):
    monkeypatch.setenv(SC.ENV_VAR, NEW)

    class Leaky:
        mode = "SIMULATION"

        def fetch(self, kind, symbol, params):
            e = gv.ProviderError("auth", "401 Bearer %s rejected token=%s" % (NEW, NEW))
            raise e
    log = tmp_path / "attempts.jsonl"
    rec = J.AttemptRecorder(Leaky(), log, gv.FakeClock(T.NOW))
    with pytest.raises(gv.ProviderError):
        rec.fetch("uw_news", "A", {"ticker": "A", "limit": 25, "token": "should-not-be-logged"})
    text = log.read_text()
    assert NEW not in text and "should-not-be-logged" not in text and "[REDACTED]" in text


def test_a_live_run_with_a_missing_or_unrotated_token_is_blocked_before_any_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_COLLECTION_LIVE", "1")
    monkeypatch.setattr(RUNNER.SK, "evaluate", lambda now, path=None, **k: {"status": "OWNER_ATTESTED", "blocks_live": False, "reason": "test"})
    f = envfile(tmp_path, OLD)
    monkeypatch.setattr(RUNNER, "ENV", f)
    monkeypatch.delenv(SC.ENV_VAR, raising=False)
    args = ["--store-dir", str(tmp_path / "s"), "--state-dir", str(tmp_path / "st"), "--mode", "live", "--arm", "--authorize-requests", "50", "--profile", "pilot50"]
    assert RUNNER.main(args + ["--require-rotated-from", SC.fingerprint(OLD)]) == 4                       # still the old token
    monkeypatch.setattr(RUNNER, "ENV", tmp_path / "absent.env")
    assert RUNNER.main(args) == 4                                                                          # no token at all
    assert not (tmp_path / "st").exists()                                                                  # nothing was built or written


def test_the_shared_key_gate_is_not_bypassed_by_a_new_token(tmp_path, monkeypatch):
    """A rotated, well-formed token still needs the owner attestation: the gate runs before the token check."""
    monkeypatch.setenv("SMART_MOVE_COLLECTION_LIVE", "1")
    monkeypatch.setattr(RUNNER.SK, "evaluate", lambda now, path=None, **k: {"status": "SHARED KEY SAFETY NOT VERIFIED", "blocks_live": True, "reason": "no attestation"})
    monkeypatch.setattr(RUNNER, "ENV", envfile(tmp_path, NEW))
    rc = RUNNER.main(["--store-dir", str(tmp_path / "s"), "--state-dir", str(tmp_path / "st"), "--mode", "live", "--arm", "--authorize-requests", "50", "--profile", "pilot50", "--require-rotated-from", SC.fingerprint(OLD)])
    assert rc == 7


def test_dry_run_output_never_contains_the_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(SC.ENV_VAR, NEW)
    store = tmp_path / "s"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("")
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "st"), "--profile", "pilot50"]) == 0
    out = capsys.readouterr().out
    assert NEW not in out and "provider_requests" in out


def test_rotation_script_never_prints_or_passes_the_token_on_a_command_line():
    s = (ROOT / "docs" / "smart_move" / "deploy" / "rotate_uw_token.ps1").read_text(encoding="utf-8")
    assert "-AsSecureString" in s and "Write-Host" in s
    bad = [ln for ln in s.splitlines() if "Write-Host" in ln and ("$t " in ln.split("-f")[0] or "$plain" in ln.split("-f")[0] or "$env:UNUSUAL" in ln.split("-f")[0])]
    assert bad == []                                                                                       # Write-Host lines only format length and fingerprint
    assert "Invoke-WebRequest" not in s and "Invoke-RestMethod" not in s and "unusualwhales.com" not in s     # no network call in the script
