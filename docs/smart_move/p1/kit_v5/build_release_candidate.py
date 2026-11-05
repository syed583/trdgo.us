"""Builds the P1 release-candidate ZIPs LOCALLY and deterministically (same inputs -> same bytes). Nothing is uploaded, installed or pushed.

    python build_release_candidate.py --repo <private repo root> --installed-v3-wrapper <collector_v3 run_engine20_collection.ps1> --out <folder>

Outputs (in --out):
  stage_c_kit_v5.zip                 engine kit v5: payload/backend (six files), KIT_V5_MANIFEST.json, installer + verifier, MANIFEST.sha256, README
  stage_d_collector_p1_wrapper.zip   collector wrapper RC: P1 wrapper, settings template, wrapper test script, wrapper installer + manifest, MANIFEST.sha256, README
  RELEASE_HASHES.txt                 SHA-256 of both zips and both release anchors (= SHA-256 of each MANIFEST.sha256)
The base (installed v4) hashes in the manifests come from the git blobs of commit 274fccac, whose bytes were verified equal to kit v4 / collector v3."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kit_v5_manifest as K  # noqa: E402

BASE_COMMIT = "274fccac"
FIXED = (1980, 1, 1, 0, 0, 0)


def lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def sha_b(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def deterministic_zip(files: dict, out: Path) -> str:
    """files: {archive path: bytes}. Sorted, fixed timestamps, no extra attributes."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in sorted(files):
            zi = zipfile.ZipInfo(name, FIXED)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, files[name])
    return sha_b(out.read_bytes())


def with_manifest(files: dict, root: str) -> dict:
    lines = ["%s  %s" % (sha_b(files[n]), n) for n in sorted(files)]
    files = dict(files)
    files["MANIFEST.sha256"] = ("\n".join(lines) + "\n").encode("ascii")
    return {root + "/" + n: b for n, b in files.items()}


def git_blob(repo: Path, rel: str) -> bytes:
    return subprocess.run(["git", "show", "%s:%s" % (BASE_COMMIT, rel)], cwd=repo, capture_output=True, check=True).stdout


KIT_README = """KIT V5 (release candidate) - install ONLY with the owner's explicit approval, from an elevated PowerShell on the server.
Nothing in this archive has been run against a real application folder; it was rehearsed on isolated copies.
  1. verify the zip SHA-256 and MANIFEST.sha256 anchor against RELEASE_HASHES.txt (sent separately)
  2. python install_kit_v5.py install  --manifest KIT_V5_MANIFEST.json --payload payload\\backend --app-backend C:\\apps\\us-stock-reader\\backend --backup-root C:\\smlive\\kit_v5_backups --state-root C:\\apps\\us-stock-reader\\sm_collector_state --store-dir C:\\apps\\us-stock-reader\\sm_evidence_store
  3. python install_kit_v5.py rollback --manifest KIT_V5_MANIFEST.json --app-backend ... (same paths)         # exact return to v4
  4. python install_kit_v5.py recover  --manifest KIT_V5_MANIFEST.json --app-backend ... (same paths)         # only after an interrupted install
The installer refuses while the collector lock exists, while the runner kill switch is missing, while the installed files differ from the approved v4 hashes, or while another operation runs.
"""

WRAPPER_README = """COLLECTOR WRAPPER P1 (release candidate) - replaces collector_tools\\run_engine20_collection.ps1 only. The settings file is never touched; P1 stays OFF until CalendarHorizonDays / deadline_priority are added to it.
  python install_wrapper_p1.py install  --manifest WRAPPER_P1_MANIFEST.json --payload collector_tools\\run_engine20_collection.ps1 --target C:\\apps\\us-stock-reader\\collector_tools\\run_engine20_collection.ps1 --backup-root C:\\smlive\\wrapper_p1_backups --state-root C:\\apps\\us-stock-reader\\sm_collector_state --store-dir C:\\apps\\us-stock-reader\\sm_evidence_store
  python install_wrapper_p1.py rollback ... (same paths)
Same protections as the kit installer (no active collector lock, runner kill switch present, baseline hash of the installed wrapper).
"""


def build(repo: Path, v3_wrapper: Path, out: Path) -> dict:
    repo, out = Path(repo), Path(out)
    # ---------------- engine kit v5
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        for n in K.CHANGED + K.PROTECTED:
            (base / n).write_bytes(git_blob(repo, "backend/" + n))
        manifest = K.build(repo, base)
    kit_files = {"KIT_V5_MANIFEST.json": json.dumps(manifest, indent=1).encode("utf-8"), "README_INSTALL.txt": KIT_README.encode("utf-8"),
                 "install_kit_v5.py": lf((HERE / "install_kit_v5.py").read_bytes()), "kit_v5_manifest.py": lf((HERE / "kit_v5_manifest.py").read_bytes())}
    for n in K.CHANGED + K.NEW:
        kit_files["payload/backend/" + n] = lf((repo / "backend" / n).read_bytes())
    kit = with_manifest(kit_files, "stage_c_kit_v5")
    kit_sha = deterministic_zip(kit, out / "stage_c_kit_v5.zip")
    kit_anchor = sha_b(kit["stage_c_kit_v5/MANIFEST.sha256"])
    # ---------------- collector wrapper RC
    p1_dir = repo / "docs" / "smart_move" / "p1" / "collector_tools"
    wrapper = lf((p1_dir / "run_engine20_collection.ps1").read_bytes())
    wm = {"before_sha256": sha_b(lf(Path(v3_wrapper).read_bytes())), "after_sha256": sha_b(wrapper), "note": "before = the installed collector v3 wrapper; after = the P1 wrapper release candidate"}
    wfiles = {"WRAPPER_P1_MANIFEST.json": json.dumps(wm, indent=1).encode("utf-8"), "README_INSTALL.txt": WRAPPER_README.encode("utf-8"), "collector_tools/run_engine20_collection.ps1": wrapper,
              "collector_tools/collector.settings.template.p1.txt": lf((p1_dir / "collector.settings.template.p1.txt").read_bytes()), "collector_tools/test_collector_wrapper.ps1": lf((p1_dir / "test_collector_wrapper.ps1").read_bytes()),
              "install_wrapper_p1.py": lf((HERE / "install_wrapper_p1.py").read_bytes()), "install_kit_v5.py": lf((HERE / "install_kit_v5.py").read_bytes()), "kit_v5_manifest.py": lf((HERE / "kit_v5_manifest.py").read_bytes())}
    wr = with_manifest(wfiles, "stage_d_collector_p1_wrapper")
    wr_sha = deterministic_zip(wr, out / "stage_d_collector_p1_wrapper.zip")
    wr_anchor = sha_b(wr["stage_d_collector_p1_wrapper/MANIFEST.sha256"])
    hashes = {"stage_c_kit_v5.zip": {"sha256": kit_sha, "anchor_manifest_sha256": kit_anchor, "files": len(kit)}, "stage_d_collector_p1_wrapper.zip": {"sha256": wr_sha, "anchor_manifest_sha256": wr_anchor, "files": len(wr)}}
    (out / "RELEASE_HASHES.txt").write_text("\n".join("%s\n  zip sha256 : %s\n  anchor     : %s (SHA-256 of MANIFEST.sha256)\n  files      : %d\n" % (k, v["sha256"], v["anchor_manifest_sha256"], v["files"]) for k, v in hashes.items()), encoding="utf-8")
    return hashes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--installed-v3-wrapper", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(json.dumps(build(Path(a.repo), Path(a.installed_v3_wrapper), Path(a.out)), indent=1))
