"""Kit v5 installer: safety rehearsal (LOCAL, isolated temp folders only; no real application folder is touched). The 'installed v4' is rebuilt from the git blobs of 274fccac (verified equal to kit v4 / collector v3).
Covers forced failures, crash recovery, concurrent operations, the collector lock, both kill switches, baseline hash checks, and rollback."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
KDIR = ROOT / "docs" / "smart_move" / "p1" / "kit_v5"
sys.path.insert(0, str(KDIR))
spec = importlib.util.spec_from_file_location("install_kit_v5", KDIR / "install_kit_v5.py")
I = importlib.util.module_from_spec(spec)
spec.loader.exec_module(I)
K = I.K
BASE = "274fccac"


def blob(n):
    return subprocess.run(["git", "show", "%s:backend/%s" % (BASE, n)], cwd=ROOT, capture_output=True, check=True).stdout


@pytest.fixture
def w(tmp_path):
    app, payload, root, base = tmp_path / "app_backend", tmp_path / "payload", tmp_path / "backups", tmp_path / "base"
    state, store = tmp_path / "sm_collector_state", tmp_path / "sm_evidence_store"
    for d in (app, payload, base, state, store):
        d.mkdir()
    (store / "KILL").write_text("runner kill switch engaged\n", encoding="utf-8")                    # the normal idle state of the runner
    for n in K.CHANGED + K.PROTECTED:
        (app / n).write_bytes(blob(n))
        (base / n).write_bytes(blob(n))
    (app / "other_unrelated_module.py").write_text("# not ours\n", encoding="utf-8")
    for n in K.CHANGED + K.NEW:
        shutil.copyfile(ROOT / "backend" / n, payload / n)
    manifest = K.build(ROOT, base)
    mf = tmp_path / "KIT_V5_MANIFEST.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    return dict(app=app, payload=payload, root=root, state=state, store=store, manifest=manifest, mf=mf, before={p.name: p.read_bytes() for p in app.iterdir()}, tmp=tmp_path)


def snapshot(app):
    return {p.name: p.read_bytes() for p in app.iterdir()}


def ins(w):
    return I.install(w["manifest"], w["payload"], w["app"], w["root"], w["state"], w["store"])


def rec(w):
    return I.recover(w["manifest"], w["app"], w["root"], w["state"], w["store"])


def rb(w):
    return I.rollback(w["manifest"], w["app"], w["root"], w["state"], w["store"])


def args(w, cmd):
    return [sys.executable, str(KDIR / "install_kit_v5.py"), cmd, "--manifest", str(w["mf"]), "--payload", str(w["payload"]), "--app-backend", str(w["app"]), "--backup-root", str(w["root"]),
            "--state-root", str(w["state"]), "--store-dir", str(w["store"])]


def cli(w, cmd, env=None):
    return subprocess.run(args(w, cmd), capture_output=True, text=True, env=dict(os.environ, **(env or {})))


def popen(w, cmd, env):
    return subprocess.Popen(args(w, cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ, **env))


def journal_state(w):
    return json.loads((I._latest_backup(w["root"]) / "journal.json").read_text())["state"]


def fingerprint(path):
    return path.read_bytes() if path.exists() else None


# ------------------------------------------------------------------ install, order, backup
def test_a_clean_install_replaces_exactly_the_six_files_keeps_a_verified_backup_and_leaves_everything_else_alone(w):
    code, msgs = ins(w)
    assert code == 0 and "INSTALLED" in msgs[0]
    after = snapshot(w["app"])
    assert set(after) - set(w["before"]) == set(K.NEW) and not [n for n in after if n.endswith(".kitv5tmp")]
    assert all(after[n] != w["before"][n] for n in K.CHANGED) and all(after[n] == w["before"][n] for n in set(w["before"]) - set(K.CHANGED))
    assert K.verify(w["manifest"], None, w["app"], "post")[0] == 0
    bdir = I._latest_backup(w["root"])
    assert journal_state(w) == "INSTALLED" and all((bdir / "files" / n).read_bytes() == w["before"][n] for n in K.CHANGED)
    assert not (w["state"] / "collection.lock").exists() and not (w["state"] / "KILL").exists()          # the holds the installer made are released


def test_install_order_puts_the_new_modules_before_the_runner_that_imports_them():
    assert I.ORDER[:3] == list(K.NEW) and I.ORDER[-1] == "smart_move_collection_runner.py"


# ------------------------------------------------------------------ forced failures and crashes
@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_a_forced_failure_after_any_number_of_files_restores_the_exact_v4_state_and_releases_the_holds(w, n, monkeypatch):
    monkeypatch.setenv(I.FAIL_AFTER_ENV, str(n))
    code, msgs = ins(w)
    assert code == 4 and "every file was restored and verified" in msgs[0]
    assert snapshot(w["app"]) == w["before"] and journal_state(w) == "RESTORED"
    assert not (w["state"] / "collection.lock").exists() and not (w["state"] / "KILL").exists()


def test_a_write_error_in_the_middle_is_handled_the_same_way(w, monkeypatch):
    real = os.replace

    def flaky(src, dst):
        if str(dst).endswith("smart_move_engine20.py") and str(src).endswith(".kitv5tmp") and not flaky.failed:
            flaky.failed = True
            raise OSError("disk full (injected)")
        return real(src, dst)
    flaky.failed = False
    monkeypatch.setattr(I.os, "replace", flaky)
    code, msgs = ins(w)
    assert code == 4 and snapshot(w["app"]) == w["before"]


def test_an_interrupted_install_fails_closed_until_recovery_and_recovery_restores_v4_and_releases_the_holds(w):
    r = cli(w, "install", {I.CRASH_AFTER_ENV: "4"})                                                   # os._exit after four files: no cleanup runs
    assert r.returncode == 9
    mid = snapshot(w["app"])
    assert mid != w["before"] and K.verify(w["manifest"], None, w["app"], "post")[0] == 3            # PARTIAL is detected, never mistaken for success
    assert journal_state(w) == "INSTALLING"
    assert (w["state"] / "collection.lock").exists() and (w["state"] / "KILL").exists()               # the collector cannot run on half-installed code
    for refuse in (cli(w, "install"), cli(w, "rollback")):
        assert refuse.returncode == 2 and "interrupted" in refuse.stdout and "recover" in refuse.stdout
        assert snapshot(w["app"]) == mid                                                              # fail closed: nothing was touched
    r2 = cli(w, "recover")                                                                            # the OS freed the operation lock when the process died
    assert r2.returncode == 0 and "RECOVERED" in r2.stdout and snapshot(w["app"]) == w["before"] and journal_state(w) == "RESTORED"
    assert not (w["state"] / "collection.lock").exists() and not (w["state"] / "KILL").exists()
    assert cli(w, "install").returncode == 0                                                         # and a clean install works afterwards


def test_a_failed_restore_keeps_the_collector_blocked_and_the_journal_says_so(w, monkeypatch):
    monkeypatch.setenv(I.FAIL_AFTER_ENV, "3")
    real = I.restore
    monkeypatch.setattr(I, "restore", lambda m, a, b: (False, ["NOT RESTORED: smart_move_live.py (injected)"]))
    code, msgs = ins(w)
    assert code == 5 and "RESTORE DID NOT VERIFY, the collector stays blocked" in msgs[0] and journal_state(w) == "RESTORE_FAILED"
    assert (w["state"] / "collection.lock").exists() and (w["state"] / "KILL").exists()
    monkeypatch.setattr(I, "restore", real)
    monkeypatch.delenv(I.FAIL_AFTER_ENV)
    again = ins(w)
    assert again[0] == 2 and "recover" in " ".join(again[1])                                          # fail closed until recovery completes
    assert rec(w)[0] == 0 and journal_state(w) == "RESTORED" and snapshot(w["app"]) == w["before"]


# ------------------------------------------------------------------ concurrency
def test_concurrent_operations_cannot_overlap_install_recover_rollback_all_refuse_while_one_runs(w):
    slow = popen(w, "install", {I.PAUSE_ENV: "5"})                                                    # holds the operation lock for ~5 s, after two files
    time.sleep(2.5)
    try:
        for cmd in ("install", "recover", "rollback"):
            r = cli(w, cmd)
            assert r.returncode == 2 and "another kit-v5 operation" in r.stdout, (cmd, r.stdout)
    finally:
        out, err = slow.communicate(timeout=60)
    assert slow.returncode == 0 and "INSTALLED" in out
    assert K.verify(w["manifest"], None, w["app"], "post")[0] == 0                                    # the refused operations changed nothing


# ------------------------------------------------------------------ collector lock and kill switches
def test_an_active_collector_lock_refuses_install_and_rollback_and_the_lock_is_never_removed(w):
    (w["state"] / "collection.lock").write_text('{"pid": 1234, "at": "now"}', encoding="utf-8")        # the wrapper's lock: a run is in progress
    before = fingerprint(w["state"] / "collection.lock")
    code, msgs = ins(w)
    assert code == 2 and "collector lock present" in " ".join(msgs) and snapshot(w["app"]) == w["before"] and not list(w["root"].glob("kit_v5_backup_*"))
    assert fingerprint(w["state"] / "collection.lock") == before and not (w["state"] / "KILL").exists()
    (w["state"] / "collection.lock").unlink()
    assert ins(w)[0] == 0
    (w["state"] / "collection.lock").write_text('{"pid": 1234}', encoding="utf-8")
    code, msgs = rb(w)
    assert code == 2 and "collector lock present" in " ".join(msgs) and K.verify(w["manifest"], None, w["app"], "post")[0] == 0 and fingerprint(w["state"] / "collection.lock") == b'{"pid": 1234}'


def test_a_missing_runner_kill_switch_means_a_live_run_may_be_active_and_refuses(w):
    (w["store"] / "KILL").unlink()
    code, msgs = ins(w)
    assert code == 2 and "runner kill switch missing" in " ".join(msgs) and snapshot(w["app"]) == w["before"]
    assert not (w["state"] / "collection.lock").exists() and not (w["state"] / "KILL").exists()


def test_the_runner_kill_switch_is_never_modified(w):
    before = fingerprint(w["store"] / "KILL")
    assert ins(w)[0] == 0 and fingerprint(w["store"] / "KILL") == before
    assert rb(w)[0] == 0 and fingerprint(w["store"] / "KILL") == before


def test_an_existing_wrapper_kill_switch_is_respected_left_in_place_and_install_still_works(w):
    (w["state"] / "KILL").write_text("collector deliberately off\n", encoding="utf-8")
    before = fingerprint(w["state"] / "KILL")
    assert ins(w)[0] == 0 and fingerprint(w["state"] / "KILL") == before                              # not removed, not rewritten
    assert rb(w)[0] == 0 and fingerprint(w["state"] / "KILL") == before


def test_the_installer_engages_the_wrapper_kill_switch_for_the_duration_and_removes_only_its_own(w):
    seen = {}
    real = I._atomic_write

    def spy(dst, data, want):
        seen["kill"] = (w["state"] / "KILL").exists()
        seen["lock"] = (w["state"] / "collection.lock").exists()
        return real(dst, data, want)
    I._atomic_write = spy
    try:
        assert ins(w)[0] == 0
    finally:
        I._atomic_write = real
    assert seen == {"kill": True, "lock": True}                                                       # held while files were being replaced
    assert not (w["state"] / "KILL").exists() and not (w["state"] / "collection.lock").exists()


# ------------------------------------------------------------------ baseline hashes and refusals
def test_refusals_touch_nothing_unknown_base_missing_or_tampered_payload_and_installing_twice(w):
    (w["app"] / "smart_move_live.py").write_bytes(b"# some other version\n")
    before = snapshot(w["app"])
    code, msgs = ins(w)
    assert code == 2 and "not the expected v4 base" in " ".join(msgs) and snapshot(w["app"]) == before and not list(w["root"].glob("kit_v5_backup_*"))
    (w["app"] / "smart_move_live.py").write_bytes(w["before"]["smart_move_live.py"])
    (w["payload"] / "smart_move_universe.py").unlink()
    assert ins(w)[0] == 2 and snapshot(w["app"]) == w["before"]
    shutil.copyfile(ROOT / "backend" / "smart_move_universe.py", w["payload"] / "smart_move_universe.py")
    (w["payload"] / "smart_move_engine20.py").write_bytes((w["payload"] / "smart_move_engine20.py").read_bytes() + b"# x\n")
    assert ins(w)[0] == 2 and snapshot(w["app"]) == w["before"]
    shutil.copyfile(ROOT / "backend" / "smart_move_engine20.py", w["payload"] / "smart_move_engine20.py")
    assert ins(w)[0] == 0
    assert ins(w)[0] == 2                                                                              # installing twice is refused: the base is no longer v4


# ------------------------------------------------------------------ rollback
def test_rollback_from_v5_to_v4_is_exact_and_refuses_when_the_installed_files_changed_afterwards(w):
    assert ins(w)[0] == 0
    v5 = snapshot(w["app"])
    (w["app"] / "smart_move_universe.py").write_text("# edited after install\n", encoding="utf-8")
    code, msgs = rb(w)
    assert code == 2 and "not exactly kit v5" in " ".join(msgs) and (w["app"] / "smart_move_universe.py").read_text() == "# edited after install\n"
    (w["app"] / "smart_move_universe.py").write_bytes(v5["smart_move_universe.py"])
    code, msgs = rb(w)
    assert code == 0 and "ROLLED BACK" in msgs[0] and snapshot(w["app"]) == w["before"] and journal_state(w) == "ROLLED_BACK"
    assert not (w["state"] / "collection.lock").exists() and not (w["state"] / "KILL").exists()
    assert rb(w)[0] == 2                                                                               # nothing left to roll back


def test_an_interrupted_rollback_also_fails_closed_and_recover_finishes_it(w, monkeypatch):
    assert ins(w)[0] == 0
    real = I.restore

    def die(m, a, b):
        raise SystemExit(9)                                                                           # the process dies in the middle of the rollback
    monkeypatch.setattr(I, "restore", die)
    with pytest.raises(SystemExit):
        rb(w)
    monkeypatch.setattr(I, "restore", real)
    assert journal_state(w) == "ROLLING_BACK" and (w["state"] / "collection.lock").exists() and (w["state"] / "KILL").exists()
    assert rb(w)[0] == 2 and ins(w)[0] == 2
    assert rec(w)[0] == 0 and snapshot(w["app"]) == w["before"] and not (w["state"] / "collection.lock").exists()


# ------------------------------------------------------------------ the installed files start
def test_the_installed_runner_starts_and_makes_no_provider_request_from_the_installed_files(w, tmp_path):
    """Compatibility: the six installed files plus the unchanged rest of the engine run a dry run (0 requests) with the old defaults and with the P1 options."""
    assert ins(w)[0] == 0
    run = tmp_path / "run"
    shutil.copytree(ROOT / "backend", run / "backend", ignore=shutil.ignore_patterns("__pycache__", "tests", ".venv", "*.db"))
    shutil.copytree(ROOT / "docs" / "smart_move" / "shadow_impl", run / "docs" / "smart_move" / "shadow_impl")
    shutil.copytree(ROOT / "docs" / "smart_move" / "shadow", run / "docs" / "smart_move" / "shadow")
    for n in K.CHANGED + K.NEW:
        shutil.copyfile(w["app"] / n, run / "backend" / n)
    store = tmp_path / "store"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("", encoding="utf-8")
    for extra in ([], ["--calendar-horizon-days", "30", "--select-policy", "deadline_priority"], ["--window-aware-evidence"]):
        r = subprocess.run([sys.executable, "-I", str(run / "backend" / "smart_move_collection_runner.py"), "--store-dir", str(store), "--state-dir", str(tmp_path / ("st" + str(len(extra)))), "--profile", "engine20", "--as-of", "2026-11-07T22:00:00+00:00"] + extra,
                           capture_output=True, text=True, cwd=str(run))
        assert r.returncode == 0, r.stderr[-400:]
        plan = json.loads((tmp_path / ("st" + str(len(extra))) / "engine20_plan.json").read_text())
        assert plan["provider_requests"] == 0 and plan["ceiling"] == 50
