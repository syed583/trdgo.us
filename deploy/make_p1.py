"""Derive the kit O installer (p1_run.ps1) from the kit N one.

The installer is deliberately kept OUT of the repository - it is the thing that changes production,
and it should be reviewed as a file on the operator's machine rather than imported from a checkout.
What lives here is how it was derived, so the next person can reproduce it instead of trusting a
blob: take the previous letter's runner, repoint the two pins, rename the letter, rewrite the
header.

    python -I deploy/make_o1.py

Reads   C:/Users/initi/SmartMoveRun/release_candidates/m1/m1_run.ps1
Writes  C:/Users/initi/SmartMoveRun/release_candidates/p1/p1_run.ps1

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

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/o1/o1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/p1/p1_run.ps1")

SHA = "3b5848329a91d9316f89d980404b4c052af8d6a0"
ANCHOR = "0db45073edec04ae0c0801279e66bec2e7864887df939254856237f74a10524b"

HEADER = (
    "# TRDGO-US Stage P runner: thirteen fixes found by CLICKING through the live site, not by a test. STRATEGY TRADES symbol links now analyse the symbol you clicked - they were written as a query but the route takes a path, so the router kept the PREVIOUS symbol and clicking AAPL from an NVDA page analysed NVDA, with the ticker box left empty. THE PROFILE CHIP WAS DEAD: an inert div with no handler at all; it is now a real menu (who you are, Help & assistant, Sign out, and the admin links for admins only) - this app has no user-facing Settings page, Settings IS the admin panel, so the menu does not invent one. THE HEADPHONES ICON IS GONE (it was labelled Help, went to /settings, and read as a sound control that did not exist), and THE BELL OPENS A PANEL instead of jumping to the rule builder: the trades the models opened, newest first, with side, price and time, a chime only for something that arrived after you were already looking, and a mute that is remembered; /alerts is still linked in its footer. THE SIDEBAR PROMOTION MOVED INSIDE THE SCROLLING MENU as its last item - it was a sibling of the nav, so it sat in the rail's fixed area and hid four menu rows. STAT TILES no longer clip their own text at 1040px (five across became three). TOP MOVERS lists common stock and ADRs only: the size floor could never exclude a leveraged ETF, because the screener publishes no market cap for one and an unknown cap is kept on purpose, so it needed an issue-type filter - and that filter FAILS OPEN, since an empty movers panel is worse than a few ETFs. SMART MONEY MOVE: the engine version reached the screen from the SERVER'S OWN note field, which the previous kit could not have caught because that string is not in the page source at all - it is redacted on the way to the screen for non-administrators, and backend/smart_move_* is NOT touched; the Historical snapshot tab is now administrator-only (it was gated on a flag true for any full-access account); and blocker codes read in plain words for readers while administrators still see the codes. WATCHLIST: a bid of zero is not a bid - the provider returns a literal 0 out of hours and the page printed a zero price for bid, ask AND spread - and the quote age reads in hours instead of raw seconds. NEWS: a headline with no link is text, not an anchor that looks clickable and does nothing. THE ASSISTANT'S FOOTER no longer names a model or a data vendor. Also: the build now stubs the Figma preview folder, which is untracked - without it the frontend build cannot resolve its imports on the server. PLUS THE OWNER'S LIST OF 21 NOVEMBER. THE ORIGINAL LOGO IS BACK AT ITS REAL SIZE: it was never missing - the files are the same two the old interface uses, but they are 1386x480 wordmarks and the rail squeezed them into a 32x32 box, which rendered an 11-pixel sliver; they now draw 150px wide at their own shape, one per theme, with the word Tradgo.us beside them removed because the wordmark already says it, and the brand's own mark standing in when the rail is collapsed. STRATEGY TRADES GETS ITS VOICE ALERTS BACK, carried over from the old page rather than rewritten: a win or loss tone, then the alert spoken, with the ticker read out letter by letter; it announces a new trade and a close, never on the first reading, and it says Agent 2 and never a model name. REWARD-TO-RISK IS GONE from that page entirely - the tile, the column and the note that explained the column. PROFIT IS GREEN AND LOSS IS RED wherever a P&L appears, in the table and in the tiles: the cells always carried the mark but a table rule outranked it, so every figure took plain ink; a count or a win rate is not a P&L and stays as it was. THE DATE PICKER'S CALENDAR BUTTON had no rule at all, so the browser drew its black default, invisible on the dark theme. THE ASSISTANT'S TYPING BOX was black on dark in the light theme, because that panel is an older component that styles itself from the old palette; its surfaces now follow whichever theme the page is in. A BELL THAT RANG ON EVERY PAGE LOAD is fixed - it is meant to ring only for something that arrives while you are already looking. AND THE EARNINGS PAGE NO LONGER FAILS when a reply arrives without its list of cards, which took the whole page down behind the error screen. NEW_UI_USERS is not touched. No scoring, A.27 rule, order logic, or Smart Move engine, route or scanner change."
)


def main() -> int:
    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    # A blind o1 -> p1 would be wrong if the digraph ever appeared inside another word.
    stray = re.findall(r"[A-Za-z0-9]o1|o1[0-9]", text)
    if stray:
        print(f"refusing: 'o1' appears inside another token: {sorted(set(stray))}")
        return 2

    out = text.replace("o1", "p1").replace("O1", "P1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER
    out = "\n".join(lines)

    out = re.sub(r"\$p1Sha\s*=\s*'[0-9a-f]{40}'", f"$p1Sha    = '{SHA}'", out, count=1)
    out = re.sub(r"\$p1Anchor\s*=\s*'[0-9a-f]{64}'", f"$p1Anchor = '{ANCHOR}'", out, count=1)

    if SHA not in out or ANCHOR not in out:
        print("refusing: the pins were not written")
        return 2
    for old in ("388124301fa2a465cf247503e7681b4474013666",
                "646365670dd92dfaa2674bfebe48d6805866e6c14fa477eb03e37f4229edf595"):
        if old in out:
            print(f"refusing: kit O's pin {old[:12]} survived")
            return 2
    if "O1" in out or re.search(r"\$o1[A-Za-z]", out):
        print("refusing: a kit O identifier survived")
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
