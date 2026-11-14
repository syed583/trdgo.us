"""Builds the kit v6.7.2 (agreement rule + horizon refresh) release-candidate ZIPs LOCALLY and deterministically (same inputs -> same bytes). Nothing is uploaded, installed or pushed.

    python build_release_candidate_v6.py --repo <private repo root> --base-v5-backend <dir holding the installed kit v5 backend files> --installed-p1-wrapper <P1 run_engine20_collection.ps1> --out <folder>

Outputs (in --out):
  stage_c_kit_v6_7_2.zip                      engine kit v6.7.2: payload/backend (3 files), KIT_V6_7_2_MANIFEST.json, installer + verifier, MANIFEST.sha256, README
  stage_d_collector_wrapper_v2.zip        collector wrapper v2 (adds -Profile): wrapper, settings template, wrapper test script, wrapper installer + manifest, MANIFEST.sha256, README
  RELEASE_HASHES_V6_7_2.txt                   SHA-256 of both zips and both release anchors (= SHA-256 of each MANIFEST.sha256)
The base hashes come from the installed v6.1 files you point --base-v5-backend at (argument name kept from the v6.1 builder; the directory must hold the installed v6.1 backend files)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kit_v6_7_2_manifest as K  # noqa: E402

FIXED = (1980, 1, 1, 0, 0, 0)


def lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def sha_b(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def deterministic_zip(files: dict, out: Path) -> str:
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


KIT_README = r"""KIT V6.7.1 (live-pass fix) - install ONLY with the owner's explicit approval, from an elevated PowerShell on the server.
Two backend files, both corrections to kit v6.7: smart_move_live.py and smart_move_calendar_session.py. No behaviour is added.
  1. verify the zip SHA-256 and MANIFEST.sha256 anchor against RELEASE_HASHES_V6_7_2.txt (sent separately)
  2. python install_kit_v6_7_2.py install  --manifest KIT_V6_7_2_MANIFEST.json --payload payload\backend --app-backend C:\apps\us-stock-reader\backend --backup-root C:\smlive\kit_v6_7_2_backups --state-root C:\apps\us-stock-reader\sm_collector_state --store-dir C:\apps\us-stock-reader\sm_evidence_store
  3. python install_kit_v6_7_2.py rollback --manifest KIT_V6_7_2_MANIFEST.json --app-backend ... (same paths)      # exact return to kit v6.7
  4. python install_kit_v6_7_2.py recover  --manifest KIT_V6_7_2_MANIFEST.json --app-backend ... (same paths)      # only after an interrupted install
The installer refuses while the collector lock exists, while the runner kill switch is missing, while the installed files differ
from the approved v6.7 hashes, or while another operation runs.
WHAT IT FIXES: the scheduled live pass has exited 1 after about six seconds with no attempts since v6.7 went in, so nothing has
been scanned since 2026-11-13. score_all held the calendar history in a variable the same loop reuses for the earnings rows, so
from the second symbol onwards a list reached history_session where a dict belongs. The variable is renamed and the helper now
refuses a wrong shape instead of raising.
WHAT DOES NOT CHANGE: session resolution, single-vendor acceptance, SESSION_UNKNOWN, scoring, thresholds, the liquidity gate,
the windows, the provider budgets and the 700/day limit are all exactly as v6.7 installed them.
"""

WRAPPER_README = """COLLECTOR WRAPPER V2 (adds -Profile engine20|monday_bmo_friday, default engine20 = unchanged behaviour) - replaces collector_tools\\run_engine20_collection.ps1 only. The settings file is never touched.
  python install_wrapper_p1.py install  --manifest WRAPPER_V2_MANIFEST.json --payload collector_tools\\run_engine20_collection.ps1 --target C:\\apps\\us-stock-reader\\collector_tools\\run_engine20_collection.ps1 --backup-root C:\\smlive\\wrapper_v2_backups --state-root C:\\apps\\us-stock-reader\\sm_collector_state --store-dir C:\\apps\\us-stock-reader\\sm_evidence_store
  python install_wrapper_p1.py rollback ... (same paths)
Same protections as the kit installer (no active collector lock, runner kill switch present, baseline hash of the installed wrapper).
"""


def build(repo: Path, base_v5: Path, p1_wrapper: Path, out: Path) -> dict:
    repo, out = Path(repo), Path(out)
    manifest = K.build(repo, Path(base_v5))
    v5dir = HERE.parent / "kit_v5"
    kit_files = {"KIT_V6_7_2_MANIFEST.json": json.dumps(manifest, indent=1).encode("utf-8"), "README_INSTALL.txt": KIT_README.encode("utf-8"),
                 "install_kit_v6_7_2.py": lf((HERE / "install_kit_v6_7_2.py").read_bytes()), "kit_v6_7_2_manifest.py": lf((HERE / "kit_v6_7_2_manifest.py").read_bytes())}
    for n in K.CHANGED + K.NEW:
        kit_files["payload/backend/" + n] = lf((repo / "backend" / n).read_bytes())
    kit = with_manifest(kit_files, "stage_c_kit_v6_7_2")
    kit_sha = deterministic_zip(kit, out / "stage_c_kit_v6_7_2.zip")
    kit_anchor = sha_b(kit["stage_c_kit_v6_7_2/MANIFEST.sha256"])
    p1_dir = repo / "docs" / "smart_move" / "p1" / "collector_tools"
    wrapper = lf((p1_dir / "run_engine20_collection.ps1").read_bytes())
    wm = {"before_sha256": sha_b(lf(Path(p1_wrapper).read_bytes())), "after_sha256": sha_b(wrapper), "note": "before = the installed P1 wrapper; after = wrapper v2 (adds -Profile)"}
    wfiles = {"WRAPPER_V2_MANIFEST.json": json.dumps(wm, indent=1).encode("utf-8"), "README_INSTALL.txt": WRAPPER_README.encode("utf-8"), "collector_tools/run_engine20_collection.ps1": wrapper,
              "collector_tools/collector.settings.template.p1.txt": lf((p1_dir / "collector.settings.template.p1.txt").read_bytes()), "collector_tools/test_collector_wrapper.ps1": lf((p1_dir / "test_collector_wrapper.ps1").read_bytes()),
              "install_wrapper_p1.py": lf((v5dir / "install_wrapper_p1.py").read_bytes()), "install_kit_v5.py": lf((v5dir / "install_kit_v5.py").read_bytes()), "kit_v5_manifest.py": lf((v5dir / "kit_v5_manifest.py").read_bytes())}
    wr = with_manifest(wfiles, "stage_d_collector_wrapper_v2")
    wr_sha = deterministic_zip(wr, out / "stage_d_collector_wrapper_v2.zip")
    wr_anchor = sha_b(wr["stage_d_collector_wrapper_v2/MANIFEST.sha256"])
    hashes = {"stage_c_kit_v6_7_2.zip": {"sha256": kit_sha, "anchor_manifest_sha256": kit_anchor, "files": len(kit)}, "stage_d_collector_wrapper_v2.zip": {"sha256": wr_sha, "anchor_manifest_sha256": wr_anchor, "files": len(wr)},
              "kit_v6_manifest": manifest, "wrapper_v2_manifest": wm}
    (out / "RELEASE_HASHES_V6_7_2.txt").write_text("\n".join("%s\n  zip sha256 : %s\n  anchor     : %s (SHA-256 of MANIFEST.sha256)\n  files      : %d\n" % (k, v["sha256"], v["anchor_manifest_sha256"], v["files"]) for k, v in hashes.items() if k.endswith(".zip")), encoding="utf-8")
    return hashes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base-v5-backend", required=True)
    ap.add_argument("--installed-p1-wrapper", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(json.dumps(build(Path(a.repo), Path(a.base_v5_backend), Path(a.installed_p1_wrapper), Path(a.out)), indent=1))
