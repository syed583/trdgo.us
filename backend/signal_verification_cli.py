"""Read-only reports for the signal-verification audit.

    python signal_verification_cli.py report 2026-10-27 [2026-10-31]    audited signals: pass / fail and why (needs mode "audit")
    python signal_verification_cli.py history 2026-10-20 [2026-10-27]   re-check EXISTING paper trades with what history stored
    python signal_verification_cli.py stats <logfile>                   the audit's own health (verify / write errors) from the app log

Uses the application's own database settings (database.py confines a local run to the development twin tables and refuses
production unless APP_ENV=production). Never writes."""
import json
import re
import sys
from datetime import date


def stats_from_log(path: str) -> dict:
    """Aggregate the SIGNAL_AUDIT_STATS lines: the counters are cumulative per process, so take the last line of each run (a counter
    that goes down marks a restart)."""
    runs, cur = [], None
    for line in open(path, encoding="utf-8", errors="ignore"):
        m = re.search(r"SIGNAL_AUDIT_STATS (\{.*\})", line)
        if not m:
            continue
        st = json.loads(m.group(1))
        if cur is not None and st.get("cycles", 0) < cur.get("cycles", 0):
            runs.append(cur)
        cur = st
    if cur is not None:
        runs.append(cur)
    total = {}
    for r in runs:
        for k, v in r.items():
            total[k] = total.get(k, 0) + v
    return {"process_runs": len(runs), "total": total}


def main(argv: list) -> int:
    if len(argv) < 3 or argv[1] not in ("report", "history", "stats"):
        print(__doc__)
        return 2
    if argv[1] == "stats":
        print(json.dumps(stats_from_log(argv[2]), indent=2))
        return 0
    import database
    import signal_verification as sv
    d0 = date.fromisoformat(argv[2])
    d1 = date.fromisoformat(argv[3]) if len(argv) > 3 else d0
    fn = sv.report if argv[1] == "report" else sv.history_report
    print(json.dumps(fn(database.SessionLocal, d0, d1), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
