"""Read-only reports for the signal-verification audit.

    python signal_verification_cli.py report 2026-10-27 [2026-10-31]    audited signals: pass / fail and why (needs mode "audit")
    python signal_verification_cli.py history 2026-10-20 [2026-10-27]   re-check EXISTING paper trades with what history stored

Uses the application's own database settings (database.py confines a local run to the development twin tables and refuses
production unless APP_ENV=production). Never writes."""
import json
import sys
from datetime import date


def main(argv: list) -> int:
    if len(argv) < 3 or argv[1] not in ("report", "history"):
        print(__doc__)
        return 2
    import database
    import signal_verification as sv
    d0 = date.fromisoformat(argv[2])
    d1 = date.fromisoformat(argv[3]) if len(argv) > 3 else d0
    fn = sv.report if argv[1] == "report" else sv.history_report
    print(json.dumps(fn(database.SessionLocal, d0, d1), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
