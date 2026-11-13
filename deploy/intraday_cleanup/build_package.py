"""Build the A.10 deploy package (deploy/intraday_cleanup/) from this checkout. Run from the repository root AFTER
``npm run build`` in frontend/:

    python deploy/intraday_cleanup/build_package.py

It writes, under deploy/intraday_cleanup/:
  backend_files.txt   every tracked backend file the installer copies (relative to the repo root), one per line
  removed_files.txt   backend modules the installer deletes on the server (retired boards, v1 engine helpers)
  protected.txt       path prefixes the installer must NEVER write or delete (Smart Money Move, earnings monitor, .env, state)
  frontend_dist/      the built frontend (copied from frontend/dist)
  MANIFEST.sha256     LF-normalised SHA-256 of every file the installer will place (backend files + frontend_dist + lists)

The installer (a10_run.ps1, kept outside the repository) pins the commit and the SHA-256 of MANIFEST.sha256.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "deploy" / "intraday_cleanup"

# Never written by the installer (installed by their own kits / installers, or live state). Prefixes, repo-relative.
PROTECTED = [
    "backend/.env", "backend/.env.", "backend/smart_move_", "backend/earnings_monitor/", "backend/provider_ledger.py",
    "backend/signal_class.py", "backend/.provider_ledger/", "backend/logs/", "backend/.cache/", "backend/.sec_cache/",
    "backend/.venv/", "backend/mt5_state/", "collector_tools/", "docs/smart_move/", "sm_collector_state/", "sm_evidence_store/",
    "smart_move_data/", "em_data/", "monitor_install_record.json", "smart_move_collector_install.json",
    "smart_move_engine_install.json", "vps_uw_cap.ps1", "vps_uw_credential_audit.ps1", "vps_uw_token_update.ps1",
]
# Removed from the server by the installer (deleted from git in the intraday cleanup).
REMOVED = [
    "backend/chatgpt_intraday_service.py", "backend/claude_pro_intraday_service.py", "backend/intraday_signal_engine.py",
    "backend/sheet_logger.py", "backend/signal_audit_log.py", "backend/signal_state_store.py", "backend/swing_manager.py",
    "backend/swing_track.py",
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def protected(rel: str) -> bool:
    return any(rel.startswith(x) for x in PROTECTED)


def main() -> int:
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        print("frontend/dist/index.html missing: run `npm run build` in frontend/ first"); return 2
    # A.23 guard (2026-11-13): the built frontend must be newer than every source file under frontend/src, or the package
    # ships a stale UI (A.18..A.22 pages never reached the server because dist was last built for A.14).
    src_dir = ROOT / "frontend" / "src"
    newest_src = max((f.stat().st_mtime for f in src_dir.rglob("*") if f.is_file()), default=0.0)
    if (dist / "index.html").stat().st_mtime < newest_src:
        print("frontend/dist is OLDER than frontend/src: run `npm run build` in frontend/ first (stale UI would ship)"); return 2
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "backend"], capture_output=True, text=True, check=True).stdout.split()
    files = sorted(f for f in tracked if not f.startswith("backend/tests/") and not protected(f)
                   and (f.endswith((".py", ".sql", ".txt", ".ini", ".json")) or f == "backend/requirements.txt"))
    # the installer never copies smart_move_* even though two such files are tracked on this branch
    files = [f for f in files if not protected(f)]
    for f in files:
        assert (ROOT / f).is_file(), f
    (PKG / "backend_files.txt").write_text("\n".join(files) + "\n", encoding="utf-8", newline="\n")
    (PKG / "removed_files.txt").write_text("\n".join(REMOVED) + "\n", encoding="utf-8", newline="\n")
    (PKG / "protected.txt").write_text("\n".join(PROTECTED) + "\n", encoding="utf-8", newline="\n")
    fd = PKG / "frontend_dist"
    if fd.exists():
        shutil.rmtree(fd)
    shutil.copytree(dist, fd)
    lines = []
    for f in files:
        lines.append(f"{sha(ROOT / f)} *{f}")
    for p in sorted(fd.rglob("*")):
        if p.is_file():
            lines.append(f"{sha(p)} *deploy/intraday_cleanup/frontend_dist/{p.relative_to(fd).as_posix()}")
    for name in ("backend_files.txt", "removed_files.txt", "protected.txt"):
        lines.append(f"{sha(PKG / name)} *deploy/intraday_cleanup/{name}")
    man = PKG / "MANIFEST.sha256"
    man.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"package: {len(files)} backend files, {sum(1 for p in fd.rglob('*') if p.is_file())} frontend files, "
          f"{len(REMOVED)} removals; manifest entries {len(lines)}; anchor (sha256 of MANIFEST.sha256, LF) = {sha(man)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
