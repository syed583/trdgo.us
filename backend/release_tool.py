"""Release workflow: the steps around a deploy that make "no unconfirmed entry, ever" provable.

    python release_tool.py window                      are new entries impossible by TIME right now?
    python release_tool.py preflight --repo R --target REV
    python release_tool.py cutover   --repo R --target REV [--service USStockReader] [--url http://127.0.0.1:8000]
    python release_tool.py unlock    [--url URL] [--expect-commit SHA] [--reason TEXT]
    python release_tool.py rollback-prepare [--reason TEXT]

Design (docs/release_and_rollback.md):
  * A swap happens only while entries are impossible by time (weekend, or outside 04:00-20:00 ET) AND no position
    is open. Both are checked here; there is no flag that skips them.
  * The target is judged by release_check.py. A target that fails is refused.
  * The service is stopped, the entry lock installed (a first install starts LOCKED), the service started, and it
    must answer on /safety before anything else happens. A failure at any step leaves entries locked / the service
    stopped, never open.
  * `unlock` refuses unless the RUNNING service (not the files on disk) reports, over HTTP, that it keeps the
    mandatory 15-minute confirmation, that it is the commit on disk, committed and not flagged unsafe.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ENTRY_OPEN, ENTRY_CLOSE = dtime(4, 0), dtime(20, 0)           # the paper-trade tracker may open 04:00-20:00 ET weekdays


# ------------------------------------------------------------------------ time ---
def entries_impossible(now: Optional[datetime] = None) -> tuple[bool, str]:
    """True when no build can open a new entry because of the TIME: a weekend, or outside 04:00-20:00 ET (the live
    engine's own window, 10:00-15:30 ET, lies inside it). Holidays are NOT assumed closed."""
    n = (now or datetime.now(ET)).astimezone(ET)
    if n.weekday() >= 5:
        return True, f"weekend ({n:%a %H:%M} ET)"
    if n.time() < ENTRY_OPEN or n.time() >= ENTRY_CLOSE:
        return True, f"outside 04:00-20:00 ET ({n:%a %H:%M} ET)"
    return False, f"entries can open now ({n:%a %H:%M} ET; the tracker runs 04:00-20:00 ET on weekdays)"


# ------------------------------------------------------------------- preflight ---
def preflight(repo: str, target: str, *, now: Optional[datetime] = None, engine=None,
              reader: Optional[Callable[[str], Optional[str]]] = None) -> dict:
    """Everything that must be true BEFORE the service is stopped. Never changes anything."""
    import entry_lock
    import release_check
    problems: list[str] = []
    quiet, why = entries_impossible(now)
    if not quiet:
        problems.append(f"not in a no-entry window: {why}")
    chk = release_check.check(reader or release_check.read_rev(target, Path(repo)))
    if not chk["ok"]:
        problems.append("the target fails the release check: " + "; ".join(c["check"] for c in chk["checks"] if not c["ok"]))
    ident = release_check.identify(Path(repo))
    if ident.get("uncommitted_changes"):
        problems.append("the production checkout has uncommitted changes: " +
                        "identify them before deploying (they would block a fast-forward pull)")
    try:
        open_pos = entry_lock.open_positions(engine)
        if any(open_pos.values()):
            problems.append(f"open positions exist: {open_pos}")
    except Exception as exc:  # noqa: BLE001
        open_pos = None
        problems.append(f"could not read open positions ({type(exc).__name__}): refusing to proceed blind")
    return {"ok": not problems, "problems": problems, "window": why, "target": target,
            "running_commit_rollback_reference": ident.get("head"), "open_positions": open_pos,
            "release_check": chk["ok"]}


# ------------------------------------------------------------ runtime verification ---
def _get(url: str, opener: Callable = urllib.request.urlopen, timeout: float = 10.0) -> dict:
    with opener(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def verify_runtime(url: str, *, expect_commit: Optional[str] = None, expect_environment: Optional[str] = None,
                   opener: Callable = urllib.request.urlopen) -> tuple[bool, list[str], dict]:
    """Ask the RUNNING service (``{url}/safety``) whether it keeps the mandatory rules. Any doubt is a failure."""
    problems: list[str] = []
    try:
        st = _get(url.rstrip("/") + "/safety", opener)
    except urllib.error.HTTPError as exc:
        return False, [f"/safety answered HTTP {exc.code}: the running build predates the safety work"], {}
    except Exception as exc:  # noqa: BLE001
        return False, [f"the service did not answer /safety ({type(exc).__name__}): is it running?"], {}
    if st.get("confirm15_required") is not True:
        problems.append("the running build does not require the 15-minute confirmation")
    if st.get("can_be_disabled") is not False:
        problems.append("the running build says the confirmation can be disabled")
    if st.get("static_checks") is not True:
        problems.append("the running build's files fail the release check")
    if st.get("unsafe_build"):
        problems.append(f"the running build failed its boot self-check: {st['unsafe_build']}")
    if st.get("running_matches_tree") is not True:
        problems.append("the running process is not the commit on disk (files changed under it, or git unavailable): restart it")
    if st.get("uncommitted_changes") is not False:
        problems.append("the running build has uncommitted changes (or the state is unknown)")
    if expect_commit and not (st.get("commit") and expect_commit.startswith(st["commit"][:12])):
        problems.append(f"the running commit {st.get('commit')} is not the expected {expect_commit[:12]}")
    if expect_environment and st.get("environment") != expect_environment:
        problems.append(f"the running environment is {st.get('environment')!r}, expected {expect_environment!r}")
    if st.get("entry_locked") is None:
        problems.append("the running build cannot read the entry lock")
    return (not problems), problems, st


def unlock(url: str, *, expect_commit: Optional[str] = None, expect_environment: Optional[str] = None,
           reason: str = "", engine=None, opener: Callable = urllib.request.urlopen) -> dict:
    """Allow new entries again -- only if the running service passes ``verify_runtime``. No override."""
    import entry_lock
    ok, problems, st = verify_runtime(url, expect_commit=expect_commit, expect_environment=expect_environment,
                                      opener=opener)
    if not ok:
        return {"ok": False, "unlocked": False, "problems": problems, "runtime": st}
    r = entry_lock.set_locked(False, reason or "unlocked after runtime verification", engine)
    return {"ok": bool(r.get("ok")), "unlocked": bool(r.get("ok")), "problems": [], "runtime": st}


# ---------------------------------------------------------------------- cutover ---
def _powershell(cmd: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=True)


def cutover(repo: str, target: str, *, service: str = "USStockReader", url: str = "http://127.0.0.1:8000",
            now: Optional[datetime] = None, engine=None,
            stop: Optional[Callable[[str], None]] = None, start: Optional[Callable[[str], None]] = None,
            healthy_after_s: float = 60.0, sleep: Callable[[float], None] = time.sleep,
            opener: Callable = urllib.request.urlopen, reader=None) -> dict:
    """Stop -> install the lock (locked) -> start -> wait for /safety. Code and build are already on disk.

    Returns {"ok", "stage", ...}. On any failure entries stay locked and the service is NOT silently restarted into an
    unknown state; the message says what to do."""
    import entry_lock
    stop = stop or (lambda s: _powershell(f"Stop-Service {s}"))
    start = start or (lambda s: _powershell(f"Start-Service {s}"))
    pre = preflight(repo, target, now=now, engine=engine, reader=reader)
    if not pre["ok"]:
        return {"ok": False, "stage": "preflight", "problems": pre["problems"], "service": "untouched"}
    try:
        stop(service)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "stop", "problems": [f"could not stop the service: {exc}"], "service": "unknown"}
    try:
        installed = entry_lock.install(engine)
        if not installed["installed_on"] or installed["skipped_missing_tables"]:
            raise RuntimeError(f"triggers missing on {installed['skipped_missing_tables'] or 'all tables'}")
        entry_lock.set_locked(True, f"cutover to {target[:12]}: locked until the running build is verified", engine)
        if entry_lock.is_locked(engine, fresh=True) is not True:
            raise RuntimeError("the lock is not on after install")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "lock-install", "service": "STOPPED (safe)",
                "problems": [f"the entry lock could not be installed ({type(exc).__name__}: {exc}); "
                             "the service was left stopped: fix the database, then start it"]}
    try:
        start(service)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "start", "service": "NOT RUNNING", "entries": "locked",
                "problems": [f"the service did not start: {exc}. Entries are locked. Follow the rollback procedure "
                             "(docs/release_and_rollback.md); do not unlock."]}
    waited = 0.0
    last: list[str] = []
    while waited <= healthy_after_s:
        ok, last, st = verify_runtime(url, opener=opener)
        if ok:
            return {"ok": True, "stage": "running-locked", "runtime": st, "entries": "locked",
                    "next": "verify the runtime, then: python release_tool.py unlock --url " + url}
        # a build that answered but is unsafe is final; "no answer yet" is retried
        if st:
            break
        sleep(2.0)
        waited += 2.0
    return {"ok": False, "stage": "verify", "service": "RUNNING", "entries": "locked", "problems": last,
            "next": "entries stay locked; fix or roll back (docs/release_and_rollback.md)"}


# --------------------------------------------------------------------- rollback ---
def rollback_prepare(*, reason: str = "rollback", engine=None) -> dict:
    """Lock entries for a rollback to an older build. Refuses while any position is open (an older build cannot
    manage exits through the lock trigger)."""
    import entry_lock
    return entry_lock.set_locked(True, reason, engine, for_rollback=True)


def main(argv: list[str]) -> int:
    def arg(name: str, default: Optional[str] = None) -> Optional[str]:
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default
    cmd = argv[0] if argv else ""
    if cmd == "window":
        ok, why = entries_impossible()
        print(json.dumps({"entries_impossible_by_time": ok, "why": why}))
        return 0 if ok else 1
    if cmd == "preflight":
        r = preflight(arg("--repo", "."), arg("--target", "HEAD"))
        print(json.dumps(r, indent=1, default=str))
        return 0 if r["ok"] else 2
    if cmd == "cutover":
        r = cutover(arg("--repo", "."), arg("--target", "HEAD"), service=arg("--service", "USStockReader"),
                    url=arg("--url", "http://127.0.0.1:8000"))
        print(json.dumps(r, indent=1, default=str))
        return 0 if r["ok"] else 2
    if cmd == "unlock":
        r = unlock(arg("--url", "http://127.0.0.1:8000"), expect_commit=arg("--expect-commit"),
                   expect_environment=arg("--expect-environment", "production"), reason=arg("--reason", ""))
        print(json.dumps(r, indent=1, default=str))
        return 0 if r["ok"] else 2
    if cmd == "rollback-prepare":
        r = rollback_prepare(reason=arg("--reason", "rollback"))
        print(json.dumps(r, indent=1, default=str))
        return 0 if r.get("ok") else 2
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
