"""Derive the kit U installer (u1_run.ps1) from the kit T one.

    python deploy/make_u1.py

Reads   C:/Users/initi/SmartMoveRun/release_candidates/t1/t1_run.ps1
Writes  C:/Users/initi/SmartMoveRun/release_candidates/u1/u1_run.ps1

Written fresh, like make_t1.py, rather than by text surgery on the previous generator.

THE LETTER BUMP HAZARD, guarded: the pair "t1" appearing inside another token (a word ending in "t",
a hex string, a variable). Checked in both cases before anything is written; the script refuses
rather than guesses. (".ps1" does not contain "t1", so the extension needs no masking this time.)

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

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/t1/t1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/u1/u1_run.ps1")

SHA = "5a9a16c8e7a429e96801793b3de32d31c491ff2a"
ANCHOR = "df9089b1c5717ad8c20c69f05333129984e2e485ba4ce9f96a7e42146514d927"

# kit T's pins: if either survives, the letter bump did not do its job
PREVIOUS_PINS = ("c1601f538c9953ee3f3bb7f4154763c380f39fb8",
                 "6362f0304ccc6e8b4063a40192e693cb461cb9ab20b5e889bf5ba4790b69da09")

HEADER = (
    "# TRDGO-US Stage U runner: six owner-requested changes from 2026-11-26. "
    "ONE - the four link cards in the landing page hero (Earnings, Options Flow, Strategy Trades, Smart Money Move) are removed; every one of those pages is still in the side menu. "
    "TWO - the Smart Money filter pills: each label now stays on one line inside its pill and the row wraps, where long labels used to break onto two or three lines and spill out of the outline. "
    "THREE - IV CRUSH CHECK SHOWED NO PRICES because 57 of its 64 rows had been computed EMPTY on 20-21 November and were never computed again: the scan only ever queued a row that was missing altogether. An empty row is now tried again after 30 minutes and a priced row is refreshed after 6 hours; the old value stays on screen meanwhile. WHY those rows came back empty that day is NOT known. THIS IS THE ONLY BACKEND CHANGE: tab_b_service.py and tab_settings.py. "
    "FOUR and FIVE - Post-Earnings AI (a simulated test whose scheduler last ran on 22 October) and Institutional Intelligence (its dataset was never configured on this server, so every tab said so) are hidden from the side menu. Both routes and all their data are kept; nothing is deleted. "
    "SIX - My Broker takes the redesigned look: hero, four tiles, cards, buttons and tables. PRESENTATION ONLY: every handler, confirmation, field name and request on that page is unchanged, and the classic design is untouched. "
    "NEW_UI_USERS is not touched. No scoring, A.27 rule, order logic, MT5, or Smart Move engine, route or scanner change."
)


def main() -> int:
    if set(SHA) == {"0"} or set(ANCHOR) == {"0"}:
        print("refusing: SHA / ANCHOR are still placeholders - set them from build_package.py's output")
        return 2

    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    stray = re.findall(r"[A-Za-z0-9]t1|t1[0-9]|[A-Za-z0-9]T1|T1[0-9]", text)
    if stray:
        print(f"refusing: the letter pair appears inside another token: {sorted(set(stray))}")
        return 2

    out = text.replace("t1", "u1").replace("T1", "U1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER + ("\r" if lines[0].endswith("\r") else "")
    out = "\n".join(lines)

    out, n1 = re.subn(r"\$u1Sha\s*=\s*'[0-9a-f]{40}'", f"$u1Sha    = '{SHA}'", out, count=1)
    out, n2 = re.subn(r"\$u1Anchor\s*=\s*'[0-9a-f]{64}'", f"$u1Anchor = '{ANCHOR}'", out, count=1)
    if n1 != 1 or n2 != 1:
        print(f"refusing: the pins were not written (sha {n1}, anchor {n2})")
        return 2
    for old in PREVIOUS_PINS:
        if old in out:
            print(f"refusing: kit T's pin {old[:12]} survived")
            return 2
    if "T1" in out or re.search(r"\$t1[A-Za-z]", out) or "t1_run" in out or "_t1_" in out:
        print("refusing: a kit T identifier survived")
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
