"""Derive the kit W installer (w1_run.ps1) from the kit V one.

    python deploy/make_w1.py

Reads   C:/Users/initi/SmartMoveRun/release_candidates/v1/v1_run.ps1
Writes  C:/Users/initi/SmartMoveRun/release_candidates/w1/w1_run.ps1

Written fresh, like make_t1.py, rather than by text surgery on the previous generator.

THE LETTER BUMP HAZARD, guarded: the pair "v1" appearing inside another token (a word ending in "v",
a hex string, a variable). Checked in both cases before anything is written; the script refuses
rather than guesses. (".ps1" does not contain "v1", so the extension needs no masking.)

THE TWO PINS ARE THE WHOLE SAFETY STORY:
  SHA     the commit whose GitHub archive is downloaded - the one that CONTAINS the built package.
  ANCHOR  SHA-256 of MANIFEST.sha256, LF-normalised, as build_package.py printed it.
"""
from __future__ import annotations

import hashlib
import io
import re
import sys
from pathlib import Path

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/v1/v1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/w1/w1_run.ps1")

SHA = "407a1c2497cc7b0b66eb43bcd12f90cba5c36856"
ANCHOR = "ee6e85c5208fec84a5e91b7688cefaac82691e45360ea642110b139245614150"

# kit V's pins: if either survives, the letter bump did not do its job
PREVIOUS_PINS = ("394db67932c1862989832b88c53cae9c21cbaa49",
                 "93af239ff3b9aaf11249b820c2546d0a68fd6109875464bb4c2d76bb80ef8d47")

HEADER = (
    "# TRDGO-US Stage W runner: one line in one file, on top of kit W. Found on 2026-11-26 straight after kit W was installed: the paid trading gateway accepted the licence key but refused to open any session - 'Id parameter is required' - because, unlike the trial host, it wants the caller to choose the session id (its own description calls the field optional). "
    "THE CHANGE: mt5_bridge.connect() sends a fresh random session id with each connect when the paid host is configured (MT5_GATEWAY_URL in backend/.env); without that setting the call is exactly as before. The id is the session token: it is masked in every log line and never returned. "
    "BACKEND: mt5_bridge.py changed. Nothing else: no frontend change, no other order-path file, the paper book, A.27 and every Smart Move file untouched. NEW_UI_USERS is not touched."
)


def main() -> int:
    if set(SHA) == {"0"} or set(ANCHOR) == {"0"}:
        print("refusing: SHA / ANCHOR are still placeholders - set them from build_package.py's output")
        return 2

    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    stray = re.findall(r"[A-Za-z0-9]v1|v1[0-9]|[A-Za-z0-9]V1|V1[0-9]", text)
    if stray:
        print(f"refusing: the letter pair appears inside another token: {sorted(set(stray))}")
        return 2

    out = text.replace("v1", "w1").replace("V1", "W1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER + ("\r" if lines[0].endswith("\r") else "")
    out = "\n".join(lines)

    out, n1 = re.subn(r"\$w1Sha\s*=\s*'[0-9a-f]{40}'", f"$w1Sha    = '{SHA}'", out, count=1)
    out, n2 = re.subn(r"\$w1Anchor\s*=\s*'[0-9a-f]{64}'", f"$w1Anchor = '{ANCHOR}'", out, count=1)
    if n1 != 1 or n2 != 1:
        print(f"refusing: the pins were not written (sha {n1}, anchor {n2})")
        return 2
    for old in PREVIOUS_PINS:
        if old in out:
            print(f"refusing: kit V's pin {old[:12]} survived")
            return 2
    if "V1" in out or re.search(r"\$v1[A-Za-z]", out) or "v1_run" in out or "_v1_" in out:
        print("refusing: a kit V identifier survived")
        return 2

    DST.parent.mkdir(parents=True, exist_ok=True)
    io.open(DST, "w", encoding="utf-8", newline="").write(out)   # newline="" preserves CRLF as found
    raw = DST.read_bytes()
    print(f"wrote {DST}")
    print(f"  bytes      {len(raw)}")
    print(f"  lines      {out.count(chr(10)) + 1}")
    print(f"  CR-CR-LF   {raw.count(bytes([13, 13, 10]))}")
    print(f"  SHA-256    {hashlib.sha256(raw).hexdigest().upper()}")
    print(f"  pinned sha {SHA}")
    print(f"  anchor     {ANCHOR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
