"""DESIGN PROOF (not production code): content-addressed, atomic, read-only payload files + integrity scan + daily seal.
Layout: <root>/payloads/ab/cd/<sha256>.json.gz  (sha256 of the RAW provider bytes). Writes: temp file in the same folder -> flush + fsync -> verify by
re-reading -> os.replace to the final name -> mark read-only. An existing final file with the same hash is never rewritten. The metadata row
is inserted only after the file is durable (a crash leaves at worst an orphan file, found by scan(); never a row without a file)."""
import gzip
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path


def rel_for(sha):
    return "payloads/%s/%s/%s.json.gz" % (sha[:2], sha[2:4], sha)


def write_payload(root, raw: bytes):
    sha = hashlib.sha256(raw).hexdigest()
    final = Path(root) / rel_for(sha)
    if final.exists():
        if hashlib.sha256(gzip.decompress(final.read_bytes())).hexdigest() != sha:
            raise RuntimeError("existing file for %s is corrupt: quarantine, do not overwrite" % sha)
        return sha, final, False
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / (".tmp-" + uuid.uuid4().hex)
    with open(tmp, "xb") as fh:
        fh.write(gzip.compress(raw, 6, ))
        fh.flush()
        os.fsync(fh.fileno())
    if hashlib.sha256(gzip.decompress(tmp.read_bytes())).hexdigest() != sha:
        tmp.unlink()
        raise RuntimeError("verification of the temporary file failed")
    os.replace(tmp, final)
    os.chmod(final, stat.S_IREAD)
    return sha, final, True


def verify_all(root):
    bad, n = [], 0
    for f in sorted((Path(root) / "payloads").rglob("*.json.gz")):
        n += 1
        sha = f.name[:-8]
        try:
            ok = hashlib.sha256(gzip.decompress(f.read_bytes())).hexdigest() == sha and str(f.relative_to(root)).replace("\\", "/") == rel_for(sha)
        except Exception:
            ok = False
        if not ok:
            bad.append(str(f))
    return n, bad


def orphans(root, known_hashes):
    files = {f.name[:-8] for f in (Path(root) / "payloads").rglob("*.json.gz")}
    tmps = [str(p) for p in (Path(root) / "payloads").rglob(".tmp-*")]
    return sorted(files - set(known_hashes)), sorted(set(known_hashes) - files), tmps


def seal(root, day, prev_seal_sha):
    shas = sorted(f.name[:-8] for f in (Path(root) / "payloads").rglob("*.json.gz"))
    body = json.dumps({"day": day, "prev": prev_seal_sha, "payloads": shas}, sort_keys=True).encode("ascii")
    return hashlib.sha256(body).hexdigest(), len(shas)
