"""Kit v6.1 installer (MONDAY_BMO_FRIDAY runner hotfix: one changed file, nothing new; rehearsed ONLY on isolated local copies, never run against a real application folder). All-or-nothing with a journal, per-file atomic replace, automatic restore on failure,
fail-closed behaviour after an interruption, and a verified rollback.

    install   --manifest M --payload P --app-backend A --backup-root B --state-root S --store-dir E
    recover   --manifest M            --app-backend A --backup-root B --state-root S --store-dir E     # after an interruption: restore the backup, verify, release the holds
    rollback  --manifest M            --app-backend A --backup-root B --state-root S --store-dir E     # after a good install: back to the exact v4 bytes

  S = the collector state folder (holds `collection.lock` and the WRAPPER kill switch `KILL`);  E = the evidence store (holds the RUNNER kill switch `KILL`).

Collector protection (install / rollback; every refusal changes nothing):
  * `S\\collection.lock` present            -> refuse (the collector is running, or a run was interrupted). The installer never deletes a lock it did not create.
  * `E\\KILL` (runner switch) missing        -> refuse: the runner releases that file only while a live run is working, so a missing file means a run may be in progress.
  * wrapper `S\\KILL`                        -> respected: if it exists the collector is deliberately off and the file is never touched; if it does not exist the installer ENGAGES it for the duration (the wrapper skips every
                                               trigger while it exists) and removes only the file it created. The installer also takes `S\\collection.lock` for the duration so even an older wrapper cannot start a run.
  * installed files must equal the approved v5 SHA-256 baseline (the manifest's before-hashes) and the payload must equal the approved v6 hashes, otherwise refuse.
Concurrency: one operation at a time. install, recover and rollback all take an OS-level exclusive lock on `B\\kit_v6_1.op.lock`; the OS releases it if the process dies, so a crash never leaves a stale lock but a second operation can never overlap.
Interruption: while the newest journal says INSTALLING or RESTORE_FAILED, install and rollback REFUSE ("run recover first"), the collector lock and the wrapper kill switch the installer created stay in place (the collector cannot run on half-installed code),
and only `recover` can finish the job. After a verified restore the holds the installer created are released.
Install order: the runner only; each file goes to a temp file, is hashed, then os.replace'd. Everything is verified again after the last replace."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_v6_1_manifest as K  # noqa: E402

ORDER = list(K.NEW) + list(K.CHANGED)                                                                # the new module first, the runner (which imports it) last
FAIL_AFTER_ENV, CRASH_AFTER_ENV, PAUSE_ENV = "KIT_V61_FAIL_AFTER", "KIT_V61_CRASH_AFTER", "KIT_V61_PAUSE_SECONDS"      # rehearsal hooks only
MARK = "kit_v6_1_installer"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# ------------------------------------------------------------------ OS-level exclusive operation lock (released by the OS if the process dies)
class OpLock:
    def __init__(self, path: Path):
        self.path, self.fh = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            raise
        return self

    def __exit__(self, *a):
        if self.fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()


# ------------------------------------------------------------------ files
def _atomic_write(dst: Path, data: bytes, want_sha: str) -> None:
    tmp = dst.with_name(dst.name + ".kitv61tmp")
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


def _read_journal(bdir: Path) -> dict:
    return json.loads(_journal(bdir).read_text(encoding="utf-8")) if _journal(bdir).exists() else {}


def _write_journal(bdir: Path, **kw) -> None:
    j = _read_journal(bdir)
    j.update(kw, updated=datetime.now(timezone.utc).isoformat())
    tmp = bdir / "journal.json.tmp"
    tmp.write_text(json.dumps(j, indent=1), encoding="utf-8")
    os.replace(tmp, _journal(bdir))


def _latest_backup(root: Path):
    d = sorted(p for p in root.glob("kit_v6_1_backup_*") if p.is_dir()) if root.exists() else []
    return d[-1] if d else None


# ------------------------------------------------------------------ collector protection
def collector_problems(state_root: Path, store_dir: Path, allow_own_hold: bool = False) -> list:
    out = []
    lock = state_root / "collection.lock"
    if lock.exists():
        mine = False
        try:
            mine = json.loads(lock.read_text(encoding="utf-8")).get("by") == MARK
        except (OSError, ValueError):
            pass
        if not (allow_own_hold and mine):
            out.append("collector lock present (%s): a run is in progress or was interrupted; the installer never removes a lock it did not create" % lock)
    if not (store_dir / "KILL").exists():
        out.append("runner kill switch missing (%s): a live run may be in progress, or the store is not the expected one" % (store_dir / "KILL"))
    return out


def hold(state_root: Path, op: str) -> dict:
    """Keep the collector off: take its lock and engage the wrapper kill switch if it is not already engaged. Returns what this call created (for the journal)."""
    state_root.mkdir(parents=True, exist_ok=True)
    made = {"lock": False, "kill": False}
    lock, kill = state_root / "collection.lock", state_root / "KILL"
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)                         # never replaces someone else's lock
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps({"by": MARK, "op": op, "pid": os.getpid(), "at": datetime.now(timezone.utc).isoformat()}))
    made["lock"] = True
    if not kill.exists():
        kill.write_text(MARK + ": %s in progress; removed by the installer when it finishes or recovers" % op, encoding="utf-8")
        made["kill"] = True
    return made


def release(state_root: Path, made: dict) -> None:
    lock, kill = state_root / "collection.lock", state_root / "KILL"
    try:
        if made.get("lock") and lock.exists() and json.loads(lock.read_text(encoding="utf-8")).get("by") == MARK:
            lock.unlink()
    except (OSError, ValueError):
        pass
    try:
        if made.get("kill") and kill.exists() and kill.read_text(encoding="utf-8").startswith(MARK):
            kill.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ operations
def restore(manifest: dict, app: Path, bdir: Path) -> tuple:
    """Put the runner back to the v6 state recorded in the backup and verify it. -> (ok, [messages])"""
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
    for tmp in app.glob("*.kitv61tmp"):
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


def _gate(manifest, app, root, state_root, store_dir, op, need_state) -> list:
    problems = collector_problems(state_root, store_dir)
    b = _latest_backup(root)
    if b is not None:
        st = _read_journal(b).get("state")
        if st in ("INSTALLING", "RESTORE_FAILED", "ROLLING_BACK"):
            problems.append("an earlier operation was interrupted (journal state %s): run `recover` first" % st)
        if need_state and st != need_state:
            problems.append("rollback refused: no completed kit-v6.1 install is recorded (journal state %s)" % st)
    elif need_state:
        problems.append("rollback refused: no completed kit-v6.1 install is recorded")
    return problems


def install(manifest: dict, payload: Path, app: Path, root: Path, state_root: Path, store_dir: Path) -> tuple:
    try:
        lock = OpLock(root / "kit_v6_1.op.lock").__enter__()
    except OSError:
        return 2, ["install refused: another kit-v6.1 operation (install / recover / rollback) is running; nothing was changed"]
    try:
        problems = _gate(manifest, app, root, state_root, store_dir, "install", None)
        code, msgs = K.verify(manifest, payload, app, "pre")
        if problems or code != 0:
            return 2, ["install refused, nothing was changed:"] + problems + msgs
        bdir = root / ("kit_v6_1_backup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
        files = {Path(f["path"]).name: f for f in manifest["files"]}
        try:
            made = hold(state_root, "install")
        except FileExistsError:
            return 2, ["install refused, nothing was changed: the collector lock appeared while the installer was starting"]
        (bdir / "files").mkdir(parents=True)
        _write_journal(bdir, state="INSTALLING", app_backend=str(app), state_root=str(state_root), holds=made, files=ORDER, started=datetime.now(timezone.utc).isoformat())
        try:
            for n in K.CHANGED:
                shutil.copyfile(app / n, bdir / "files" / n)
                if sha(bdir / "files" / n) != files[n]["before_sha256"]:
                    raise RuntimeError("backup of %s is not byte-identical to the installed file" % n)
            done = 0
            for n in ORDER:
                _atomic_write(app / n, (payload / n).read_bytes(), files[n]["after_sha256"])
                done += 1
                if os.environ.get(PAUSE_ENV) and done == 2:
                    time.sleep(float(os.environ[PAUSE_ENV]))
                if os.environ.get(CRASH_AFTER_ENV) == str(done):
                    os._exit(9)                                                          # power cut: journal stays INSTALLING, holds stay in place, the OS frees the operation lock
                if os.environ.get(FAIL_AFTER_ENV) == str(done):
                    raise RuntimeError("injected failure after %d file(s)" % done)
            vcode, vmsgs = K.verify(manifest, None, app, "post")
            if vcode != 0:
                raise RuntimeError("post-install verification failed: " + " | ".join(vmsgs))
            _write_journal(bdir, state="INSTALLED")
            release(state_root, made)
            return 0, ["INSTALLED: kit v6.1 (runner hotfix) verified; every other file unchanged; collector holds released; backup and journal in %s" % bdir]
        except Exception as e:  # noqa: BLE001
            ok, rmsgs = restore(manifest, app, bdir)
            _write_journal(bdir, state="RESTORED" if ok else "RESTORE_FAILED", error=str(e))
            if ok:
                release(state_root, made)                                                # only on a verified restore; otherwise the collector stays blocked
            return (4 if ok else 5), ["INSTALL FAILED (%s); %s" % (e, "every file was restored and verified, holds released" if ok else "RESTORE DID NOT VERIFY, the collector stays blocked: " + "; ".join(rmsgs))]
    finally:
        lock.__exit__()


def recover(manifest: dict, app: Path, root: Path, state_root: Path, store_dir: Path) -> tuple:
    try:
        lock = OpLock(root / "kit_v6_1.op.lock").__enter__()
    except OSError:
        return 2, ["recover refused: another kit-v6.1 operation is running"]
    try:
        bdir = _latest_backup(root)
        if bdir is None:
            return 2, ["nothing to recover: no kit-v6.1 backup/journal found"]
        j = _read_journal(bdir)
        if j.get("state") not in ("INSTALLING", "RESTORE_FAILED", "ROLLING_BACK"):
            return 2, ["nothing to recover: the last journal state is %s" % j.get("state")]
        problems = collector_problems(state_root, store_dir, allow_own_hold=True)
        if problems:
            return 2, ["recover refused, nothing was changed:"] + problems
        ok, msgs = restore(manifest, app, bdir)
        _write_journal(bdir, state="RESTORED" if ok else "RESTORE_FAILED")
        if ok:
            release(state_root, j.get("holds") or {})
        return (0 if ok else 5), ["RECOVERED: the runner restored to v6 and verified; holds released" if ok else "RECOVERY DID NOT VERIFY, the collector stays blocked: " + "; ".join(msgs)]
    finally:
        lock.__exit__()


def rollback(manifest: dict, app: Path, root: Path, state_root: Path, store_dir: Path) -> tuple:
    try:
        lock = OpLock(root / "kit_v6_1.op.lock").__enter__()
    except OSError:
        return 2, ["rollback refused: another kit-v6.1 operation is running"]
    try:
        problems = _gate(manifest, app, root, state_root, store_dir, "rollback", "INSTALLED")
        code, msgs = K.verify(manifest, None, app, "post")
        if problems or code != 0:
            return 2, ["rollback refused, nothing was changed:"] + problems + (["the installed files are not exactly kit v6.1 any more (changed after the install?):"] + msgs if code != 0 else [])
        bdir = _latest_backup(root)
        made = hold(state_root, "rollback")
        _write_journal(bdir, state="ROLLING_BACK", holds=made)
        ok, rmsgs = restore(manifest, app, bdir)
        _write_journal(bdir, state="ROLLED_BACK" if ok else "RESTORE_FAILED")
        if ok:
            release(state_root, made)
        return (0 if ok else 5), ["ROLLED BACK to v6: the runner restored, hashes verified, holds released" if ok else "ROLLBACK DID NOT VERIFY, the collector stays blocked: " + "; ".join(rmsgs)]
    finally:
        lock.__exit__()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("install", "recover", "rollback"))
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--payload")
    ap.add_argument("--app-backend", required=True)
    ap.add_argument("--backup-root", required=True)
    ap.add_argument("--state-root", required=True, help="collector state folder (collection.lock, wrapper KILL)")
    ap.add_argument("--store-dir", required=True, help="evidence store folder (runner KILL)")
    a = ap.parse_args(argv)
    try:
        manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("REFUSED: manifest unreadable:", e)
        return 2
    app, root, st, store = Path(a.app_backend), Path(a.backup_root), Path(a.state_root), Path(a.store_dir)
    if a.cmd == "install":
        code, msgs = install(manifest, Path(a.payload) if a.payload else Path("."), app, root, st, store)
    elif a.cmd == "recover":
        code, msgs = recover(manifest, app, root, st, store)
    else:
        code, msgs = rollback(manifest, app, root, st, store)
    print("\n".join(msgs))
    return code


if __name__ == "__main__":
    sys.exit(main())
