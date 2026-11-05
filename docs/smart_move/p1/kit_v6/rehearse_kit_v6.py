"""Isolated rehearsal of the kit v6 and wrapper v2 installers on throw-away copies (never a real application folder). Exercises: pre-check, install, post verification, rollback (byte-identical v5),
interrupted install (hard crash after the first file) -> install refused -> recover -> byte-identical v5; refusal while the collector lock exists; refusal when the runner kill switch is missing;
wrapper v2 install + rollback. Prints PASS/FAIL lines and a RESULT line; exit 1 on any failure.

    python rehearse_kit_v6.py --kit-zip stage_c_kit_v6.zip --wrapper-zip stage_d_collector_wrapper_v2.zip --base-v5-backend <dir> --installed-p1-wrapper <ps1>"""
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
        kd = kit / "stage_c_kit_v6"
        man = kd / "KIT_V6_MANIFEST.json"
        m = json.loads(man.read_text(encoding="utf-8"))
        want = {Path(f["path"]).name: f for f in m["files"]}
        for case in ("main", "crash", "locked", "nokill"):
            app = td / case / "backend"
            shutil.copytree(a.base_v5_backend, app)
            (td / case / "state").mkdir(parents=True)
            (td / case / "store").mkdir(parents=True)
            (td / case / "store" / "KILL").write_text("engaged")
        base_hashes = {p.name: sha(p) for p in (td / "main" / "backend").iterdir()}
        common = lambda c: ["--manifest", str(man), "--app-backend", str(td / c / "backend"), "--backup-root", str(td / c / "backups"), "--state-root", str(td / c / "state"), "--store-dir", str(td / c / "store")]  # noqa: E731
        # pre-check
        code, out = run([str(kd / "kit_v6_manifest.py"), "verify", "--manifest", str(man), "--payload", str(kd / "payload" / "backend"), "--app-backend", str(td / "main" / "backend"), "--phase", "pre"])
        chk("pre-check on the v5 base passes", code == 0, out)
        # install
        code, out = run([str(kd / "install_kit_v6.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("main"))
        chk("install returns INSTALLED", code == 0 and "INSTALLED" in out, out)
        app = td / "main" / "backend"
        chk("runner and monday_bmo module equal their after-hashes", all(sha(app / n) == f["after_sha256"] for n, f in want.items()))
        chk("every protected file is byte-identical to the base", all(sha(app / Path(rel).name) == h for rel, h in m["protected_unchanged"].items()))
        chk("collector holds released after install", not (td / "main" / "state" / "collection.lock").exists() and not (td / "main" / "state" / "KILL").exists() and (td / "main" / "store" / "KILL").exists())
        # the installed runner really knows the profile
        code, out = run([str(app / "smart_move_collection_runner.py"), "--help"])
        chk("installed runner lists the monday_bmo_friday profile", "monday_bmo_friday" in out, out[-300:])
        # rollback
        code, out = run([str(kd / "install_kit_v6.py"), "rollback"] + common("main"))
        chk("rollback returns ROLLED BACK", code == 0 and "ROLLED BACK" in out, out)
        chk("after rollback every file is byte-identical to the v5 base and the new module is gone", {p.name: sha(p) for p in app.iterdir()} == base_hashes)
        # install again (so the final state is v6, like production after approval)
        code, out = run([str(kd / "install_kit_v6.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("main"))
        chk("second install after rollback succeeds", code == 0 and "INSTALLED" in out, out)
        # crash after the first file
        code, out = run([str(kd / "install_kit_v6.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("crash"), env={"KIT_V6_CRASH_AFTER": "1"})
        chk("hard crash after one file: process died (exit 9)", code == 9, out)
        j = json.loads(sorted((td / "crash" / "backups").glob("kit_v6_backup_*"))[-1].joinpath("journal.json").read_text())
        chk("journal says INSTALLING and the holds stay (lock + wrapper KILL present)", j["state"] == "INSTALLING" and (td / "crash" / "state" / "collection.lock").exists() and (td / "crash" / "state" / "KILL").exists())
        code, out = run([str(kd / "install_kit_v6.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("crash"))
        chk("install refused while interrupted (run recover first)", code == 2 and "recover" in out, out)
        code, out = run([str(kd / "install_kit_v6.py"), "recover"] + common("crash"))
        chk("recover returns RECOVERED", code == 0 and "RECOVERED" in out, out)
        chk("after recover the files are byte-identical to the v5 base and holds are released", {p.name: sha(p) for p in (td / "crash" / "backend").iterdir()} == base_hashes and not (td / "crash" / "state" / "collection.lock").exists() and not (td / "crash" / "state" / "KILL").exists())
        # refusals
        (td / "locked" / "state" / "collection.lock").write_text("pid=1")
        code, out = run([str(kd / "install_kit_v6.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("locked"))
        chk("install refused while the collector lock exists; nothing changed", code == 2 and {p.name: sha(p) for p in (td / "locked" / "backend").iterdir()} == base_hashes and (td / "locked" / "state" / "collection.lock").exists(), out)
        (td / "nokill" / "store" / "KILL").unlink()
        code, out = run([str(kd / "install_kit_v6.py"), "install", "--payload", str(kd / "payload" / "backend")] + common("nokill"))
        chk("install refused while the runner kill switch is missing; nothing changed", code == 2 and {p.name: sha(p) for p in (td / "nokill" / "backend").iterdir()} == base_hashes, out)
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
        chk("wrapper v2 installed again (final rehearsal state = v6 + wrapper v2)", code == 0 and sha(tgt) == wm["after_sha256"], out)
    print("RESULT: %d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
