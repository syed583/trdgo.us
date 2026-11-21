"""Derive the kit N installer (n1_run.ps1) from the kit M one.

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

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/m1/m1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/n1/n1_run.ps1")

SHA = "aefae0212a39a791a15f18dc404ccbc2504272f8"
ANCHOR = "62059b468468eb2bbd0b4936a2cbe071892cce3d24db5dc9164eddabc33f89cf"

HEADER = (
    "# TRDGO-US Stage N runner: Figma parity for the five framed pages, and the shell's own look "
    "for the rest. Analysis Intraday, Strategy Trades, Smart Money Move, News & Sentiment and "
    "Market Overview now land on their Figma frames' own coordinates - measured against the file "
    "through its MCP server, not matched by eye; worst difference across the five is 19px. Roboto "
    "400/500/700 is self-hosted (two woff2 subsets of one variable font), so pages no longer "
    "render in whatever the reader's system picked. Sparklines come from ONE shared server-cached "
    "payload, at most 22 chart requests per 300s window (~2% of the essential rail), and on Market "
    "Overview they are gated on the new UI: the classic shell draws none and makes no request, "
    "because that page is rendered by both shells. The Analysis landing's featured card gains its "
    "price chart with 1D/5D/1M tabs, plus Trend, RSI(14) and ATR(14) computed from daily bars the "
    "server already holds - no AI Score, which would need an analysis run on every visit to the "
    "busiest page. Smart Money Move gains Search and Export (CSV of the rows on screen); DISPLAY "
    "ONLY, no engine, collector, route or ledger file is touched. Pages with no Figma frame "
    "(Earnings Calendar, Post-Earnings AI, Overview, Options) get the shell's colours, radius, "
    "shadow and font by remapping the classic tokens inside .app-nu - not one layout rule changed. "
    "Also fixes a defect that spanned the whole shell: every PageHead title was invisible on dark "
    "pages. WHICH ACCOUNTS SEE IT is still NEW_UI_USERS in backend\\.env - this kit does not touch "
    "that line. No scoring, A.27 rule, order logic or Smart Move engine, route or scanner change."
)


def main() -> int:
    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    # A blind m1 -> n1 would be wrong if the digraph ever appeared inside another word.
    stray = re.findall(r"[A-Za-z0-9]m1|m1[0-9]", text)
    if stray:
        print(f"refusing: 'm1' appears inside another token: {sorted(set(stray))}")
        return 2

    out = text.replace("m1", "n1").replace("M1", "N1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER
    out = "\n".join(lines)

    out = re.sub(r"\$n1Sha\s*=\s*'[0-9a-f]{40}'", f"$n1Sha    = '{SHA}'", out, count=1)
    out = re.sub(r"\$n1Anchor\s*=\s*'[0-9a-f]{64}'", f"$n1Anchor = '{ANCHOR}'", out, count=1)

    if SHA not in out or ANCHOR not in out:
        print("refusing: the pins were not written")
        return 2
    for old in ("bf495623fa1aa1e33f39eb638e445863a8ddff8a",
                "3f1af8bb8eace58c6f85fb43c182d341445e0eb57684988545e3bcb325cce943"):
        if old in out:
            print(f"refusing: kit M's pin {old[:12]} survived")
            return 2
    if "M1" in out or re.search(r"\$m1[A-Za-z]", out):
        print("refusing: a kit M identifier survived")
        return 2

    DST.parent.mkdir(parents=True, exist_ok=True)
    # The runner is read and run on Windows; write CRLF, no BOM, as the previous letters were.
    io.open(DST, "w", encoding="utf-8", newline="\r\n").write(out)
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
