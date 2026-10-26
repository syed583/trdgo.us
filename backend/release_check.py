"""Is this build (or this git revision) safe to run? A deploy or a rollback must never silently allow trade entries
without the mandatory 15-minute candle confirmation, the stale-data protection or the connection cap.

    python release_check.py                 check the working tree
    python release_check.py <git-rev> ...   check one or more revisions (e.g. the proposed rollback target)
    python release_check.py --identify      which commit is checked out HERE (run it on the server), its state, how it
                                            relates to local `main` / `origin/main`, and whether it passes

Exit code 0 = every checked build passes; 1 = at least one does not (it must NOT be deployed or rolled back to
while entries are open; see docs/release_and_rollback.md). Read-only: it reads files / git objects and never imports
or starts the application. Never assume that `main` or `origin/main` is what production runs: identify it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent

# (what it proves, file, regex, forbid)  -- forbid=True: the pattern must NOT be present
REQUIRED = [
    ("15-minute candle module present", "backend/intraday_15m.py", r"def confirmation\(", False),
    ("one entry gate for every path (entry_check)", "backend/intraday_15m.py", r"def entry_check\(", False),
    ("the gate refuses unless the reading is CONFIRMED", "backend/intraday_15m.py", r'if not c\.get\("confirmed"\)', False),
    ("the gate refuses outside the regular session", "backend/intraday_15m.py", r'phase != "rth"', False),
    ("no settings switch can disable the confirmation", "backend/tab_settings.py", r"require_confirmation", True),
    ("no environment override can disable the confirmation", "backend/intraday_15m.py", r"I_UNDERSTAND", True),
    ("boards demote an unconfirmed BUY/SELL to WAIT", "backend/intraday_safety.py", r"entry_check\(sym, sig, phase\)", False),
    ("tradable() asks the gate directly", "backend/intraday_safety.py", r"def tradable\([\s\S]*?entry_check\(", False),
    ("paper-trade tracker checks at the moment of opening", "backend/demo_trade_service.py",
     r"entry_check\([\s\S]{0,160}?execution=True", False),
    ("live engine checks before opening and again at the insert", "backend/intraday_signal_v2.py",
     r"entry_check\([\s\S]*?entry_check\([\s\S]{0,160}?execution=True", False),
    ("operator entry lock with database triggers", "backend/entry_lock.py", r"def install\(", False),
    ("stale-data gate present", "backend/intraday_safety.py", r"def gate\(", False),
    ("per-signal stale-data limits", "backend/intraday_feed.py", r"def effective_inputs\(", False),
    ("critical inputs missing -> WAIT", "backend/intraday_feed.py", r"missing_critical", False),
    ("entries unlock only after the RUNNING build is verified", "backend/entry_lock.py", r"release_tool\.unlock", False),
    ("the running build is verified over HTTP before unlock", "backend/release_tool.py", r"def verify_runtime", False),
    ("cut-over ends with entries locked", "backend/release_tool.py", r"set_locked\(True, f\"cutover", False),
    ("the build checks itself at boot", "backend/main.py", r"boot_self_check\(\)", False),
    ("public runtime safety route", "backend/main.py", r"def safety_public", False),
    ("the entry gate fails closed", "backend/intraday_15m.py", r"GATE_ERROR", False),
    ("every open live signal row is managed, not only the newest", "backend/intraday_signal_v2.py", r"rows = _open_rows[(]db, symbol, now[)]\s", False),
    ("a second open live signal for a symbol is refused", "backend/intraday_signal_v2.py", r"if _open_rows\(db, symbol, now\):", False),
    ("a cut-over validates every open signal before it proceeds", "backend/release_tool.py", r"signal_recovery\.assess\(engine, now=now\)", False),
    ("open paper positions block a cut-over", "backend/release_tool.py", r"open paper positions exist", False),
    ("a rollback needs an empty book", "backend/release_tool.py", r"set_locked\(True, reason, engine, for_rollback=True\)", False),
    ("the signal recovery validator exists", "backend/signal_recovery.py", r"def validate\(", False),
    ("stale open signals are never closed automatically","backend/intraday_signal_v2.py", r"def expire_stale\(apply: bool = False", False),
    ("paper positions are carried across dates", "backend/demo_trade_service.py", r'filter_by\(status="OPEN"\)\.order_by', False),
    ("a position is never closed merely because the date changed", "backend/demo_trade_service.py", r"t\.session_date < n\.date\(\)", True),
    ("paper exits are guarded against a double close", "backend/demo_trade_service.py", r'DemoTrade\.status == "OPEN"\)\.values', False),
    ("live signals are managed whichever session opened them", "backend/intraday_signal_v2.py", r'WHERE symbol=:s AND is_open=TRUE AND session_date>=:c ORDER BY id ASC', False),
    ("live exits are guarded against a double close", "backend/intraday_signal_v2.py", r"WHERE id=:id AND is_open=TRUE", False),
    ("exchange-holiday-aware close rule", "backend/market_calendar.py", r"def first_close_after", False),
    ("three-connection cap", "backend/unusualwhales_service.py", r"PROVIDER_CONCURRENCY\s*=\s*3", False),
    ("machine-wide connection slots", "backend/uw_slots.py", r"class MachineSlots", False),
    ("priority gate with adaptive brake", "backend/uw_priority.py", r"def brake\(", False),
]


def check(read: Callable[[str], Optional[str]]) -> dict:
    """Pure: ``read(path)`` returns a file's text (or None if absent)."""
    results = []
    for what, path, rx, forbid in REQUIRED:
        text = read(path)
        found = bool(text and re.search(rx, text))
        ok = (text is not None) and (not found if forbid else found)
        results.append({"check": what, "file": path, "ok": ok, "present": text is not None, "forbid": forbid})
    return {"ok": all(r["ok"] for r in results), "checks": results}


def read_tree(root: Path = ROOT) -> Callable[[str], Optional[str]]:
    def read(path: str) -> Optional[str]:
        p = root / path
        return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else None
    return read


def read_rev(rev: str, root: Path = ROOT) -> Callable[[str], Optional[str]]:
    def read(path: str) -> Optional[str]:
        out = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"], capture_output=True, text=True,
                             encoding="utf-8", errors="ignore")
        return out.stdout if out.returncode == 0 else None
    return read


def _git(*args: str, root: Path = ROOT) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def identify(root: Path = ROOT) -> dict:
    """The commit that is checked out in ``root`` and how it relates to the branches people assume it matches."""
    head = _git("rev-parse", "HEAD", root=root)
    info = {"head": head, "branch": _git("rev-parse", "--abbrev-ref", "HEAD", root=root),
            "subject": _git("log", "-1", "--format=%s", root=root),
            "uncommitted_changes": bool(_git("status", "--porcelain", root=root)),
            "passes_release_check": check(read_tree(root))["ok"], "relation": {}}
    for ref in ("main", "origin/main"):
        sha = _git("rev-parse", "--verify", "--quiet", ref, root=root)
        if not sha:
            info["relation"][ref] = None
            continue
        counts = _git("rev-list", "--left-right", "--count", f"{ref}...HEAD", root=root) or ""
        behind, _, ahead = counts.partition("\t")
        info["relation"][ref] = {"commit": sha, "same_commit": sha == head, "this_build_is_behind_by": behind or None,
                                 "this_build_is_ahead_by": ahead or None}
    return info


def main(argv: list[str]) -> int:
    if "--identify" in argv:
        info = identify()
        print(json.dumps(info, indent=1))
        return 0 if info["passes_release_check"] else 1
    targets = [a for a in argv if not a.startswith("--")] or ["WORKING-TREE"]
    report = {}
    ok = True
    for t in targets:
        r = check(read_tree() if t == "WORKING-TREE" else read_rev(t))
        report[t] = r
        ok = ok and r["ok"]
        failed = [c["check"] for c in r["checks"] if not c["ok"]]
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {t}" + ("" if r["ok"] else "  missing: " + "; ".join(failed)))
    if "--json" in argv:
        print(json.dumps(report, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
