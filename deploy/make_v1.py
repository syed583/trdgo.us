"""Derive the kit V installer (v1_run.ps1) from the kit U one.

    python deploy/make_v1.py

Reads   C:/Users/initi/SmartMoveRun/release_candidates/u1/u1_run.ps1
Writes  C:/Users/initi/SmartMoveRun/release_candidates/v1/v1_run.ps1

Written fresh, like make_t1.py, rather than by text surgery on the previous generator.

THE LETTER BUMP HAZARD, guarded: the pair "u1" appearing inside another token (a word ending in "u",
a hex string, a variable). Checked in both cases before anything is written; the script refuses
rather than guesses. (".ps1" does not contain "u1", so the extension needs no masking.)

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

SRC = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/u1/u1_run.ps1")
DST = Path(r"C:/Users/initi/SmartMoveRun/release_candidates/v1/v1_run.ps1")

SHA = "394db67932c1862989832b88c53cae9c21cbaa49"
ANCHOR = "93af239ff3b9aaf11249b820c2546d0a68fd6109875464bb4c2d76bb80ef8d47"

# kit U's pins: if either survives, the letter bump did not do its job
PREVIOUS_PINS = ("5a9a16c8e7a429e96801793b3de32d31c491ff2a",
                 "df9089b1c5717ad8c20c69f05333129984e2e485ba4ce9f96a7e42146514d927")

HEADER = (
    "# TRDGO-US Stage V runner: the MT5 mirror. Found by a read-only audit on 2026-11-26: one user's account took 109 of 575 signals in five days, because 20 positions the BROKER had already closed (its own stop or target) stayed open in OUR log for ever and filled a ceiling of 20; and from 25 November the trading gateway's trial had expired, so nothing could be placed or closed at all. "
    "ONE - THE GATEWAY: every call, the operator's and every user's, goes to the paid host with the licence key in one HTTP header. Both come from backend\\.env (MT5_GATEWAY_URL, MT5_GATEWAY_KEY) and from nowhere else; the key is never written to a URL, a result, a log or this package. WITHOUT THOSE TWO LINES IN .env THIS KIT BEHAVES AS BEFORE AND THE TRIAL ERROR CONTINUES. A licence or gateway failure now raises an operator alert in mt5.log (and to MT5_ALERT_WEBHOOK_URL if set) and a banner on My Broker; it is no longer silent. "
    "TWO - PHANTOM POSITIONS: a close that finds the ticket gone is checked against the broker's own position list and order history and recorded CLOSED, with the broker's price when its history has one and flagged price-unknown when it does not. It is never left open again. Reconciliation is read-only at the broker, inserts rows and deletes none, runs before the position limit decides, every five minutes while trading and before the end-of-day sweep, and never judges a position filled in the last two minutes. "
    "THREE - the position limit counts only positions really open at the broker and fails closed if the broker cannot be read. FOUR - not enough margin is decided from the broker's own free margin and required margin before sending, recorded in plain words, and needs no reset. FIVE - one close per ticket at a time; one worker per user with exits before entries, so one account's slow broker delays only itself; every order row records how long each step took. SIX - a connected account that is not trading gets each missed entry in its own log with the reason. "
    "SEVEN - My Broker: position limit 20 / 50 / 100 / custom, daily loss limit as presets, custom or OFF with a warning, and the connection banner. Reasons are shown in plain words with no supplier's name. "
    "NOT IN THIS KIT: the per-user execution report (per-trade table, day and week summary, CSV). "
    "BACKEND: mt5_bridge.py, user_copy_service.py, user_mt5_store.py, user_mt5_routes.py changed; user_mt5_plain.py new. The operator's own engine (mt5_autotrade.py), the paper book, A.27 and every Smart Move file are untouched. NEW_UI_USERS is not touched."
)


def main() -> int:
    if set(SHA) == {"0"} or set(ANCHOR) == {"0"}:
        print("refusing: SHA / ANCHOR are still placeholders - set them from build_package.py's output")
        return 2

    text = io.open(SRC, encoding="utf-8-sig", newline="").read()

    stray = re.findall(r"[A-Za-z0-9]u1|u1[0-9]|[A-Za-z0-9]U1|U1[0-9]", text)
    if stray:
        print(f"refusing: the letter pair appears inside another token: {sorted(set(stray))}")
        return 2

    out = text.replace("u1", "v1").replace("U1", "V1")

    lines = out.split("\n")
    assert lines[0].startswith("# TRDGO-US Stage"), "first line is not the header"
    lines[0] = HEADER + ("\r" if lines[0].endswith("\r") else "")
    out = "\n".join(lines)

    out, n1 = re.subn(r"\$v1Sha\s*=\s*'[0-9a-f]{40}'", f"$v1Sha    = '{SHA}'", out, count=1)
    out, n2 = re.subn(r"\$v1Anchor\s*=\s*'[0-9a-f]{64}'", f"$v1Anchor = '{ANCHOR}'", out, count=1)
    if n1 != 1 or n2 != 1:
        print(f"refusing: the pins were not written (sha {n1}, anchor {n2})")
        return 2
    for old in PREVIOUS_PINS:
        if old in out:
            print(f"refusing: kit U's pin {old[:12]} survived")
            return 2
    if "U1" in out or re.search(r"\$u1[A-Za-z]", out) or "u1_run" in out or "_u1_" in out:
        print("refusing: a kit U identifier survived")
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
