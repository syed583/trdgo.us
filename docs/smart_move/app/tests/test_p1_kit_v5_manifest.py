"""Kit v5 draft manifest: build and fail-closed verification (audit finding F3). Temp folders only; nothing is installed. The 'installed base' is rebuilt from git blobs of 274fccac, whose bytes were verified equal to kit v4 / collector v3."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("kit_v5_manifest", ROOT / "docs" / "smart_move" / "p1" / "kit_v5" / "kit_v5_manifest.py")
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)
BASE_COMMIT = "274fccac"


def blob(name):
    return subprocess.run(["git", "show", "%s:backend/%s" % (BASE_COMMIT, name)], cwd=ROOT, capture_output=True, check=True).stdout


@pytest.fixture
def world(tmp_path):
    base, payload, app = tmp_path / "base", tmp_path / "payload", tmp_path / "app_backend"
    for d in (base, payload, app):
        d.mkdir()
    for n in K.CHANGED + K.PROTECTED:
        (base / n).write_bytes(blob(n))
        (app / n).write_bytes(blob(n))                                                              # the installed v4 state
    for n in K.CHANGED + K.NEW:
        shutil.copyfile(ROOT / "backend" / n, payload / n)
    manifest = K.build(ROOT, base)
    return manifest, payload, app, base


def codes(m, payload, app, phase):
    c, msgs = K.verify(m, payload, app, phase)
    return c, " | ".join(msgs)


def test_build_lists_exactly_the_six_files_with_before_and_after_hashes_and_pins_the_protected_ones(world):
    m, *_ = world
    assert [f["path"] for f in m["files"]] == ["backend/" + n for n in K.CHANGED + K.NEW] and m["all_or_nothing"] is True
    assert all("before_sha256" in f for f in m["files"] if f["role"] == "CHANGED") and all("before_sha256" not in f for f in m["files"] if f["role"] == "NEW")
    assert all(f["before_sha256"] != f["after_sha256"] for f in m["files"] if f["role"] == "CHANGED") and set(m["protected_unchanged"]) == {"backend/" + n for n in K.PROTECTED}
    assert "NOT claimed" in m["note"]


def test_preflight_passes_on_the_expected_v4_base_with_a_complete_payload(world):
    m, payload, app, _ = world
    assert codes(m, payload, app, "pre")[0] == 0


def test_preflight_fails_closed_on_a_missing_tampered_or_extra_payload_file(world):
    m, payload, app, _ = world
    (payload / "smart_move_universe.py").unlink()
    c, why = codes(m, payload, app, "pre")
    assert c == 2 and "payload file missing: smart_move_universe.py" in why
    shutil.copyfile(ROOT / "backend" / "smart_move_universe.py", payload / "smart_move_universe.py")
    (payload / "smart_move_engine20.py").write_bytes((payload / "smart_move_engine20.py").read_bytes() + b"\n# tampered\n")
    c, why = codes(m, payload, app, "pre")
    assert c == 2 and "does not match its manifest SHA-256: smart_move_engine20.py" in why
    shutil.copyfile(ROOT / "backend" / "smart_move_engine20.py", payload / "smart_move_engine20.py")
    (payload / "extra.py").write_text("x", encoding="utf-8")
    c, why = codes(m, payload, app, "pre")
    assert c == 2 and "unlisted file in the payload: extra.py" in why


def test_preflight_refuses_an_unknown_installed_base_or_a_missing_installed_file_or_a_conflicting_new_file(world):
    m, payload, app, _ = world
    (app / "smart_move_live.py").write_bytes(b"# not the v4 file\n")
    assert "not the expected v4 base: smart_move_live.py" in codes(m, payload, app, "pre")[1]
    (app / "smart_move_live.py").write_bytes(blob("smart_move_live.py"))
    (app / "smart_move_collection_runner.py").unlink()
    c, why = codes(m, payload, app, "pre")
    assert c == 2 and "installed file missing" in why and "smart_move_collection_runner.py" in why
    (app / "smart_move_collection_runner.py").write_bytes(blob("smart_move_collection_runner.py"))
    (app / "smart_move_calendar_tiers.py").write_text("# someone else's file\n", encoding="utf-8")
    assert "a different file with this name is already installed: smart_move_calendar_tiers.py" in codes(m, payload, app, "pre")[1]


def test_preflight_refuses_when_a_protected_file_is_not_the_pinned_one(world):
    m, payload, app, _ = world
    (app / "smart_move_live_publisher.py").write_bytes(blob("smart_move_live_publisher.py") + b"# edited\n")
    c, why = codes(m, payload, app, "pre")
    assert c == 2 and "protected file changed: backend/smart_move_live_publisher.py" in why


def test_postcheck_ok_after_a_full_copy_partial_after_a_half_copy_and_refused_when_nothing_is_installed(world):
    m, payload, app, _ = world
    assert codes(m, payload, app, "post")[0] == 2 and "no kit-v5 file is installed" in codes(m, payload, app, "post")[1]
    for n in K.NEW[:1] + K.CHANGED[:2]:                                                             # a half-finished install
        shutil.copyfile(payload / n, app / n)
    c, why = codes(m, payload, app, "post")
    assert c == 3 and why.startswith("PARTIAL: 3 of 6") and "smart_move_universe.py" in why
    for n in K.CHANGED + K.NEW:
        shutil.copyfile(payload / n, app / n)
    c, why = codes(m, payload, app, "post")
    assert c == 0 and why.startswith("OK (post)")
    (app / "smart_move_calendar_guard.py").write_bytes(b"# changed\n")
    assert codes(m, payload, app, "post")[0] == 2


def test_the_cli_returns_the_same_exit_codes_and_a_corrupt_manifest_is_refused(world, tmp_path):
    m, payload, app, base = world
    out = tmp_path / "m.json"
    assert K.main(["build", "--repo", str(ROOT), "--base-installed", str(base), "--out", str(out)]) == 0
    assert K.main(["verify", "--manifest", str(out), "--payload", str(payload), "--app-backend", str(app), "--phase", "pre"]) == 0
    out.write_text("{broken", encoding="utf-8")
    assert K.main(["verify", "--manifest", str(out), "--payload", str(payload), "--app-backend", str(app), "--phase", "pre"]) == 2
    (base / "smart_move_calendar_guard.py").write_bytes(b"# differs from the source tree\n")
    with pytest.raises(SystemExit):
        K.main(["build", "--repo", str(ROOT), "--base-installed", str(base), "--out", str(tmp_path / "x.json")])
    (base / "smart_move_calendar_guard.py").unlink()
    (base / "smart_move_engine20.py").unlink()
    with pytest.raises(SystemExit):
        K.build(ROOT, base)
