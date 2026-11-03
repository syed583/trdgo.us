"""Read-only API for the Engine20 prediction snapshot page (local Smart Move server; production registration is a separate, approved deployment step). It serves the newest immutable snapshot written by
smart_move_engine20_snapshot.build_snapshot and the outcome comparison. No provider, no scoring, no write.
Environment: SMART_MOVE_ENGINE20_DIR = folder holding snapshots.jsonl and the snapshot files; SMART_MOVE_OUTCOMES_DIR = outcome store (optional; without it nothing is compared)."""
import os
from datetime import datetime, timezone

from fastapi import APIRouter

import smart_move_engine20_snapshot as S

router = APIRouter(prefix="/api/smart-move/engine20", tags=["smart-move-engine20"])


def _latest():
    d = os.getenv("SMART_MOVE_ENGINE20_DIR")
    if not d:
        return None, {"status": "NO_SNAPSHOT", "detail": "No snapshot folder is configured (SMART_MOVE_ENGINE20_DIR)."}
    snap = S.load_latest(d)
    if snap is None:
        return None, {"status": "NO_SNAPSHOT", "detail": "No prediction snapshot has been generated yet."}
    if "error" in snap:
        return None, {"status": "SNAPSHOT_UNVERIFIED", "detail": "The newest snapshot file is missing or no longer matches its recorded hash.", **snap}
    return snap, None


@router.get("/latest")
def latest():
    snap, err = _latest()
    if snap is None:
        return err
    return {"status": "OK", "read_only": True, **S.read_status(snap, datetime.now(timezone.utc))}


@router.get("/outcomes")
def outcomes():
    snap, err = _latest()
    if snap is None:
        return err
    return {"status": "OK", "snapshot_generated_at": snap["generated_at"], **S.outcomes(snap, os.getenv("SMART_MOVE_OUTCOMES_DIR"), datetime.now(timezone.utc))}
