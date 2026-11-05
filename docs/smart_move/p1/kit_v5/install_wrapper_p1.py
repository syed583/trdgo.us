"""Collector wrapper release candidate installer (rehearsed only on isolated local copies). Replaces ONE file, `collector_tools\\run_engine20_collection.ps1`, with the P1 wrapper, under the same collector protections as the kit installer
(it imports them): no active collector lock, runner kill switch present, wrapper kill switch respected, collector held off for the duration, one operation at a time, installed file must equal the approved v3/v4 wrapper SHA-256.

    install   --manifest WRAPPER_P1_MANIFEST.json --payload <file> --target <installed run_engine20_collection.ps1> --backup-root B --state-root S --store-dir E
    rollback  --manifest ... --target ... --backup-root B --state-root S --store-dir E

The settings file (`collector.settings.txt`) is NEVER touched: every existing key keeps its meaning, and the new keys (CalendarHorizonDays, SelectPolicy=deadline_priority) are optional and absent unless the owner adds them. P1 therefore stays disabled after the install."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_kit_v5 as I  # noqa: E402


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _bdir(root: Path):
    d = sorted(p for p in root.glob("wrapper_p1_backup_*") if p.is_dir()) if root.exists() else []
    return d[-1] if d else None


def _state(b: Path) -> str:
    return json.loads((b / "journal.json").read_text(encoding="utf-8")).get("state") if (b / "journal.json").exists() else ""


def install(m: dict, payload: Path, target: Path, root: Path, state_root: Path, store: Path) -> tuple:
    try:
        lock = I.OpLock(root / "wrapper_p1.op.lock").__enter__()
    except OSError:
        return 2, ["install refused: another wrapper operation is running; nothing was changed"]
    try:
        problems = I.collector_problems(state_root, store)
        b = _bdir(root)
        if b is not None and _state(b) in ("INSTALLING", "RESTORE_FAILED"):
            problems.append("an earlier wrapper install was interrupted: run rollback first")
        if not payload.exists() or sha(payload) != m["after_sha256"]:
            problems.append("payload missing or not the approved wrapper (SHA-256 mismatch)")
        if not target.exists() or sha(target) != m["before_sha256"]:
            problems.append("installed wrapper is not the approved baseline (SHA-256 mismatch or missing)")
        if problems:
            return 2, ["install refused, nothing was changed:"] + problems
        bdir = root / ("wrapper_p1_backup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
        bdir.mkdir(parents=True)
        (bdir / "orig.ps1").write_bytes(target.read_bytes())
        if sha(bdir / "orig.ps1") != m["before_sha256"]:
            return 2, ["backup is not byte-identical: refused"]
        made = I.hold(state_root, "wrapper install")
        (bdir / "journal.json").write_text(json.dumps({"state": "INSTALLING", "holds": made}), encoding="utf-8")
        try:
            I._atomic_write(target, payload.read_bytes(), m["after_sha256"])
            if sha(target) != m["after_sha256"]:
                raise RuntimeError("post-install hash mismatch")
            (bdir / "journal.json").write_text(json.dumps({"state": "INSTALLED", "holds": made}), encoding="utf-8")
            I.release(state_root, made)
            return 0, ["INSTALLED: wrapper verified; settings file untouched; P1 options stay off until added to the settings"]
        except Exception as e:  # noqa: BLE001
            I._atomic_write(target, (bdir / "orig.ps1").read_bytes(), m["before_sha256"])
            ok = sha(target) == m["before_sha256"]
            (bdir / "journal.json").write_text(json.dumps({"state": "RESTORED" if ok else "RESTORE_FAILED", "holds": made}), encoding="utf-8")
            if ok:
                I.release(state_root, made)
            return (4 if ok else 5), ["INSTALL FAILED (%s); %s" % (e, "previous wrapper restored and verified" if ok else "RESTORE DID NOT VERIFY, the collector stays blocked")]
    finally:
        lock.__exit__()


def rollback(m: dict, target: Path, root: Path, state_root: Path, store: Path) -> tuple:
    try:
        lock = I.OpLock(root / "wrapper_p1.op.lock").__enter__()
    except OSError:
        return 2, ["rollback refused: another wrapper operation is running"]
    try:
        b = _bdir(root)
        problems = I.collector_problems(state_root, store, allow_own_hold=True)
        if b is None or _state(b) not in ("INSTALLED", "INSTALLING", "RESTORE_FAILED"):
            problems.append("no wrapper install to roll back")
        if problems:
            return 2, ["rollback refused, nothing was changed:"] + problems
        j = json.loads((b / "journal.json").read_text(encoding="utf-8"))
        if _state(b) == "INSTALLED" and sha(target) != m["after_sha256"]:
            return 2, ["rollback refused: the installed wrapper is not exactly the P1 wrapper any more"]
        made = j.get("holds") or {}
        extra = I.hold(state_root, "wrapper rollback") if _state(b) == "INSTALLED" else {}
        I._atomic_write(target, (b / "orig.ps1").read_bytes(), m["before_sha256"])
        ok = sha(target) == m["before_sha256"]
        (b / "journal.json").write_text(json.dumps({"state": "ROLLED_BACK" if ok else "RESTORE_FAILED", "holds": made}), encoding="utf-8")
        if ok:
            I.release(state_root, extra if extra else made)
        return (0 if ok else 5), ["ROLLED BACK: previous wrapper restored and verified" if ok else "ROLLBACK DID NOT VERIFY, the collector stays blocked"]
    finally:
        lock.__exit__()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("install", "rollback"))
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--payload")
    ap.add_argument("--target", required=True)
    ap.add_argument("--backup-root", required=True)
    ap.add_argument("--state-root", required=True)
    ap.add_argument("--store-dir", required=True)
    a = ap.parse_args(argv)
    m = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    root, st, store, target = Path(a.backup_root), Path(a.state_root), Path(a.store_dir), Path(a.target)
    code, msgs = install(m, Path(a.payload) if a.payload else Path("."), target, root, st, store) if a.cmd == "install" else rollback(m, target, root, st, store)
    print("\n".join(msgs))
    return code


if __name__ == "__main__":
    sys.exit(main())
