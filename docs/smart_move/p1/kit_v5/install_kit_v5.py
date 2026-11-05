"""Kit v5 installer (DRAFT, local rehearsal only - it has NOT been run against any real application folder). All-or-nothing with a journal, per-file atomic replace, automatic restore on any failure, crash recovery and a verified rollback.

    install   --manifest M --payload P --app-backend A --backup-root B
    recover   --manifest M --app-backend A --backup-root B        # after a crash: journal says INSTALLING -> restore the backup
    rollback  --manifest M --app-backend A --backup-root B        # after a good install: back to the exact v4 bytes

Safety rules (every one is tested in docs/smart_move/app/tests/test_p1_kit_v5_installer.py):
  1. install refuses unless `kit_v5_manifest.verify --phase pre` passes (complete payload, expected v4 base, protected files pinned): nothing is touched on a refusal.
  2. a backup of every CHANGED file is taken and compared byte for byte BEFORE the first replace; the journal (`journal.json`, state INSTALLING) is written first.
  3. the three NEW modules are written first, then live.py, engine20.py and the runner last, so the runner never imports a module that is not there yet. Each file is written to a temp file in the same folder, hashed, then os.replace'd.
  4. any exception (including the injected failure used by the rehearsal) restores every file from the backup, removes the NEW files, verifies the result equals the v4 hashes, and exits 4. A restore that does not verify exits 5 and says so.
  5. after the last replace the install is verified (`--phase post`); the journal becomes INSTALLED. Only then does `rollback` work, and only while the installed files still equal the v5 hashes.
  6. a second installer cannot run at the same time (exclusive lock file)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_v5_manifest as K  # noqa: E402

ORDER = list(K.NEW) + ["smart_move_live.py", "smart_move_engine20.py", "smart_move_collection_runner.py"]
FAIL_AFTER_ENV, CRASH_AFTER_ENV = "KIT_V5_FAIL_AFTER", "KIT_V5_CRASH_AFTER"            # rehearsal hooks only: raise / hard-kill after N files


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _atomic_write(dst: Path, data: bytes, want_sha: str) -> None:
    tmp = dst.with_name(dst.name + ".kitv5tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if hashlib.sha256(tmp.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != want_sha:
        tmp.unlink()
        raise RuntimeError("temp copy of %s does not match its manifest SHA-256" % dst.name)
    os.replace(tmp, dst)


def _journal(bdir: Path) -> Path:
    return bdir / "journal.json"


def _write_journal(bdir: Path, **kw) -> None:
    j = {}
    if _journal(bdir).exists():
        j = json.loads(_journal(bdir).read_text(encoding="utf-8"))
    j.update(kw, updated=datetime.now(timezone.utc).isoformat())
    tmp = bdir / "journal.json.tmp"
    tmp.write_text(json.dumps(j, indent=1), encoding="utf-8")
    os.replace(tmp, _journal(bdir))


def _latest_backup(root: Path):
    d = sorted(p for p in root.glob("kit_v5_backup_*") if p.is_dir()) if root.exists() else []
    return d[-1] if d else None


def restore(manifest: dict, app: Path, bdir: Path) -> tuple:
    """Put every file back to the v4 state recorded in the backup and verify it. -> (ok, [messages])"""
    msgs, files = [], {Path(f["path"]).name: f for f in manifest["files"]}
    for n, f in files.items():
        cur = app / n
        if f["role"] == "CHANGED":
            src = bdir / "files" / n
            if not src.exists():
                msgs.append("backup file missing: %s" % n)
                continue
            _atomic_write(cur, src.read_bytes(), f["before_sha256"])
        elif cur.exists() and sha(cur) == f["after_sha256"]:
            cur.unlink()                                                                  # only our own new module is removed, never a foreign file of the same name
    for tmp in app.glob("*.kitv5tmp"):
        tmp.unlink()
    ok = True
    for n, f in files.items():
        cur = app / n
        if f["role"] == "CHANGED":
            if not cur.exists() or sha(cur) != f["before_sha256"]:
                ok = False
                msgs.append("NOT RESTORED: %s" % n)
        elif cur.exists() and sha(cur) == f["after_sha256"]:
            ok = False
            msgs.append("NEW file still present: %s" % n)
    return ok, msgs


def install(manifest: dict, payload: Path, app: Path, root: Path) -> tuple:
    code, msgs = K.verify(manifest, payload, app, "pre")
    if code != 0:
        return 2, ["install refused, nothing was changed:"] + msgs
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "kit_v5.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return 2, ["install refused: another install holds %s (remove it only if no installer is running)" % lock]
    os.close(fd)
    bdir = root / ("kit_v5_backup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
    files = {Path(f["path"]).name: f for f in manifest["files"]}
    try:
        (bdir / "files").mkdir(parents=True)
        _write_journal(bdir, state="INSTALLING", app_backend=str(app), files=ORDER, started=datetime.now(timezone.utc).isoformat())
        for n in K.CHANGED:
            shutil.copyfile(app / n, bdir / "files" / n)
            if sha(bdir / "files" / n) != files[n]["before_sha256"]:
                raise RuntimeError("backup of %s is not byte-identical to the installed file" % n)
        done = 0
        for n in ORDER:
            _atomic_write(app / n, (payload / n).read_bytes(), files[n]["after_sha256"])
            done += 1
            if os.environ.get(CRASH_AFTER_ENV) == str(done):
                os._exit(9)                                                              # simulates a power cut / kill: the journal stays INSTALLING
            if os.environ.get(FAIL_AFTER_ENV) == str(done):
                raise RuntimeError("injected failure after %d file(s)" % done)
        code, vmsgs = K.verify(manifest, None, app, "post")
        if code != 0:
            raise RuntimeError("post-install verification failed: " + " | ".join(vmsgs))
        _write_journal(bdir, state="INSTALLED")
        return 0, ["INSTALLED: six files verified; backup and journal in %s" % bdir]
    except Exception as e:  # noqa: BLE001
        ok, rmsgs = restore(manifest, app, bdir) if bdir.exists() else (True, [])
        if bdir.exists():
            _write_journal(bdir, state="RESTORED" if ok else "RESTORE_FAILED", error=str(e))
        return (4 if ok else 5), ["INSTALL FAILED (%s); %s" % (e, "every file was restored and verified" if ok else "RESTORE DID NOT VERIFY: " + "; ".join(rmsgs))]
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def recover(manifest: dict, app: Path, root: Path) -> tuple:
    bdir = _latest_backup(root)
    if bdir is None or not _journal(bdir).exists():
        return 2, ["nothing to recover: no kit-v5 backup/journal found"]
    state = json.loads(_journal(bdir).read_text(encoding="utf-8")).get("state")
    if state != "INSTALLING":
        return 2, ["nothing to recover: the last journal state is %s" % state]
    ok, msgs = restore(manifest, app, bdir)
    _write_journal(bdir, state="RESTORED" if ok else "RESTORE_FAILED")
    lock = root / "kit_v5.lock"
    if ok and lock.exists():
        lock.unlink()
    return (0 if ok else 5), ["RECOVERED: every file restored to v4 and verified" if ok else "RECOVERY DID NOT VERIFY: " + "; ".join(msgs)]


def rollback(manifest: dict, app: Path, root: Path) -> tuple:
    bdir = _latest_backup(root)
    if bdir is None or json.loads(_journal(bdir).read_text(encoding="utf-8")).get("state") != "INSTALLED":
        return 2, ["rollback refused: no completed kit-v5 install is recorded"]
    code, msgs = K.verify(manifest, None, app, "post")
    if code != 0:
        return 2, ["rollback refused: the installed files are not exactly kit v5 any more (changed after the install?):"] + msgs
    ok, rmsgs = restore(manifest, app, bdir)
    _write_journal(bdir, state="ROLLED_BACK" if ok else "RESTORE_FAILED")
    return (0 if ok else 5), ["ROLLED BACK to v4: three changed files restored, three new modules removed, hashes verified" if ok else "ROLLBACK DID NOT VERIFY: " + "; ".join(rmsgs)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("install", "recover", "rollback"))
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--payload")
    ap.add_argument("--app-backend", required=True)
    ap.add_argument("--backup-root", required=True)
    a = ap.parse_args(argv)
    try:
        manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("REFUSED: manifest unreadable:", e)
        return 2
    app, root = Path(a.app_backend), Path(a.backup_root)
    if a.cmd == "install":
        code, msgs = install(manifest, Path(a.payload) if a.payload else Path("."), app, root)
    elif a.cmd == "recover":
        code, msgs = recover(manifest, app, root)
    else:
        code, msgs = rollback(manifest, app, root)
    print("\n".join(msgs))
    return code


if __name__ == "__main__":
    sys.exit(main())
