"""Historical-evidence preservation check (offline, read only). Builds a manifest (file -> size, sha256, line count) of append-only JSONL evidence and payload folders, and compares a later manifest with an
earlier one: a JSONL file may GROW (the old bytes must still be a byte-identical prefix) but may never shrink, change or vanish, and any other file (payloads) may never change or vanish.
It deletes and rewrites nothing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_manifest(paths: Iterable) -> dict:
    """paths: files and/or folders (walked recursively)."""
    files = {}
    for p in map(Path, paths):
        for f in ([p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())):
            data = f.read_bytes()
            ent = {"size": len(data), "sha256": _sha(data)}
            if f.suffix == ".jsonl":
                ent["lines"] = sum(1 for ln in data.split(b"\n") if ln.strip())
            files[str(f)] = ent
    return {"schema": "smart_move.evidence_manifest.v1", "files": files}


def compare(old: dict, new: dict) -> dict:
    """-> {ok, missing, changed, truncated, appended, added}."""
    res = {"missing": [], "changed": [], "truncated": [], "appended": [], "added": []}
    for f, o in old["files"].items():
        n = new["files"].get(f)
        if n is None:
            res["missing"].append(f)
        elif n["sha256"] == o["sha256"]:
            continue
        elif not f.endswith(".jsonl"):
            res["changed"].append(f)
        elif n["size"] < o["size"]:
            res["truncated"].append(f)
        else:
            data = Path(f).read_bytes()
            (res["appended"] if _sha(data[: o["size"]]) == o["sha256"] else res["changed"]).append(f)
    res["added"] = sorted(set(new["files"]) - set(old["files"]))
    res["ok"] = not (res["missing"] or res["changed"] or res["truncated"])
    return res


def save(manifest: dict, path) -> Path:
    p = Path(path)
    if p.exists():
        raise FileExistsError(p)
    p.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return p
