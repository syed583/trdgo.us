"""Full local release rehearsal on an ISOLATED COPY of a sandbox application (never the real application, never the VPS): dashboard A.7 -> A.8 and back, engine kit v4 -> v5 and back, interrupted-install recovery,
collector wrapper migration and rollback, default settings (P1 disabled) versus P1 enabled, and the request limits. Every step prints PASS/FAIL; the exit code is non-zero on any FAIL. No provider is contacted (stub/dry-run only).

    python rehearse_p1_release.py --scratch <scratchpad> --rc <release_candidates\\p1 folder> --a7-sha <sha> --a7-anchor <hex> --a8-sha <sha> --a8-anchor <hex>"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + str(extra) if extra and not ok else ""), flush=True)
    return ok


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def snap(app: Path) -> dict:
    """Hash of every code/config file the releases may touch (not backups, records, state, caches)."""
    out = {}
    for sub in ("backend", "collector_tools", "docs/smart_move", "frontend/dist"):
        base = app / sub
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith((".pyc", ".kitv5tmp")):
                out[str(p.relative_to(app)).replace("\\", "/")] = sha(p)
    return out


def ps(*args, timeout=900):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", *map(str, args)], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def py(*args, env=None):
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, env=dict(os.environ, **(env or {})))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    ap = argparse.ArgumentParser()
    for k in ("scratch", "rc", "a7_sha", "a7_anchor", "a8_sha", "a8_anchor"):
        ap.add_argument("--" + k.replace("_", "-"), required=True)
    a = ap.parse_args()
    sc, rc = Path(a.scratch), Path(a.rc)
    work = sc / "rc_work"
    app = sc / "rc_app"
    if app.exists():
        shutil.rmtree(app)
    # ------------------------------------------------------------- S0: an isolated application copy in the INSTALLED state (A.6 monitor, kit v4, collector v3)
    shutil.copytree(sc / "sandbox_a6_app", app, ignore=shutil.ignore_patterns("__pycache__"))
    for pk in (sc / "hotfix_smlive" / "kit_v4" / "payload", sc / "hotfix_smlive" / "collector_v3" / "payload"):
        shutil.copytree(pk, app, dirs_exist_ok=True)
    (app / "sm_evidence_store").mkdir(exist_ok=True)
    (app / "sm_evidence_store" / "KILL").write_text("runner kill switch engaged\n", encoding="utf-8")
    for p in (app / "sm_collector_state").glob("collection.lock"):
        p.unlink()
    (app / "sm_collector_state" / "KILL").unlink(missing_ok=True)
    settings = app / "collector_tools" / "collector.settings.txt"
    settings.write_text("Mode=DRY_RUN\nSelectPolicy=deadline\nSelectTop=80\nAuthorize=50\nDailyLimit=100\nCalendarDaysAhead=3\n", encoding="utf-8")
    base_snap = snap(app)
    check("S0 isolated copy of the installed state built (A.6 monitor, kit v4, collector v3, DRY_RUN settings)", len(base_snap) > 100 and (app / "collector_tools" / "run_engine20_collection.ps1").exists(), len(base_snap))
    state, store, tools, backend = app / "sm_collector_state", app / "sm_evidence_store", app / "collector_tools", app / "backend"

    def norm(p):
        """The wrapper does not pass its clock override to the runner, so a wrapper-triggered plan carries the real time of the run: compare everything except that timestamp."""
        return None if p is None else {k: v for k, v in p.items() if k != "as_of"}

    def direct(tag):
        """The runner itself at a FIXED instant with the wrapper-v3 arguments: a fully deterministic plan."""
        sd = work / ("direct_" + tag)
        shutil.rmtree(sd, ignore_errors=True)
        r = subprocess.run([sys.executable, "-I", str(backend / "smart_move_collection_runner.py"), "--profile", "engine20", "--store-dir", str(store), "--state-dir", str(sd), "--select-top", "80", "--select-policy", "deadline",
                            "--calendar-days-ahead", "3", "--publish-dir", str(work / ("pub_" + tag)), "--as-of", "2026-11-04T15:00:00+00:00"], capture_output=True, text=True)
        pf = sd / "engine20_plan.json"
        return r.returncode, (json.loads(pf.read_text()) if pf.exists() else None)

    def dry(tag, extra=(), settings_file=None):
        """One wrapper trigger, DRY_RUN, fixed clock, against the sandbox store: returns (exit code, plan dict or None, console)."""
        args = [tools / "run_engine20_collection.ps1", "-Runner", backend / "smart_move_collection_runner.py", "-StoreDir", store, "-StateRoot", state, "-Python", sys.executable, "-SettingsFile", settings_file or settings,
                "-NowOverride", "2026-11-04T15:00:00Z", *extra]
        for old in state.glob("run_20261104_150000"):                                  # the wrapper names the run folder after the (fixed) clock: start each trigger from a clean one
            shutil.rmtree(old, ignore_errors=True)
        code, out = ps(*args)
        pf = state / "run_20261104_150000" / "engine20_plan.json"
        plan = json.loads(pf.read_text()) if pf.exists() else None
        return code, plan, out

    # ------------------------------------------------------------- S1: default behaviour BEFORE anything is installed (v4 runner, v3 wrapper)
    cd0, direct_v4 = direct("v4")
    c0, plan_v4, out0 = dry("v4")
    check("S1 baseline dry run on kit v4 + wrapper v3 works and sends 0 provider requests", c0 == 0 and plan_v4 and plan_v4["provider_requests"] == 0 and plan_v4["ceiling"] == 50, out0[-300:])

    # ------------------------------------------------------------- S2: dashboard A.7 -> A.8 -> rollback -> A.8 (isolated app, no restart)
    c, o = ps(work / "a7_run_sandbox.ps1", "-Mode", "PreCheck")
    check("S2 A.7 PreCheck passes on the isolated copy", c == 0 and "PRECHECK PASS" in o, o[-300:])
    c, o = ps(work / "a7_run_sandbox.ps1", "-Mode", "Update", "-ConfirmWord", "YES-INSTALL-A7", "-ApproveCommit", a.a7_sha, "-ApproveManifest", a.a7_anchor)
    check("S2 A.7 Update completes (backup verified, automatic restore armed)", c == 0 and "UPDATE COMPLETED" in o, o[-400:])
    c, o = ps(work / "a7_run_sandbox.ps1", "-Mode", "Rollback", "-ConfirmWord", "YES-ROLLBACK-A7")
    check("S2 A.7 Rollback returns the isolated copy byte-for-byte to the A.6 monitor state", c == 0 and snap(app) == base_snap, o[-300:])
    c, o = ps(work / "a7_run_sandbox.ps1", "-Mode", "Update", "-ConfirmWord", "YES-INSTALL-A7", "-ApproveCommit", a.a7_sha, "-ApproveManifest", a.a7_anchor)
    check("S2 A.7 installed again", c == 0 and "UPDATE COMPLETED" in o, o[-300:])
    snap_a7 = snap(app)
    c, o = ps(work / "a8_run_sandbox.ps1", "-Mode", "PreCheck")
    check("S2 A.8 PreCheck passes (release commit, anchor, 139 files, content gates)", c == 0 and "PRECHECK PASS" in o, o[-400:])
    c, o = ps(work / "a8_run_sandbox.ps1", "-Mode", "Update", "-ConfirmWord", "YES-INSTALL-A8", "-ApproveCommit", a.a8_sha, "-ApproveManifest", a.a8_anchor)
    check("S2 A.8 Update completes", c == 0 and "UPDATE COMPLETED" in o, o[-400:])
    c, o = ps(work / "a8_run_sandbox.ps1", "-Mode", "PostCheck")
    check("S2 A.8 PostCheck passes", c == 0 and "POSTCHECK PASS" in o, o[-300:])
    rt = (app / "backend" / "earnings_monitor" / "routes.py").read_text(encoding="utf-8")
    check("S2 the installed monitor has the universe route; the installed dashboard bundle has the tab", "/smart-move/universe" in rt and any("sm-universe-view" in p.read_text(encoding="utf-8", errors="ignore") for p in (app / "frontend" / "dist" / "assets").glob("SmartMoveEngine20Page-*.js")))
    c, o = ps(work / "a8_run_sandbox.ps1", "-Mode", "Rollback", "-ConfirmWord", "YES-ROLLBACK-A8")
    check("S2 A.8 Rollback completes and the monitor files are byte-identical to the A.7 state", c == 0 and snap(app) == snap_a7, o[-300:])
    c, o = ps(work / "a8_run_sandbox.ps1", "-Mode", "Update", "-ConfirmWord", "YES-INSTALL-A8", "-ApproveCommit", a.a8_sha, "-ApproveManifest", a.a8_anchor)
    check("S2 A.8 can be installed again after its rollback", c == 0 and "UPDATE COMPLETED" in o, o[-300:])
    snap_a8 = snap(app)

    # ------------------------------------------------------------- S3: engine kit v4 -> v5 from the RELEASE ZIP, default settings (P1 disabled)
    z = rc / "stage_c_kit_v5.zip"
    kit = work / "kit_x"
    if kit.exists():
        shutil.rmtree(kit)
    zipfile.ZipFile(z).extractall(kit)
    kdir = kit / "stage_c_kit_v5"
    lines = (kdir / "MANIFEST.sha256").read_text().splitlines()
    check("S3 kit ZIP content matches its MANIFEST.sha256", all(sha(kdir / l.split("  ", 1)[1]) == l.split("  ", 1)[0] for l in lines) or all(hashlib.sha256((kdir / l.split("  ", 1)[1]).read_bytes()).hexdigest() == l.split("  ", 1)[0] for l in lines))
    kargs = ["--manifest", kdir / "KIT_V5_MANIFEST.json", "--app-backend", backend, "--backup-root", work / "kit_backups", "--state-root", state, "--store-dir", store]
    pre_kit = snap(app)
    c, o = py(kdir / "install_kit_v5.py", "install", *kargs, "--payload", kdir / "payload" / "backend")
    check("S3 kit v5 installs (collector idle, baseline matched, six files verified)", c == 0 and "INSTALLED" in o, o[-400:])
    check("S3 only the six kit files changed", {k for k in snap(app) if snap(app)[k] != pre_kit.get(k)} == {"backend/" + n for n in ("smart_move_collection_runner.py", "smart_move_engine20.py", "smart_move_live.py", "smart_move_calendar_tiers.py", "smart_move_calendar_changes.py", "smart_move_universe.py")})
    cd1, direct_v5 = direct("v5")
    check("S3 DEFAULT settings on kit v5: the runner plan at a fixed instant is IDENTICAL to the kit v4 plan (P1 disabled, no behaviour change)", cd0 == 0 and cd1 == 0 and direct_v4 and direct_v4 == direct_v5)
    c1, plan_v5, out1 = dry("v5-default")
    check("S3 DEFAULT settings through the old wrapper: same plan as before the install (apart from the run timestamp)", c1 == 0 and norm(plan_v5) == norm(plan_v4), out1[-300:])
    check("S3 no P1 state file is written with P1 disabled", not (state / "live" / "universe.json").exists() and not (state / "live" / "calendar_slots.json").exists())

    # ------------------------------------------------------------- S4: wrapper migration (settings file untouched), default then P1 enabled
    wz = rc / "stage_d_collector_p1_wrapper.zip"
    wx = work / "wrapper_x"
    if wx.exists():
        shutil.rmtree(wx)
    zipfile.ZipFile(wz).extractall(wx)
    wdir = wx / "stage_d_collector_p1_wrapper"
    s_before = settings.read_bytes()
    wargs = ["--manifest", wdir / "WRAPPER_P1_MANIFEST.json", "--target", tools / "run_engine20_collection.ps1", "--backup-root", work / "wrapper_backups", "--state-root", state, "--store-dir", store]
    c, o = py(wdir / "install_wrapper_p1.py", "install", *wargs, "--payload", wdir / "collector_tools" / "run_engine20_collection.ps1")
    check("S4 wrapper P1 installs over the v3 wrapper (baseline matched); the settings file is untouched", c == 0 and "INSTALLED" in o and settings.read_bytes() == s_before, o[-300:])
    c2, plan_w, out2 = dry("w-default")
    check("S4 DEFAULT settings with the P1 wrapper: same plan as the v3 wrapper produced (apart from the run timestamp): no migration needed", c2 == 0 and norm(plan_w) == norm(plan_v4), out2[-300:])
    en = work / "settings_p1.txt"
    en.write_text(settings.read_text() + "CalendarHorizonDays=30\nSelectPolicy=deadline_priority\n", encoding="utf-8")
    c3, plan_p1, out3 = dry("p1", settings_file=en)
    check("S4 P1 ENABLED (CalendarHorizonDays=30, deadline_priority, window-aware implied): tiered plan present, 0 provider requests, ceiling 50", c3 == 0 and plan_p1 and "calendar_tiered" in plan_p1 and plan_p1["provider_requests"] == 0 and plan_p1["ceiling"] == 50, out3[-300:])
    check("S4 the P1-enabled dry run writes the event universe and still no calendar state", (state / "live" / "universe.json").exists() and not (state / "live" / "calendar_slots.json").exists())
    uni = json.loads((state / "live" / "universe.json").read_text())
    check("S4 universe.json is well formed: schema, horizon 30, every row has a status", uni["schema"] == "smart-move-universe-1" and uni["horizon_days"] == 30 and all(r["collection_status"] for r in uni["rows"]), len(uni["rows"]))
    bad = [(["-Authorize", "151"]), (["-DailyLimit", "701"]), (["-CalendarHorizonDays", "2"]), (["-CalendarHorizonDays", "31"])]
    check("S4 limits above the design ceilings (150 / 700) and bad horizons are refused before the runner starts", all(dry("bad", extra=b)[0] == 2 for b in bad))
    envs = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, text=True)
    check("S4 the approved 50 / 100 are what the settings pin (Authorize=50, DailyLimit=100); the plan ceiling equals 50", "Authorize=50" in settings.read_text() and "DailyLimit=100" in settings.read_text() and plan_p1 and plan_p1["ceiling"] == 50)
    cl, plan_l, outl = dry("live-no-attestation", extra=["-Live"])
    check("S4 a LIVE trigger without the owner's attestation is refused (exit 2) - nothing can run live from this rehearsal", cl == 2 and "attestation" in outl.lower(), outl[-200:])
    r = subprocess.run([sys.executable, str(backend / "smart_move_collection_runner.py"), "--store-dir", str(store), "--state-dir", str(work / "st_live"), "--profile", "engine20", "--mode", "live", "--arm", "--authorize-requests", "51"], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(backend / "smart_move_collection_runner.py"), "--store-dir", str(store), "--state-dir", str(work / "st_live2"), "--profile", "engine20", "--mode", "live", "--arm", "--authorize-requests", "50"], capture_output=True, text=True)
    check("S4 the runner refuses 51 requests, and refuses a live run without the machine flag and attestation", r.returncode == 6 and r2.returncode == 6, (r.stdout + r2.stdout)[-300:])

    # ------------------------------------------------------------- S5: interrupted kit install recovery in the full environment (wrapper must stay blocked while recovering)
    c, o = py(wdir / "install_wrapper_p1.py", "rollback", *wargs)
    check("S5 wrapper rollback restores the v3 wrapper exactly", c == 0 and sha(tools / "run_engine20_collection.ps1") == json.loads((wdir / "WRAPPER_P1_MANIFEST.json").read_text())["before_sha256"], o[-200:])
    c, o = py(kdir / "install_kit_v5.py", "rollback", *kargs)
    check("S5 kit v5 -> v4 rollback is byte-exact against the pre-install snapshot", c == 0 and snap(app) == {**pre_kit, **{}} or snap(app) == pre_kit, o[-300:])
    c, o = py(kdir / "install_kit_v5.py", "install", *kargs, "--payload", kdir / "payload" / "backend", env={"KIT_V5_CRASH_AFTER": "4"})
    check("S5 a hard kill after four files leaves an INTERRUPTED state", c == 9 and (state / "collection.lock").exists() and (state / "KILL").exists(), o[-200:])
    ck, plan_k, outk = dry("blocked-while-interrupted")
    check("S5 the collector wrapper SKIPS its trigger while the installation is unfinished (kill switch)", ck == 0 and "SKIPPED" in outk and "kill" in outk.lower(), outk[-200:])
    c, o = py(kdir / "install_kit_v5.py", "install", *kargs, "--payload", kdir / "payload" / "backend")
    check("S5 a new install is refused until recovery (fail closed)", c == 2 and "recover" in o, o[-200:])
    c, o = py(kdir / "install_kit_v5.py", "recover", *kargs)
    check("S5 recover restores v4 exactly and releases the holds", c == 0 and snap(app) == pre_kit and not (state / "collection.lock").exists() and not (state / "KILL").exists(), o[-300:])
    ck2, plan_k2, outk2 = dry("after-recovery")
    cd2, direct_rec = direct("recovered")
    check("S5 the collector runs again after recovery with the original plan (wrapper, and the runner at the fixed instant)", ck2 == 0 and norm(plan_k2) == norm(plan_v4) and direct_rec == direct_v4, outk2[-200:])

    # ------------------------------------------------------------- S6: dashboard rollback to the installed state
    c, o = ps(work / "a8_run_sandbox.ps1", "-Mode", "Rollback", "-ConfirmWord", "YES-ROLLBACK-A8")
    final = snap(app)
    diff = sorted(k for k in set(final) | set(snap_a7) if final.get(k) != snap_a7.get(k))
    check("S6 A.8 rollback; kit v4 and wrapper v3 are in place: the application is byte-identical to the state before this release (A.7 monitor + kit v4 + wrapper v3)", c == 0 and not diff, diff[:8])
    c, o = ps(work / "a8_run_sandbox.ps1", "-Mode", "Rollback", "-ConfirmWord", "YES-ROLLBACK-A8")
    check("S6 a second A.8 rollback is refused (the updater keeps ONE recorded level: nothing left to roll back)", c != 0 and "nothing to roll back" in o, o[-200:])
    print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
