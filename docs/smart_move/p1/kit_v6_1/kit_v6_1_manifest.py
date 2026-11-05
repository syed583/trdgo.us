"""Kit v6.1 (hotfix) manifest builder and fail-closed verifier (MONDAY_BMO_FRIDAY variant; preparation only: nothing is installed by this file, it only reads files and prints a verdict).

Kit v6.1 = installed kit v6 + exactly ONE backend file (the runner accepts --calendar-horizon-days for the variant profile):
    CHANGED (1): smart_move_collection_runner.py   (adds the `monday_bmo_friday` profile; the engine20 profile is untouched)
    PROTECTED now includes smart_move_monday_bmo.py (unchanged, never removed by a rollback)
Every other kit file is PROTECTED: it must be byte-identical (LF-normalised) in the source tree, the installed v5 base and after the install. The rules are ALL-OR-NOTHING and FAIL CLOSED:

  build   --repo <repo> --base-installed <dir with the installed v5 backend files> --out KIT_V6_MANIFEST.json
  verify  --manifest M --payload <dir>  --app-backend <installed backend dir>  --phase pre | post

  pre   (before any copy): every payload file exists and equals its after-SHA; the payload holds NO other file; the CHANGED file currently installed equals its before-SHA (an unknown base is refused, never overwritten);
        the NEW file is absent or already identical; every protected file equals its pinned SHA. Anything else: exit 2.
  post  (after a copy): both installed files equal their after-SHA and the protected files are unchanged. If ONE but not BOTH match the answer is PARTIAL (exit 3): the caller must restore the backup, not continue.
Line endings are normalised to LF before hashing (the kit is built from LF bytes)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

KIT = "kit-v6.1"
CHANGED = ("smart_move_collection_runner.py",)
NEW = ()
PROTECTED = ("smart_move_monday_bmo.py", "smart_move_engine20.py", "smart_move_live.py", "smart_move_calendar_tiers.py", "smart_move_calendar_changes.py", "smart_move_universe.py",
             "smart_move_live_publisher.py", "smart_move_calendar_guard.py", "smart_move_collection_jobs.py", "smart_move_event_records.py", "smart_move_outcomes.py")
FROZEN_PINS = {"docs/smart_move/shadow_impl/smvshadow/scorer.py": "62e09aa0ed785e2a", "backend/smart_move_v2.py": "de2d8f9612934338"}              # informational: the suite pins these; the scorer is not part of the kit change


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build(repo: Path, base_installed: Path) -> dict:
    files, problems = [], []
    for n in CHANGED + NEW:
        src = repo / "backend" / n
        if not src.exists():
            problems.append("source file missing: %s" % n)
            continue
        entry = {"path": "backend/" + n, "role": "CHANGED" if n in CHANGED else "NEW", "after_sha256": sha(src)}
        if n in CHANGED:
            inst = base_installed / n
            if not inst.exists():
                problems.append("installed base file missing: %s" % n)
                continue
            entry["before_sha256"] = sha(inst)
            if entry["before_sha256"] == entry["after_sha256"]:
                problems.append("CHANGED file is identical to the base: %s" % n)
        files.append(entry)
    protected = {}
    for n in PROTECTED:
        src, inst = repo / "backend" / n, base_installed / n
        if not src.exists() or not inst.exists():
            problems.append("protected file missing: %s" % n)
        elif sha(src) != sha(inst):
            problems.append("protected file differs between the source tree and the installed base: %s" % n)
        else:
            protected["backend/" + n] = sha(src)
    if problems:
        raise SystemExit("build refused (fail closed):\n  " + "\n  ".join(problems))
    return {"kit": KIT, "all_or_nothing": True, "files": files, "protected_unchanged": protected, "frozen_pins_informational": FROZEN_PINS,
            "note": "MONDAY_BMO_FRIDAY hotfix kit: one changed file (runner argument check); the variant module and every other file are protected. The frozen scorer, thresholds, windows and the engine20 profile are not part of this change."}


def verify(manifest: dict, payload: Path | None, app_backend: Path, phase: str) -> tuple:
    """-> (exit_code, [messages]). 0 = ok, 2 = refused (fail closed), 3 = partial install."""
    msgs, bad = [], False
    files = {Path(f["path"]).name: f for f in manifest["files"]}
    if set(files) != set(CHANGED + NEW):
        return 2, ["manifest does not list exactly the kit-v6.1 files: %s" % sorted(files)]
    if phase == "pre":
        if payload is None or not payload.is_dir():
            return 2, ["payload folder missing"]
        present = {p.name for p in payload.rglob("*") if p.is_file()}
        for extra in sorted(present - set(files)):
            msgs.append("REFUSED: unlisted file in the payload: %s" % extra)
            bad = True
        for n, f in files.items():
            p = payload / n
            if not p.exists():
                msgs.append("REFUSED: payload file missing: %s" % n)
                bad = True
            elif sha(p) != f["after_sha256"]:
                msgs.append("REFUSED: payload file does not match its manifest SHA-256: %s" % n)
                bad = True
            cur = app_backend / n
            if f["role"] == "CHANGED":
                if not cur.exists():
                    msgs.append("REFUSED: installed file missing (cannot confirm the base): %s" % n)
                    bad = True
                elif sha(cur) != f["before_sha256"]:
                    msgs.append("REFUSED: installed file is not the expected v6 base: %s" % n)
                    bad = True
            elif cur.exists() and sha(cur) != f["after_sha256"]:
                msgs.append("REFUSED: a different file with this name is already installed: %s" % n)
                bad = True
    else:
        match = [n for n, f in files.items() if (app_backend / n).exists() and sha(app_backend / n) == f["after_sha256"]]
        if len(match) != len(files):
            missing = sorted(set(files) - set(match))
            msgs.append(("PARTIAL: %d of %d files match; restore the backup. Not matching: %s" if match else "REFUSED: no kit-v6.1 file is installed. Not matching: %s") % ((len(match), len(files), missing) if match else (missing,)))
            return (3 if match else 2), msgs
    for rel, want in manifest["protected_unchanged"].items():
        p = app_backend / Path(rel).name
        if not p.exists():
            msgs.append("REFUSED: protected file missing: %s" % rel)
            bad = True
        elif sha(p) != want:
            msgs.append("REFUSED: protected file changed: %s" % rel)
            bad = True
    if bad:
        return 2, msgs
    return 0, msgs + ["OK (%s): the kit-v6.1 runner verified, protected files unchanged" % phase]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--repo", required=True)
    b.add_argument("--base-installed", required=True)
    b.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True)
    v.add_argument("--payload")
    v.add_argument("--app-backend", required=True)
    v.add_argument("--phase", choices=("pre", "post"), required=True)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        m = build(Path(a.repo), Path(a.base_installed))
        Path(a.out).write_text(json.dumps(m, indent=1), encoding="utf-8")
        print("manifest written:", a.out)
        return 0
    try:
        m = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("REFUSED: manifest unreadable:", e)
        return 2
    code, msgs = verify(m, Path(a.payload) if a.payload else None, Path(a.app_backend), a.phase)
    print("\n".join(msgs))
    return code


if __name__ == "__main__":
    sys.exit(main())
