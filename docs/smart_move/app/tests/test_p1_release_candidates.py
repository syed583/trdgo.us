"""Release-candidate build and the wrapper installer: deterministic ZIPs, manifest/anchor consistency, a kit install straight from the extracted ZIP, and the wrapper installer's collector protections. Local temp folders only."""
import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
KDIR = ROOT / "docs" / "smart_move" / "p1" / "kit_v5"
sys.path.insert(0, str(KDIR))


def load(name):
    spec = importlib.util.spec_from_file_location(name, KDIR / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


K = load("kit_v5_manifest")
I = load("install_kit_v5")
B = load("build_release_candidate")
W = load("install_wrapper_p1")
V3_WRAPPER_BYTES = b"# stand-in for the installed collector v3 wrapper (the real one lives in the collector payload)\r\nWrite-Host 'v3'\r\n"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("rc")
    v3 = d / "v3_wrapper.ps1"
    v3.write_bytes(V3_WRAPPER_BYTES)
    a, b = d / "a", d / "b"
    ha, hb = B.build(ROOT, v3, a), B.build(ROOT, v3, b)
    return dict(dir=d, a=a, b=b, ha=ha, hb=hb, v3=v3)


def test_the_build_is_deterministic_same_inputs_same_zip_bytes(built):
    assert built["ha"] == built["hb"]
    for n in ("stage_c_kit_v5.zip", "stage_d_collector_p1_wrapper.zip"):
        assert (built["a"] / n).read_bytes() == (built["b"] / n).read_bytes()
    assert (built["a"] / "RELEASE_HASHES.txt").read_text() == (built["b"] / "RELEASE_HASHES.txt").read_text()


def test_every_zip_lists_exactly_the_files_in_its_manifest_and_the_anchor_is_the_manifest_hash(built):
    for name, root in (("stage_c_kit_v5.zip", "stage_c_kit_v5"), ("stage_d_collector_p1_wrapper.zip", "stage_d_collector_p1_wrapper")):
        z = zipfile.ZipFile(built["a"] / name)
        names = {i.filename for i in z.infolist()}
        manifest = z.read(root + "/MANIFEST.sha256").decode("ascii").splitlines()
        listed = {l.split("  ", 1)[1]: l.split("  ", 1)[0] for l in manifest}
        assert {root + "/" + n for n in listed} | {root + "/MANIFEST.sha256"} == names
        assert all(hashlib.sha256(z.read(root + "/" + n)).hexdigest() == h for n, h in listed.items())
        assert hashlib.sha256(z.read(root + "/MANIFEST.sha256")).hexdigest() == built["ha"][name]["anchor_manifest_sha256"]
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())
        assert all(b"\r\n" not in z.read(i.filename) for i in z.infolist() if i.filename.endswith((".py", ".ps1", ".json", ".txt", ".sha256")))     # LF bytes only


def test_the_kit_zip_installs_from_its_extracted_payload_on_an_isolated_v4_copy_and_rolls_back(built, tmp_path):
    z = zipfile.ZipFile(built["a"] / "stage_c_kit_v5.zip")
    z.extractall(tmp_path / "x")
    kit = tmp_path / "x" / "stage_c_kit_v5"
    app, bk, state, store = tmp_path / "app", tmp_path / "bk", tmp_path / "state", tmp_path / "store"
    for d in (app, state, store):
        d.mkdir()
    (store / "KILL").write_text("engaged", encoding="utf-8")
    for n in K.CHANGED + K.PROTECTED:
        (app / n).write_bytes(subprocess.run(["git", "show", "%s:backend/%s" % (B.BASE_COMMIT, n)], cwd=ROOT, capture_output=True, check=True).stdout)
    before = {p.name: p.read_bytes() for p in app.iterdir()}
    m = json.loads((kit / "KIT_V5_MANIFEST.json").read_text())
    assert I.install(m, kit / "payload" / "backend", app, bk, state, store)[0] == 0
    assert K.verify(m, None, app, "post")[0] == 0
    assert I.rollback(m, app, bk, state, store)[0] == 0 and {p.name: p.read_bytes() for p in app.iterdir()} == before


# ------------------------------------------------------------------ wrapper installer
@pytest.fixture
def ww(built, tmp_path):
    z = zipfile.ZipFile(built["a"] / "stage_d_collector_p1_wrapper.zip")
    z.extractall(tmp_path / "x")
    pkg = tmp_path / "x" / "stage_d_collector_p1_wrapper"
    tools, state, store, bk = tmp_path / "collector_tools", tmp_path / "state", tmp_path / "store", tmp_path / "bk"
    for d in (tools, state, store):
        d.mkdir()
    (store / "KILL").write_text("engaged", encoding="utf-8")
    target = tools / "run_engine20_collection.ps1"
    target.write_bytes(V3_WRAPPER_BYTES)
    settings = tools / "collector.settings.txt"
    settings.write_text("Mode=DRY_RUN\nSelectPolicy=deadline\nCalendarDaysAhead=3\n", encoding="utf-8")
    return dict(m=json.loads((pkg / "WRAPPER_P1_MANIFEST.json").read_text()), payload=pkg / "collector_tools" / "run_engine20_collection.ps1", target=target, bk=bk, state=state, store=store, settings=settings)


def test_the_wrapper_installs_verifies_leaves_the_settings_alone_and_rolls_back_exactly(ww):
    s0 = ww["settings"].read_bytes()
    code, msgs = W.install(ww["m"], ww["payload"], ww["target"], ww["bk"], ww["state"], ww["store"])
    assert code == 0 and W.sha(ww["target"]) == ww["m"]["after_sha256"] and ww["settings"].read_bytes() == s0
    assert not (ww["state"] / "collection.lock").exists() and not (ww["state"] / "KILL").exists()
    code, msgs = W.rollback(ww["m"], ww["target"], ww["bk"], ww["state"], ww["store"])
    assert code == 0 and ww["target"].read_bytes() == V3_WRAPPER_BYTES and ww["settings"].read_bytes() == s0


def test_the_wrapper_installer_refuses_an_active_lock_a_missing_runner_switch_and_a_wrong_baseline_and_changes_nothing(ww):
    before = ww["target"].read_bytes()
    (ww["state"] / "collection.lock").write_text('{"pid": 1}', encoding="utf-8")
    assert W.install(ww["m"], ww["payload"], ww["target"], ww["bk"], ww["state"], ww["store"])[0] == 2 and ww["target"].read_bytes() == before
    (ww["state"] / "collection.lock").unlink()
    (ww["store"] / "KILL").unlink()
    assert W.install(ww["m"], ww["payload"], ww["target"], ww["bk"], ww["state"], ww["store"])[0] == 2 and ww["target"].read_bytes() == before
    (ww["store"] / "KILL").write_text("engaged", encoding="utf-8")
    ww["target"].write_bytes(b"# an edited wrapper\n")
    code, msgs = W.install(ww["m"], ww["payload"], ww["target"], ww["bk"], ww["state"], ww["store"])
    assert code == 2 and "not the approved baseline" in " ".join(msgs) and ww["target"].read_bytes() == b"# an edited wrapper\n"


def test_a_failed_wrapper_write_restores_the_previous_wrapper(ww, monkeypatch):
    real = I._atomic_write
    calls = {"n": 0}

    def flaky(dst, data, want):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full (injected)")
        return real(dst, data, want)
    monkeypatch.setattr(I, "_atomic_write", flaky)
    code, msgs = W.install(ww["m"], ww["payload"], ww["target"], ww["bk"], ww["state"], ww["store"])
    assert code == 4 and ww["target"].read_bytes() == V3_WRAPPER_BYTES and not (ww["state"] / "collection.lock").exists()
