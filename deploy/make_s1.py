"""Derive the kit O installer (s1_run.ps1) from the kit N one.

The installer is deliberately kept OUT of the repository - it is the thing that changes production,
and it should be reviewed as a file on the operator's machine rather than imported from a checkout.
What lives here is how it was derived, so the next person can reproduce it instead of trusting a
blob: take the previous letter's runner, repoint the two pins, rename the letter, rewrite the
header.

    python -I deploy/make_p1.py

Reads   C:/Users/initi/SmartMoveRun/release_candidates/m1/m1_run.ps1
Writes  C:/Users/initi/SmartMoveRun/release_candidates/s1/s1_run.ps1

WHY A NEW LETTER. Installing the same letter twice needs a Rollback first, because the runner
refuses to proceed when an install record for its own letter already exists. Kit M's record is on
the server; N has none, so Update can run straight away and M's record stays intact as the thing
Rollback returns to.

THE TWO PINS ARE THE WHOLE SAFETY STORY:
  sha     the commit whose GitHub archive is downloaded. It must be the commit that CONTAINS the
          built package, not the source commit before it.
  anchor  SHA-256 of MANIFEST.sha256 itself, LF-normalised, as build_package.py printed it. The
          runner refuses to install an archive whose manifest does not hash to this.
"""
from __future__ import annotations

import hashlib
import io
import re
import sys
from pathlib import Path

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/r1/r1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/s1/s1_run.ps1")

SHA = "5b8ac6a3ab28fe75011d58e32bc09fcd01e34823"
ANCHOR = "859ff0472057ee21e69112bfb03a131e10aa5df8d68edc037128cdc795872bb6"

HEADER = (
    "# TRDGO-US Stage Q runner: the boxes that overlapped and the text that ran off the edge, found by auditing ALL 23 PAGES on the live site while signed in. THE CAUSE WAS ONE MISTAKE OF MINE, REPEATED. When the redesign was matched to the design file I copied each frame's numbers literally - heights AND widths. A frame is one drawing of one state, so its height is whatever happened to be in it that day; when the real page has more, the box stays the size it was pinned to, the extra escapes, and everything below is placed as though the box were still short. That is the banner that was drawn on top of the Smart Money cards. Measured on the live site: the Smart Money summary had 90 pixels of box and 190 of content, its filter row 40 against 96, the Market Overview heading 51 against 115, and the index strip needed 933 pixels inside 808, which is why the VIX card was sliced in half at the edge of the page. THOSE PINNED SIZES ARE NOW MINIMUMS, and the grids wrap instead of spilling. The part that actually mattered was less obvious: a column of 1fr and a flex item both mean NEVER NARROWER THAN MY CONTENT, so widening the tracks changed nothing at all - the floor had to come off the children. And once they could shrink they wrapped, which then met more pinned heights. Three passes, each one measured rather than guessed. THE LANDING HERO was positioned at the design's own coordinates, including a headline the frame draws WIDER THAN THE BOX IT SITS IN; it now keeps those numbers wherever they fit and gives way where they do not, so the desktop layout is unchanged and nothing leaves the page on a narrower screen. ALSO THREE THINGS THE LAST KIT GOT WRONG, every one found by clicking rather than by a test: a flat zero was painted GREEN as though it were a profit, so a day with no trades read as gains; four tiles in a three-column grid left the fourth stranded on a row of its own; and the rule that a bid of zero is not a bid had been applied to the watchlist only, so the analysis page still printed a price of nothing out of hours. THREE FINDINGS WERE FALSE POSITIVES AND WERE DELIBERATELY LEFT ALONE: the sentiment number sits inside its dial on purpose, and two Market Overview blocks are placed beside each other on purpose or do not touch at all. Each was read in the stylesheet before anything was changed. 15 of the 23 pages were already clean. NEW_UI_USERS is not touched. No scoring, A.27 rule, order logic, or Smart Move engine, route or scanner change."
)


def main() -> int:
    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    # A blind r1 -> s1 would be wrong if the digraph appeared inside another word.
    stray = re.findall(r"[A-Za-z0-9]r1|r1[0-9]", text)
    if stray:
        print(f"refusing: 'r1' appears inside another token: {sorted(set(stray))}")
        return 2

    out = text.replace("r1", "s1").replace("R1", "S1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER
    out = "\n".join(lines)

    out = re.sub(r"\$s1Sha\s*=\s*'[0-9a-f]{40}'", f"$s1Sha    = '{SHA}'", out, count=1)
    out = re.sub(r"\$s1Anchor\s*=\s*'[0-9a-f]{64}'", f"$s1Anchor = '{ANCHOR}'", out, count=1)

    if SHA not in out or ANCHOR not in out:
        print("refusing: the pins were not written")
        return 2
    for old in ("d4b7bab661100817578a65b74af6493a3358f14d",
                "8d11a7317d431ea2d98be71c4431ede270bbc808ae1e5ea2a90e9086507d73d8"):
        if old in out:
            print(f"refusing: kit R's pin {old[:12]} survived")
            return 2
    if "R1" in out or re.search(r"\$r1[A-Za-z]", out):
        print("refusing: a kit R identifier survived")
        return 2

    DST.parent.mkdir(parents=True, exist_ok=True)
    # CRLF, no BOM - and kit M's runner is LF, so this does NOT match its shape. An earlier
    # comment claimed it did. It was wrong: the patch meant to switch this to LF never matched the
    # line it was editing and nothing checked, so the claim outlived the change. Left as CRLF
    # deliberately: it is the natural Windows shape, PowerShell reads either, and the published
    # SHA-256 is the hash of what is actually on disk. Regenerating it to win a cosmetic argument
    # would invalidate a hash the operator already holds.
    # newline="" PRESERVES whatever the source used instead of translating it. Kit N's runner is
    # CRLF, and asking Python to write CRLF turned every existing CR-LF pair into CR-CR-LF: the
    # file still ran, but PowerShell counted 503 lines where there are 253, so no stated hash
    # could be trusted. Kit N never hit this because ITS source was LF and the translation was a
    # no-op. Preserving is correct whichever shape the previous letter used.
    io.open(DST, "w", encoding="utf-8", newline="").write(out)
    digest = hashlib.sha256(DST.read_bytes()).hexdigest()
    print(f"wrote {DST}")
    print(f"  bytes      {DST.stat().st_size}")
    print(f"  lines      {out.count(chr(10)) + 1}")
    print(f"  SHA-256    {digest.upper()}")
    print(f"  pinned sha {SHA}")
    print(f"  anchor     {ANCHOR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
