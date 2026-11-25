"""Derive the kit T installer (t1_run.ps1) from the kit S one.

    python deploy/make_t1.py

Reads   C:/Users/initi/SmartMoveRun/release_candidates/s1/s1_run.ps1
Writes  C:/Users/initi/SmartMoveRun/release_candidates/t1/t1_run.ps1

WRITTEN FRESH rather than derived by text surgery on make_s1.py. Deriving one generator from the
previous one by search-and-replace has gone wrong twice (the blanket letter bump rewrote the
anchors the targeted edits were looking for, so they silently matched nothing). The logic is
small enough to state directly, so it is stated directly.

TWO HAZARDS THE LETTER BUMP HAS, both guarded:
  1. ".ps1" CONTAINS "s1". A blanket s1 -> t1 would rename every PowerShell file to ".pt1", so the
     extension is masked for the duration of the swap. (This did not exist for earlier letters.)
  2. The letter pair appearing inside another token. Checked in both cases before anything is
     written; the script refuses rather than guesses.

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

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/s1/s1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/t1/t1_run.ps1")

SHA = "c1601f538c9953ee3f3bb7f4154763c380f39fb8"
ANCHOR = "6362f0304ccc6e8b4063a40192e693cb461cb9ab20b5e889bf5ba4790b69da09"

# kit S's pins: if either survives, the letter bump did not do its job
PREVIOUS_PINS = ("5b8ac6a3ab28fe75011d58e32bc09fcd01e34823",
                 "859ff0472057ee21e69112bfb03a131e10aa5df8d68edc037128cdc795872bb6")

HEADER = (
    "# TRDGO-US Stage T runner: the LIVE EVENT STRIP is back on Strategy Trades. The old page pinned and blinked every trade event at the top; the redesign kept only the spoken call-out. "
    "A strip now sits directly under the page header, always present so it has room of its own and covers nothing, and holds the last ten events, newest first. "
    "Each event shows the SYMBOL first, then what happened - BUY in green, SELL in red, STOP LOSS in red, TARGET in green, SCORE EXIT in amber - then the price, the time in New York, and for an exit its profit in green or loss in red. "
    "A new event BLINKS FOR SIX SECONDS and then stays, still; the matching table row flashes for the same six seconds and carries a small badge for as long as the event is on the strip. "
    "EVERY EVENT IS A REAL TRADE ROW, found the way the old page found them: by comparing each reading of the trade list with the one before it. The first reading only sets the baseline, so opening the page never announces the whole day as though it had just happened, and a refresh that changes nothing shows nothing - the same event cannot blink twice. "
    "ONE LIST NOW DRIVES THE STRIP, THE ROW FLASH AND THE VOICE ALERT, so what is spoken, what blinks and which row lights up cannot disagree; the sound still obeys its own on/off button. "
    "TWO THINGS SAID PLAINLY ON THE STRIP ITSELF: a close that is not a stop, target or score exit - the end-of-day close, the bell - is shown as CLOSED rather than dropped or mislabelled; and because the server sends an exit DATE but no exit time of day, an exit shows the New York time this page SAW the change, marked seen, which can trail the exit itself by one refresh. Entries show the server's own entry time. "
    "People who have asked their system for less motion get the colours without the pulsing. "
    "FRONTEND ONLY: no backend file changes. NEW_UI_USERS is not touched. No scoring, A.27 rule, order logic, MT5, or Smart Move engine, route or scanner change."
)

_EXT = "\x01PSEXT\x01"


def main() -> int:
    if set(SHA) == {"0"} or set(ANCHOR) == {"0"}:
        print("refusing: SHA / ANCHOR are still placeholders - set them from build_package.py's output")
        return 2

    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    masked = text.replace(".ps1", _EXT)
    stray = re.findall(r"[A-Za-z0-9]s1|s1[0-9]|[A-Za-z0-9]S1|S1[0-9]", masked)
    if stray:
        print(f"refusing: the letter pair appears inside another token: {sorted(set(stray))}")
        return 2

    out = masked.replace("s1", "t1").replace("S1", "T1").replace(_EXT, ".ps1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER + ("\r" if lines[0].endswith("\r") else "")
    out = "\n".join(lines)

    out, n1 = re.subn(r"\$t1Sha\s*=\s*'[0-9a-f]{40}'", f"$t1Sha    = '{SHA}'", out, count=1)
    out, n2 = re.subn(r"\$t1Anchor\s*=\s*'[0-9a-f]{64}'", f"$t1Anchor = '{ANCHOR}'", out, count=1)
    if n1 != 1 or n2 != 1:
        print(f"refusing: the pins were not written (sha {n1}, anchor {n2})")
        return 2
    for old in PREVIOUS_PINS:
        if old in out:
            print(f"refusing: kit S's pin {old[:12]} survived")
            return 2
    if "S1" in out or re.search(r"\$s1[A-Za-z]", out) or ".pt1" in out:
        print("refusing: a kit S identifier survived, or an extension was rewritten")
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
