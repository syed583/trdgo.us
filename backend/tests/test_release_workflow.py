"""The complete release and rollback workflow in an ISOLATED environment: a temporary git repository (an old build
and a new build), a throw-away database with the real lock triggers, fake service control and a real local HTTP
server standing in for the running service. Nothing here touches production, the provider or the network."""
import http.server
import json
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text

import entry_lock
import intraday_15m as c15
import release_check
import release_tool as rt
import swr

ET = ZoneInfo("America/New_York")
BACKEND = Path(__file__).resolve().parent.parent
NIGHT = datetime(2026, 10, 24, 21, 30, tzinfo=ET)          # Friday 21:30 ET: entries are impossible by time
SATURDAY = datetime(2026, 10, 24, 11, 0, tzinfo=ET)
SESSION = datetime(2026, 10, 23, 11, 0, tzinfo=ET)         # Thursday 11:00 ET: entries can open

GOOD = {"confirm15_required": True, "can_be_disabled": False, "static_checks": True, "unsafe_build": None,
        "entry_locked": True, "commit": "abc123def456", "uncommitted_changes": False, "running_matches_tree": True,
        "environment": "production", "database_dialect": "postgresql"}

SAFETY_FILES = ["intraday_15m.py", "entry_lock.py", "release_tool.py", "release_check.py", "uw_slots.py"]


# ----------------------------------------------------------------- environment ---
def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


@pytest.fixture(scope="module")
def server_repo(tmp_path_factory):
    """A 'production checkout': commit 1 = an OLD build (no 15-minute confirmation), commit 2 = the NEW build."""
    root = tmp_path_factory.mktemp("server")
    (root / "backend").mkdir()
    _git(root, "init", "-q", "-b", "main")
    files = [p for p in BACKEND.glob("*.py")]
    for p in files:
        if p.name not in SAFETY_FILES:
            shutil.copy(p, root / "backend" / p.name)
    # the old build also lacks the safety wiring inside shared files
    for name, marker in (("intraday_safety.py", "entry_check("), ("demo_trade_service.py", "entry_check("),
                         ("intraday_signal_v2.py", "entry_check(")):
        f = root / "backend" / name
        f.write_text(f.read_text(encoding="utf-8").replace(marker, "old_check("), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "old build")
    for p in files:
        shutil.copy(p, root / "backend" / p.name)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "new build")
    return root


@pytest.fixture
def book():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, status TEXT)"))
        c.execute(text("CREATE TABLE intraday_sig_v2 (id INTEGER PRIMARY KEY, is_open BOOLEAN)"))
    return eng


class Service:
    """Fake service control that records what happened and can be told to fail."""

    def __init__(self, fail_stop=False, fail_start=False):
        self.calls, self.fail_stop, self.fail_start = [], fail_stop, fail_start

    def stop(self, name):
        self.calls.append("stop")
        if self.fail_stop:
            raise RuntimeError("cannot stop")

    def start(self, name):
        self.calls.append("start")
        if self.fail_start:
            raise RuntimeError("did not start")


class Running:
    """A real HTTP server answering /safety like the running service would."""

    def __init__(self, status=None, code=200):
        self.status, self.code = dict(GOOD if status is None else status), code
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(outer.status).encode()
                self.send_response(outer.code if self.path == "/safety" else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def running():
    r = Running()
    yield r
    r.close()


def cut(server_repo, book, svc, running, *, now=NIGHT, target="HEAD", **kw):
    return rt.cutover(str(server_repo), target, now=now, engine=book, stop=svc.stop, start=svc.start,
                      url=running.url, healthy_after_s=0.0, sleep=lambda s: None, **kw)


# ------------------------------------------------------------------- preflight ---
def test_preflight_passes_in_the_window_with_nothing_open(server_repo, book):
    r = rt.preflight(str(server_repo), "HEAD", now=NIGHT, engine=book)
    assert r["ok"], r["problems"]
    assert r["running_commit_rollback_reference"] and r["open_positions"] == {"demo_trades": 0, "intraday_sig_v2": 0}
    assert rt.preflight(str(server_repo), "HEAD", now=SATURDAY, engine=book)["ok"]


def test_preflight_refuses_while_entries_can_open(server_repo, book):
    r = rt.preflight(str(server_repo), "HEAD", now=SESSION, engine=book)
    assert not r["ok"] and any("no-entry window" in p for p in r["problems"])


def test_preflight_refuses_an_open_position_and_an_unreadable_database(server_repo, book):
    with book.begin() as c:
        c.execute(text("INSERT INTO demo_trades (status) VALUES ('OPEN')"))
    r = rt.preflight(str(server_repo), "HEAD", now=NIGHT, engine=book)
    assert not r["ok"] and any("open positions" in p for p in r["problems"])

    class Broken:
        def connect(self):
            raise RuntimeError("db down")
    r = rt.preflight(str(server_repo), "HEAD", now=NIGHT, engine=Broken())
    assert not r["ok"] and any("could not read open positions" in p for p in r["problems"])


def test_preflight_refuses_a_target_without_the_confirmation(server_repo, book):
    old = rt.preflight(str(server_repo), "HEAD~1", now=NIGHT, engine=book)
    assert not old["ok"] and any("fails the release check" in p for p in old["problems"])
    assert release_check.check(release_check.read_rev("HEAD~1", server_repo))["ok"] is False
    assert release_check.check(release_check.read_rev("HEAD", server_repo))["ok"] is True


def test_preflight_notices_uncommitted_changes_in_the_production_checkout(server_repo, book):
    marker = server_repo / "backend" / "dirty.txt"
    marker.write_text("x")
    try:
        r = rt.preflight(str(server_repo), "HEAD", now=NIGHT, engine=book)
        assert not r["ok"] and any("uncommitted" in p for p in r["problems"])
    finally:
        marker.unlink()


# --------------------------------------------------------------------- cut-over ---
def test_a_clean_cutover_stops_installs_the_lock_starts_and_leaves_entries_locked(server_repo, book, running):
    svc = Service()
    r = cut(server_repo, book, svc, running)
    assert r["ok"] and r["stage"] == "running-locked" and svc.calls == ["stop", "start"]
    assert entry_lock.is_locked(book, fresh=True) is True
    assert set(entry_lock.status(book)["triggers"]) == {"demo_trades", "intraday_sig_v2"}


def test_a_cutover_inside_the_session_never_touches_the_service(server_repo, book, running):
    svc = Service()
    r = cut(server_repo, book, svc, running, now=SESSION)
    assert not r["ok"] and r["stage"] == "preflight" and svc.calls == []


def test_a_lock_install_failure_leaves_the_service_stopped_not_running_unlocked(server_repo, running):
    class BadDb:
        dialect = create_engine("sqlite://").dialect

        def begin(self):
            raise RuntimeError("permission denied for trigger")

        def connect(self):
            raise RuntimeError("permission denied")
    svc = Service()
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, status TEXT)"))
        c.execute(text("CREATE TABLE intraday_sig_v2 (id INTEGER PRIMARY KEY, is_open BOOLEAN)"))
    calls = {"n": 0}
    real_install = entry_lock.install

    def failing_install(engine=None, **kw):
        calls["n"] += 1
        raise RuntimeError("trigger creation failed")
    entry_lock.install = failing_install
    try:
        r = rt.cutover(str(server_repo), "HEAD", now=NIGHT, engine=eng, stop=svc.stop, start=svc.start,
                       url=running.url, healthy_after_s=0.0, sleep=lambda s: None)
    finally:
        entry_lock.install = real_install
    assert not r["ok"] and r["stage"] == "lock-install" and "STOPPED" in r["service"]
    assert svc.calls == ["stop"]                                       # it was NEVER started


def test_triggers_that_cannot_be_installed_on_every_table_fail_the_cutover(server_repo, running):
    eng = create_engine("sqlite://")
    with eng.begin() as c:                                             # only ONE guarded table exists
        c.execute(text("CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, status TEXT)"))
    svc = Service()
    r = rt.cutover(str(server_repo), "HEAD", now=NIGHT, engine=eng, stop=svc.stop, start=svc.start, url=running.url,
                   healthy_after_s=0.0, sleep=lambda s: None)
    assert not r["ok"] and r["stage"] == "lock-install" and svc.calls == ["stop"]


def test_a_service_that_fails_to_start_keeps_entries_locked(server_repo, book, running):
    svc = Service(fail_start=True)
    r = cut(server_repo, book, svc, running)
    assert not r["ok"] and r["stage"] == "start" and r["entries"] == "locked"
    assert entry_lock.is_locked(book, fresh=True) is True


def test_a_service_that_never_answers_keeps_entries_locked(server_repo, book):
    svc = Service()
    r = rt.cutover(str(server_repo), "HEAD", now=NIGHT, engine=book, stop=svc.stop, start=svc.start,
                   url="http://127.0.0.1:9", healthy_after_s=0.0, sleep=lambda s: None)
    assert not r["ok"] and r["stage"] == "verify" and r["entries"] == "locked"
    assert entry_lock.is_locked(book, fresh=True) is True


def test_a_started_but_unsafe_build_is_not_accepted(server_repo, book):
    bad = Running(dict(GOOD, unsafe_build="the 15-minute module is missing", static_checks=False))
    try:
        r = cut(server_repo, book, Service(), bad)
    finally:
        bad.close()
    assert not r["ok"] and r["stage"] == "verify" and entry_lock.is_locked(book, fresh=True) is True
    assert any("boot self-check" in p or "release check" in p for p in r["problems"])


def test_a_failed_stop_stops_the_release(server_repo, book, running):
    r = cut(server_repo, book, Service(fail_stop=True), running)
    assert not r["ok"] and r["stage"] == "stop"
    assert entry_lock.status(book)["installed"] is False                # nothing was changed in the database


# ------------------------------------------------------------ unlock verification ---
@pytest.mark.parametrize("change, expect", [
    (dict(confirm15_required=False), "does not require"),
    (dict(can_be_disabled=True), "can be disabled"),
    (dict(static_checks=False), "release check"),
    (dict(unsafe_build="x"), "boot self-check"),
    (dict(running_matches_tree=False), "not the commit on disk"),
    (dict(uncommitted_changes=True), "uncommitted"),
    (dict(uncommitted_changes=None), "uncommitted"),
    (dict(environment="development"), "environment"),
    (dict(commit="000000000000"), "expected"),
    (dict(entry_locked=None), "cannot read the entry lock"),
])
def test_unlock_refuses_every_unsafe_runtime_and_stays_locked(book, change, expect):
    entry_lock.set_locked(True, "cutover", book)
    srv = Running(dict(GOOD, **change))
    try:
        r = rt.unlock(srv.url, expect_commit="abc123def456aaaa", expect_environment="production", engine=book)
    finally:
        srv.close()
    assert r["ok"] is False and r["unlocked"] is False and any(expect in p for p in r["problems"]), r
    assert entry_lock.is_locked(book, fresh=True) is True


def test_unlock_refuses_a_build_that_predates_the_safety_route_and_a_dead_service(book):
    entry_lock.set_locked(True, "cutover", book)
    srv = Running(code=404)
    try:
        r = rt.unlock(srv.url, engine=book)
    finally:
        srv.close()
    assert not r["ok"] and any("predates" in p for p in r["problems"])
    assert not rt.unlock("http://127.0.0.1:9", engine=book)["ok"]
    assert entry_lock.is_locked(book, fresh=True) is True


def test_unlock_succeeds_only_for_a_verified_running_build(book, running):
    entry_lock.set_locked(True, "cutover", book)
    r = rt.unlock(running.url, expect_commit="abc123def456aaaa", expect_environment="production", engine=book)
    assert r["ok"] and r["unlocked"] and entry_lock.is_locked(book, fresh=True) is False


def test_there_is_no_flag_that_skips_the_checks():
    for name in ("entry_lock.py", "release_tool.py", "release_check.py"):
        src = (BACKEND / name).read_text(encoding="utf-8").lower()
        for flag in ("--force", "--skip", "--no-check", "--yes-i-know", "--ignore"):
            assert flag not in src, (name, flag)


def test_the_cli_unlock_goes_through_the_runtime_check(monkeypatch, capsys):
    seen = {}

    def fake_unlock(url, **kw):
        seen["url"] = url
        return {"ok": False, "unlocked": False, "problems": ["nope"], "runtime": {}}
    monkeypatch.setattr(rt, "unlock", fake_unlock)
    assert entry_lock.main(["unlock", "--url=http://x:1"]) == 2 and seen["url"] == "http://x:1"
    monkeypatch.setattr(rt, "unlock", lambda url, **kw: {"ok": True, "unlocked": True, "problems": [], "runtime": {}})
    assert entry_lock.main(["unlock"]) == 0


# ---------------------------------------------------------- the deployment script ---
def test_the_update_script_cannot_skip_preflight_or_unlock_by_itself():
    ps = (BACKEND.parent / "deploy" / "windows" / "update.ps1").read_text(encoding="utf-8")
    code = [ln.strip() for ln in ps[ps.index("#>") + 2:].splitlines() if ln.strip() and not ln.strip().startswith("#")]
    runs = [ln for ln in code if ln.startswith("&") or ln.startswith("git ") or ln.startswith("npm ")]
    pos = {name: next(i for i, ln in enumerate(runs) if name in ln)
           for name in ("preflight", "git pull", "-m pytest", "cutover")}
    assert pos["preflight"] < pos["git pull"] < pos["-m pytest"] < pos["cutover"]    # judged first, cut over last
    assert not any("unlock" in ln for ln in runs)                                    # the script NEVER unlocks
    assert not any("Restart-Service" in ln or "Start-Service" in ln for ln in code)  # only release_tool starts the service
    assert any("release_check.py" in ln for ln in runs)                              # the installed checker is a second opinion
    start = ps.index("param(")
    param = ps[start:ps.index(")", ps.index("Root", start))]
    assert "Skip" not in param and "Force" not in param and "Check" not in param
    assert "exit 1" in ps and ps.count("exit 1") >= 5                                # every failure stops the script


# ----------------------------------------------------------------------- rollback ---
def test_rollback_to_an_older_build_requires_a_locked_book_with_nothing_open(server_repo, book):
    entry_lock.set_locked(False, "live", book)
    with book.begin() as c:
        c.execute(text("INSERT INTO demo_trades (status) VALUES ('OPEN')"))
    refused = rt.rollback_prepare(engine=book)
    assert refused["ok"] is False and refused["open_positions"]["demo_trades"] == 1
    assert entry_lock.is_locked(book, fresh=True) is False               # nothing changed on refusal
    with book.begin() as c:
        c.execute(text("UPDATE demo_trades SET status = 'TARGET'"))      # the new build's exit closes it
    assert rt.rollback_prepare(engine=book)["ok"] is True
    # the OLD build (which knows nothing about the lock) now cannot open an entry, even though it is "running"
    with pytest.raises(Exception) as e:
        with book.begin() as c:
            c.execute(text("INSERT INTO demo_trades (status) VALUES ('OPEN')"))
    assert "locked" in str(e.value).lower()
    with book.begin() as c:                                              # and position management keeps working
        c.execute(text("UPDATE demo_trades SET status = 'STOP' WHERE status = 'TARGET'"))
    # the old target is refused by the normal release path, and says why
    assert not rt.preflight(str(server_repo), "HEAD~1", now=NIGHT, engine=book)["ok"]


# --------------------------------------------------- missing data, gate and boot ---
def test_missing_or_unverified_confirmation_data_blocks_entries_after_a_clean_unlock(book, running, monkeypatch):
    real = entry_lock.is_locked
    monkeypatch.setattr(entry_lock, "is_locked", lambda engine=None, **k: real(engine or book, **k))
    entry_lock.set_locked(True, "cutover", book)
    assert rt.unlock(running.url, expect_commit="abc123def456aaaa", expect_environment="production", engine=book)["ok"]
    swr._values.pop(c15.KEY.format("AAA"), None)                          # no 15-minute reading exists yet
    out = c15.entry_check("AAA", "BUY", "rth", execution=True)
    assert out["allowed"] is False and out["status"] == "MISSING"
    entry_lock.set_locked(True, "locked", book)                           # and a perfect reading is still not enough while locked
    import time
    t = time.time()
    swr._values[c15.KEY.format("AAA")] = (t, {"symbol": "AAA", "status": "OK", "bias": 1, "votes": {}, "bars": 60,
                                              "rejected": {}, "detail": "", "ts": t - 30, "last_bar_end_epoch": t - 200})
    assert c15.entry_check("AAA", "BUY", "rth", execution=True)["status"] == "ENTRIES_LOCKED"
    assert c15.entry_check("AAA", "BUY", "rth")["allowed"] is True       # display / not-execution is unaffected by the lock


def test_an_incomplete_build_refuses_every_entry_from_its_own_boot_check(monkeypatch):
    monkeypatch.setattr(c15, "UNSAFE_BUILD", None)
    real = release_check.read_tree()
    monkeypatch.setattr(release_check, "read_tree", lambda root=release_check.ROOT: (
        lambda path: None if path.endswith("entry_lock.py") else real(path)))
    assert "entry lock" in (c15.boot_self_check() or "")
    t = __import__("time").time()
    swr._values[c15.KEY.format("AAA")] = (t, {"symbol": "AAA", "status": "OK", "bias": 1, "votes": {}, "bars": 60,
                                              "rejected": {}, "detail": "", "ts": t - 30, "last_bar_end_epoch": t - 200})
    out = c15.entry_check("AAA", "BUY", "rth")
    assert out["allowed"] is False and out["status"] == "BUILD_FAILED_SAFETY_CHECK"
    monkeypatch.setattr(c15, "UNSAFE_BUILD", None)


def test_a_failure_inside_the_gate_refuses_the_entry_and_never_raises(monkeypatch):
    def boom(*a, **k):
        raise ImportError("module went missing")
    monkeypatch.setattr(c15, "_entry_check", boom)
    out = c15.entry_check("AAA", "BUY", "rth", execution=True)
    assert out["allowed"] is False and out["status"] == "GATE_ERROR"
