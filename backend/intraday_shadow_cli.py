"""Read-only helpers for shadow mode.

    python intraday_shadow_cli.py plan                      what the additive schema change would do (changes nothing)
    python intraday_shadow_cli.py report 2026-10-27 [2026-10-31]   Legacy vs V2 shadow comparison for the logged days

Uses the application's own database settings (a local run is confined to the development twin tables; database.py refuses
production unless APP_ENV=production). Never writes."""
import json
import sys
from datetime import date


def main(argv: list) -> int:
    if len(argv) < 2 or argv[1] not in ("plan", "report"):
        print(__doc__)
        return 2
    import database
    if argv[1] == "plan":
        import intraday_workflow as wf
        print(json.dumps(wf.migration_plan(database.engine), indent=2, default=str))
        return 0
    import intraday_shadow as sh
    d0 = date.fromisoformat(argv[2])
    d1 = date.fromisoformat(argv[3]) if len(argv) > 3 else d0
    print(json.dumps(sh.report(database.SessionLocal, d0, d1), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
