"""Derive the kit O installer (o1_run.ps1) from the kit N one.

The installer is deliberately kept OUT of the repository - it is the thing that changes production,
and it should be reviewed as a file on the operator's machine rather than imported from a checkout.
What lives here is how it was derived, so the next person can reproduce it instead of trusting a
blob: take the previous letter's runner, repoint the two pins, rename the letter, rewrite the
header.

    python -I deploy/make_n1.py

Reads   C:/Users/initi/SmartMoveRun/release_candidates/m1/m1_run.ps1
Writes  C:/Users/initi/SmartMoveRun/release_candidates/n1/n1_run.ps1

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

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/n1/n1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/o1/o1_run.ps1")

SHA = "388124301fa2a465cf247503e7681b4474013666"
ANCHOR = "646365670dd92dfaa2674bfebe48d6805866e6c14fa477eb03e37f4229edf595"

HEADER = (
    "# TRDGO-US Stage O runner: the first round of fixes after the new interface went live to every account. The Analysis Intraday STATISTICS ROW IS REMOVED - it carried the only data-vendor names anywhere in the product, and the other two boxes did not earn a row on their own; the index strip now runs straight into the panels below with no gap. The Smart Money Move ENGINE VERSION is administrator-only: fixing the live scanner's subtitle turned up a second copy in the historical snapshot view, gated on a flag that is NOT an administrator, so a normal full-access account could read it - both now depend on the real one, and so does the snapshot's model_version line. News & Sentiment PAGES ITS HEADLINES twenty at a time with the frame's own \"Showing 1-20 of 40\" and page buttons. The sidebar promotion is 150px instead of 213 and the navigation has a floor of its own, so three more menu rows stay visible. Wide tables now scroll INSIDE their card rather than past the right edge of the page (seen live on the Smart Money Move scanner, the Options Flow tape and the watchlist board). WATCHLIST is the new page: TradingView's official embeds for the delayed list and chart, with their attribution kept, and ONE /api/quote when a symbol is picked, for live bid and ask; its search is restricted to US common stock and ADRs by a set built from the screener ONCE A DAY (at most 16 requests per 24h, shared), so typing costs no provider request. AND A THIN MARKET PULSE CAN NO LONGER REPLACE A GOOD ONE: the kit N restart emptied the quote memo, the next build priced 6 of 82 symbols, and that was cached over a complete reading - which is why the index strip went blank while Market Overview stayed full. The builder now keeps whichever reading shows more, marked with the time it was taken. No extra provider request, no startup warm. NEW_UI_USERS is not touched. No scoring, A.27 rule, order logic or Smart Move engine, route or scanner change."
)


def main() -> int:
    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    # A blind m1 -> n1 would be wrong if the digraph ever appeared inside another word.
    stray = re.findall(r"[A-Za-z0-9]n1|n1[0-9]", text)
    if stray:
        print(f"refusing: 'm1' appears inside another token: {sorted(set(stray))}")
        return 2

    out = text.replace("n1", "o1").replace("N1", "O1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER
    out = "\n".join(lines)

    out = re.sub(r"\$o1Sha\s*=\s*'[0-9a-f]{40}'", f"$o1Sha    = '{SHA}'", out, count=1)
    out = re.sub(r"\$o1Anchor\s*=\s*'[0-9a-f]{64}'", f"$o1Anchor = '{ANCHOR}'", out, count=1)

    if SHA not in out or ANCHOR not in out:
        print("refusing: the pins were not written")
        return 2
    for old in ("aefae0212a39a791a15f18dc404ccbc2504272f8",
                "62059b468468eb2bbd0b4936a2cbe071892cce3d24db5dc9164eddabc33f89cf"):
        if old in out:
            print(f"refusing: kit N's pin {old[:12]} survived")
            return 2
    if "N1" in out or re.search(r"\$n1[A-Za-z]", out):
        print("refusing: a kit N identifier survived")
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
