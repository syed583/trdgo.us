"""Where the Unusual Whales token comes from, whether it has been rotated, and redaction of it from anything that is printed or logged. NEVER returns, prints or stores the token itself.

Loading order used by the Smart Move runner (python-dotenv with override=False): a process environment variable UNUSUAL_WHALES_API_KEY wins, otherwise backend/.env. The admin UI "provider key override"
(a row in the production settings table, read only by the web application at startup) is a separate source that the collector never reads; it cannot be inspected from here.
Reports show only the length and a short SHA-256 fingerprint (8 hex characters), which cannot be reversed to the token.
Nothing here contacts the provider.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

ENV_VAR = "UNUSUAL_WHALES_API_KEY"
DEFAULT_ENV_FILE = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
DEFAULT_BACKUPS = (Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env.bak"),)
_ASSIGN = re.compile(r"^\s*" + ENV_VAR + r"\s*=\s*(.*)$")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")
_KEYVAL = re.compile(r"(?i)((?:api[_-]?key|token|authorization)\s*[=:]\s*)[\"']?[A-Za-z0-9._\-]{8,}[\"']?")


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def read_env_file(path) -> Optional[str]:
    """The value of the last UNUSUAL_WHALES_API_KEY assignment in a dotenv file (None if absent or unreadable). Quotes are stripped as python-dotenv does."""
    try:
        val = None
        for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            m = _ASSIGN.match(ln)
            if m:
                val = m.group(1).strip().strip('"').strip("'")
        return val or None
    except OSError:
        return None


def shape_problems(token: Optional[str]) -> list:
    if not token:
        return ["MISSING"]
    out = []
    if len(token) < 20:
        out.append("TOO_SHORT")
    if re.search(r"\s", token):
        out.append("CONTAINS_WHITESPACE")
    if token != token.strip("\"'"):
        out.append("CONTAINS_QUOTES")
    return out


def active_token(env_file=DEFAULT_ENV_FILE, env=os.environ) -> Optional[str]:
    """What the runner will use: the process variable first, else the .env file (same precedence as load_dotenv(override=False))."""
    return (env.get(ENV_VAR) or "").strip() or read_env_file(env_file)


def inspect(env_file=DEFAULT_ENV_FILE, env=os.environ, backups: Iterable = DEFAULT_BACKUPS, old_fingerprint: Optional[str] = None) -> dict:
    proc = (env.get(ENV_VAR) or "").strip() or None
    filev = read_env_file(env_file)
    tok = proc or filev
    source = "PROCESS_ENVIRONMENT" if proc else "ENV_FILE" if filev else "MISSING"
    fp = fingerprint(tok) if tok else None
    notes = []
    if proc and filev and fingerprint(proc) != fingerprint(filev):
        notes.append("the process variable and backend/.env hold DIFFERENT tokens; the process variable wins for this run")
    bk = []
    for b in backups:
        v = read_env_file(b)
        if Path(b).exists():
            bk.append({"file": str(b), "holds_a_token": bool(v), "fingerprint": fingerprint(v) if v else None, "same_as_active": bool(v and tok and fingerprint(v) == fp),
                       "same_as_old": bool(v and old_fingerprint and fingerprint(v) == old_fingerprint)})
    rotated = None if not old_fingerprint else bool(fp and fp != old_fingerprint)
    return {"source": source, "env_file": str(env_file), "length": len(tok) if tok else 0, "fingerprint": fp, "shape_problems": shape_problems(tok), "notes": notes,
            "file_token_fingerprint": fingerprint(filev) if filev else None, "process_token_fingerprint": fingerprint(proc) if proc else None,
            "rotated_from_old": rotated, "backup_copies": bk,
            "ready_for_connectivity_test": bool(tok) and not shape_problems(tok) and rotated is not False and not any("DIFFERENT" in n for n in notes),
            "note": "no provider request was made; the token itself is never shown"}


def redact(text: str, env_file=DEFAULT_ENV_FILE, env=os.environ, extra: Iterable[str] = ()) -> str:
    """Remove the active token (and any extra known secrets), Bearer headers and key=value secrets from text."""
    out = str(text)
    for secret in {t for t in (active_token(env_file, env), *extra) if t and len(t) >= 8}:
        out = out.replace(secret, "[REDACTED]")
    out = _BEARER.sub(r"\1[REDACTED]", out)
    return _KEYVAL.sub(r"\1[REDACTED]", out)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Inspect the Unusual Whales token source (no provider request, token never shown).")
    ap.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    ap.add_argument("--old-fingerprint", default=None, help="8-hex fingerprint of the token being replaced; reports whether the active token is different")
    a = ap.parse_args(argv)
    print(json.dumps(inspect(a.env_file, old_fingerprint=a.old_fingerprint), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
