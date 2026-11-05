"""Kit v5 installer rehearsal (LOCAL, temp folders only; no real application folder is touched). The 'installed v4' is rebuilt from the git blobs of 274fccac (verified equal to kit v4 / collector v3)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
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
    for d in (app, payload, base):
        d.mkdir()
    for n in K.CHANGED + K.PROTECTED:
        (app / n).write_bytes(blob(n))
        (base / n).write_bytes(blob(n))
    (app / "other_unrelated_module.py").write_text("# not ours\n", encoding="utf-8")
    for n in K.CHANGED + K.NEW:
        shutil.copyfile(ROOT / "backend" / n, payload / n)
    manifest = K.build(ROOT, base)
    mf = tmp_path / "KIT_V5_MANIFEST.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    return dict(app=app, payload=payload, root=root, manifest=manifest, mf=mf, before={p.name: p.read_bytes() for p in app.iterdir()}, tmp=tmp_path)


def snapshot(app):
    return {p.name: p.read_bytes() for p in app.iterdir()}


def cli(w, cmd, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, str(KDIR / "install_kit_v5.py"), cmd, "--manifest", str(w["mf"]), "--payload", str(w["payload"]), "--app-backend", str(w["app"]), "--backup-root", str(w["root"])],
                          capture_output=True, text=True, env=e)


def test_a_clean_install_replaces_exactly_the_six_files_keeps_a_verified_backup_and_leaves_everything_else_alone(w):
    code, msgs = I.install(w["manifest"], w["payload"], w["app"], w["root"])
    assert code == 0 and "INSTALLED" in msgs[0]
    after = snapshot(w["app"])
    assert set(after) - set(w["before"]) == set(K.NEW) and not [n for n in after if n.endswith(".kitv5tmp")]
    assert all(after[n] != w["before"][n] for n in K.CHANGED) and all(after[n] == w["before"][n] for n in set(w["before"]) - set(K.CHANGED))      # protected and unrelated files untouched
    assert K.verify(w["manifest"], None, w["app"], "post")[0] == 0
    bdir = I._latest_backup(w["root"])
    assert json.loads((bdir / "journal.json").read_text())["state"] == "INSTALLED" and all((bdir / "files" / n).read_bytes() == w["before"][n] for n in K.CHANGED) and not (w["root"] / "kit_v5.lock").exists()


def test_a_second_installer_cannot_run_while_the_lock_is_held(w):
    w["root"].mkdir()
    (w["root"] / "kit_v5.lock").write_text("held", encoding="utf-8")
    code, msgs = I.install(w["manifest"], w["payload"], w["app"], w["root"])
    assert code == 2 and "holds" in msgs[0] and snapshot(w["app"]) == w["before"]


def test_install_order_puts_the_new_modules_before_the_runner_that_imports_them():
    assert I.ORDER[:3] == list(K.NEW) and I.ORDER[-1] == "smart_move_collection_runner.py"


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_a_forced_failure_after_any_number_of_files_restores_the_exact_v4_state(w, n, monkeypatch):
    monkeypatch.setenv(I.FAIL_AFTER_ENV, str(n))
    code, msgs = I.install(w["manifest"], w["payload"], w["app"], w["root"])
    assert code == 4 and "every file was restored and verified" in msgs[0]
    assert snapshot(w["app"]) == w["before"]                                                          # byte-identical to before, no new module, no temp file
    assert json.loads((I._latest_backup(w["root"]) / "journal.json").read_text())["state"] == "RESTORED" and not (w["root"] / "kit_v5.lock").exists()


def test_a_write_error_in_the_middle_is_handled_the_same_way(w, monkeypatch):
    real = os.replace

    def flaky(src, dst):
        if str(dst).endswith("smart_move_engine20.py") and str(src).endswith(".kitv5tmp") and not flaky.failed:
            flaky.failed = True
            raise OSError("disk full (injected)")                                                      # the fifth file of six
        return real(src, dst)
    flaky.failed = False
    monkeypatch.setattr(I.os, "replace", flaky)
    code, msgs = I.install(w["manifest"], w["payload"], w["app"], w["root"])
    assert code == 4 and snapshot(w["app"]) == w["before"]


def test_a_hard_crash_leaves_a_partial_state_that_verification_flags_and_recover_repairs(w):
    r = cli(w, "install", {I.CRASH_AFTER_ENV: "4"})                                                   # os._exit after four files: no cleanup runs
    assert r.returncode == 9
    mid = snapshot(w["app"])
    assert mid != w["before"] and K.verify(w["manifest"], None, w["app"], "post")[0] == 3            # PARTIAL is detected, never mistaken for success
    assert json.loads((I._latest_backup(w["root"]) / "journal.json").read_text())["state"] == "INSTALLING" and (w["root"] / "kit_v5.lock").exists()
    again = cli(w, "install")
    assert again.returncode == 2 and "not the expected v4 base" in again.stdout                      # a second install cannot start over a half-installed state (and nothing is touched)
    assert snapshot(w["app"]) == mid
    rec = cli(w, "recover")
    assert rec.returncode == 0 and "RECOVERED" in rec.stdout and snapshot(w["app"]) == w["before"] and not (w["root"] / "kit_v5.lock").exists()
    assert cli(w, "install").returncode == 0                                                         # and a clean install works afterwards


def test_rollback_from_v5_to_v4_is_exact_and_refuses_when_the_installed_files_changed_afterwards(w):
    assert I.install(w["manifest"], w["payload"], w["app"], w["root"])[0] == 0
    v5 = snapshot(w["app"])
    (w["app"] / "smart_move_universe.py").write_text("# edited after install\n", encoding="utf-8")
    code, msgs = I.rollback(w["manifest"], w["app"], w["root"])
    assert code == 2 and "not exactly kit v5" in " ".join(msgs) and (w["app"] / "smart_move_universe.py").read_text() == "# edited after install\n"
    (w["app"] / "smart_move_universe.py").write_bytes(v5["smart_move_universe.py"])
    code, msgs = I.rollback(w["manifest"], w["app"], w["root"])
    assert code == 0 and "ROLLED BACK" in msgs[0] and snapshot(w["app"]) == w["before"]
    assert json.loads((I._latest_backup(w["root"]) / "journal.json").read_text())["state"] == "ROLLED_BACK"
    assert I.rollback(w["manifest"], w["app"], w["root"])[0] == 2                                      # nothing left to roll back


def test_refusals_touch_nothing_unknown_base_missing_payload_file_wrong_hash_second_install(w):
    (w["app"] / "smart_move_live.py").write_bytes(b"# some other version\n")
    before = snapshot(w["app"])
    code, msgs = I.install(w["manifest"], w["payload"], w["app"], w["root"])
    assert code == 2 and snapshot(w["app"]) == before and not w["root"].exists()                       # refused before a backup folder was even created
    (w["app"] / "smart_move_live.py").write_bytes(w["before"]["smart_move_live.py"])
    (w["payload"] / "smart_move_universe.py").unlink()
    assert I.install(w["manifest"], w["payload"], w["app"], w["root"])[0] == 2 and snapshot(w["app"]) == w["before"]
    shutil.copyfile(ROOT / "backend" / "smart_move_universe.py", w["payload"] / "smart_move_universe.py")
    assert I.install(w["manifest"], w["payload"], w["app"], w["root"])[0] == 0
    assert I.install(w["manifest"], w["payload"], w["app"], w["root"])[0] == 2                         # installing twice is refused: the base is no longer v4


def test_the_installed_runner_starts_and_makes_no_provider_request_from_the_installed_files(w, tmp_path):
    """Compatibility: the six installed files plus the unchanged rest of the engine run a dry run (0 requests) with the old defaults and with the P1 options."""
    assert I.install(w["manifest"], w["payload"], w["app"], w["root"])[0] == 0
    run = tmp_path / "run"
    shutil.copytree(ROOT / "backend", run / "backend", ignore=shutil.ignore_patterns("__pycache__", "tests", ".venv", "*.db"))
    shutil.copytree(ROOT / "docs" / "smart_move" / "shadow_impl", run / "docs" / "smart_move" / "shadow_impl")
    shutil.copytree(ROOT / "docs" / "smart_move" / "shadow", run / "docs" / "smart_move" / "shadow")
    for n in K.CHANGED + K.NEW:
        shutil.copyfile(w["app"] / n, run / "backend" / n)                                              # exactly what the installer wrote
    store = tmp_path / "store"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("", encoding="utf-8")
    for extra in ([], ["--calendar-horizon-days", "30", "--select-policy", "deadline_priority"], ["--window-aware-evidence"]):
        r = subprocess.run([sys.executable, "-I", str(run / "backend" / "smart_move_collection_runner.py"), "--store-dir", str(store), "--state-dir", str(tmp_path / ("st" + str(len(extra)))), "--profile", "engine20", "--as-of", "2026-11-07T22:00:00+00:00"] + extra,
                           capture_output=True, text=True, cwd=str(run))
        assert r.returncode == 0, r.stderr[-400:]
        plan = json.loads((tmp_path / ("st" + str(len(extra))) / "engine20_plan.json").read_text())
        assert plan["provider_requests"] == 0 and plan["ceiling"] == 50
