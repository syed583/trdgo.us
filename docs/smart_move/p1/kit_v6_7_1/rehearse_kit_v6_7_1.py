"""Isolated rehearsal of the kit v6.7.1 and wrapper v2 installers on throw-away copies (never a real application folder). Exercises: pre-check, install, post verification, rollback (byte-identical v5),
interrupted install (hard crash after the first file) -> install refused -> recover -> byte-identical v5; refusal while the collector lock exists; refusal when the runner kill switch is missing;
wrapper v2 install + rollback. Prints PASS/FAIL lines and a RESULT line; exit 1 on any failure.

    python rehearse_kit_v6.py --kit-zip stage_c_kit_v6_7_1.zip --wrapper-zip stage_d_collector_wrapper_v2.zip --base-v5-backend <dir> --installed-p1-wrapper <ps1>"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PY = sys.executable
PASS = FAIL = 0


def chk(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("PASS ", name)
    else:
        FAIL += 1
        print("FAIL ", name, extra)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def run(args, env=None):
    r = subprocess.run([PY, "-I"] + args, capture_output=True, text=True, env=dict(os.environ, **(env or {})))
    return r.returncode, (r.stdout + r.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit-zip", required=True)
    ap.add_argument("--wrapper-zip", required=True)
    ap.add_argument("--base-v5-backend", required=True)
    ap.add_argument("--installed-p1-wrapper", required=True)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="k6_") as td:
        td = Path(td)
        kit = td / "kit"
        zipfile.ZipFile(a.kit_zip).extractall(kit)
        kd = kit / "stage_c_kit_v6_7_1"
        man = kd / "KIT_V6_7_1_MANIFEST.json"
        m = json.loads(man.read_text(encoding="utf-8"))
        want = {Path(f["path"]).name: f for f in m["files"]}
        for case in ("main", "crash", "locked", "nokill"):
            app = td / case / "backend"
            shutil.copytree(a.base_v5_backend, app)
            (td / case / "state").mkdir(parents=True)
            (td / case / "store").mkdir(parents=True)
            (td / case / "store" / "KILL").write_text("engaged")
        base_hashes = {p.name: sha(p) for p in (td / "main" / "backend").iterdir() if p.is_file()}
        common = lambda c: ["--manifest", str(man), "--app-backend", str(td / c / "backend"), "--backup-root", str(td / c / "backups"), "--state-root", str(td / c / "state"), "--store-dir", str(td / c / "store")]  # noqa: E731
        # pre-check
        code, out = run([str(kd / "kit_v6_7_1_manifest.py"), "verify", "--manifest", str(man), "--payload", str(kd / "payload" / "backend"), "--app-backend", str(td / "main" / "backend"), "--phase", "pre"])
        chk("pre-check on the installed v6.1 base passes", code == 0, out)
        # install
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("main"))
        chk("install returns INSTALLED", code == 0 and "INSTALLED" in out, out)
        app = td / "main" / "backend"
        chk("every changed file equals its after-hash", all(sha(app / n) == f["after_sha256"] for n, f in want.items()))
        chk("every protected file is byte-identical to the base", all(sha(app / Path(rel).name) == h for rel, h in m["protected_unchanged"].items()))
        chk("collector holds released after install", not (td / "main" / "state" / "collection.lock").exists() and not (td / "main" / "state" / "KILL").exists() and (td / "main" / "store" / "KILL").exists())
        # the installed files really carry the hourly-cap pause
        e20 = (app / "smart_move_engine20.py").read_text(encoding="utf-8")
        rn = (app / "smart_move_collection_runner.py").read_text(encoding="utf-8")
        mb = (app / "smart_move_monday_bmo.py").read_text(encoding="utf-8")
        chk("installed engine20 pauses at the hourly rail and both profiles publish with scan_ok_of", 'halted = "HOURLY_CAP"' in e20 and "def scan_ok_of(res" in e20 and 'last_denial_reason(svc) == "HOURLY_SHARED_CAP"' in e20
            and 'scan_ok=E20.scan_ok_of(res)' in rn and 'scan_ok=E20.scan_ok_of(res)' in mb and 'res["halted"] is None and res["failed"] == 0' not in rn + mb)
        # rollback
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "rollback"] + common("main"))
        chk("rollback returns ROLLED BACK", code == 0 and "ROLLED BACK" in out, out)
        chk("after rollback every file is byte-identical to the installed v6.1 base", {p.name: sha(p) for p in app.iterdir() if p.is_file()} == base_hashes)
        # install again (so the final state is v6, like production after approval)
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("main"))
        chk("second install after rollback succeeds", code == 0 and "INSTALLED" in out, out)
        # crash after the first file
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("crash"), env={"KIT_V66_CRASH_AFTER": "1"})
        chk("hard crash after one file: process died (exit 9)", code == 9, out)
        j = json.loads(sorted((td / "crash" / "backups").glob("kit_v6_7_1_backup_*"))[-1].joinpath("journal.json").read_text())
        chk("journal says INSTALLING and the holds stay (lock + wrapper KILL present)", j["state"] == "INSTALLING" and (td / "crash" / "state" / "collection.lock").exists() and (td / "crash" / "state" / "KILL").exists())
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("crash"))
        chk("install refused while interrupted (run recover first)", code == 2 and "recover" in out, out)
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "recover"] + common("crash"))
        chk("recover returns RECOVERED", code == 0 and "RECOVERED" in out, out)
        chk("after recover the files are byte-identical to the v6.1 base and holds are released", {p.name: sha(p) for p in (td / "crash" / "backend").iterdir() if p.is_file()} == base_hashes and not (td / "crash" / "state" / "collection.lock").exists() and not (td / "crash" / "state" / "KILL").exists())
        # refusals
        (td / "locked" / "state" / "collection.lock").write_text("pid=1")
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("locked"))
        chk("install refused while the collector lock exists; nothing changed", code == 2 and {p.name: sha(p) for p in (td / "locked" / "backend").iterdir() if p.is_file()} == base_hashes and (td / "locked" / "state" / "collection.lock").exists(), out)
        (td / "nokill" / "store" / "KILL").unlink()
        code, out = run([str(kd / "install_kit_v6_7_1.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("nokill"))
        chk("install refused while the runner kill switch is missing; nothing changed", code == 2 and {p.name: sha(p) for p in (td / "nokill" / "backend").iterdir() if p.is_file()} == base_hashes, out)
        # wrapper v2
        wz = td / "wrap"
        zipfile.ZipFile(a.wrapper_zip).extractall(wz)
        wd = wz / "stage_d_collector_wrapper_v2"
        wm = json.loads((wd / "WRAPPER_V2_MANIFEST.json").read_text())
        tgt = td / "wrapper_target" / "run_engine20_collection.ps1"
        tgt.parent.mkdir()
        shutil.copyfile(a.installed_p1_wrapper, tgt)
        chk("installed P1 wrapper equals the manifest before-hash", sha(tgt) == wm["before_sha256"])
        wcommon = ["--manifest", str(wd / "WRAPPER_V2_MANIFEST.json"), "--target", str(tgt), "--backup-root", str(td / "wbackups"), "--state-root", str(td / "main" / "state"), "--store-dir", str(td / "main" / "store")]
        code, out = run([str(wd / "install_wrapper_p1.py"), "install", "--payload", str(wd / "collector_tools" / "run_engine20_collection.ps1")] + wcommon)
        chk("wrapper v2 install returns INSTALLED and the target equals the after-hash", code == 0 and "INSTALLED" in out and sha(tgt) == wm["after_sha256"], out)
        code, out = run([str(wd / "install_wrapper_p1.py"), "rollback"] + wcommon)
        chk("wrapper rollback restores the P1 wrapper byte for byte", code == 0 and sha(tgt) == wm["before_sha256"], out)
        code, out = run([str(wd / "install_wrapper_p1.py"), "install", "--payload", str(wd / "collector_tools" / "run_engine20_collection.ps1")] + wcommon)
        chk("wrapper v2 installed again (final rehearsal state = v6.7.1 + wrapper v2)", code == 0 and sha(tgt) == wm["after_sha256"], out)
    print("RESULT: %d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
