"""Operate the shared Unusual Whales budget from the command line.

    python uw_budget_cli.py usage 8123       record today's request count read from the PROVIDER DASHBOARD
    python uw_budget_cli.py pause-warmer     stop the historical warmer (live trading is unaffected)
    python uw_budget_cli.py resume-warmer
    python uw_budget_cli.py status           what has been recorded (live numbers: GET /api/uw/budget)

The Unusual Whales plan has UNLIMITED daily requests (dashboard + provider support, 2026-10-23), so with
`tab_settings.UW_BUDGET["plan_unlimited"]` set (the default) NO reading is needed: the warmer is not held
back for an unverified quota. `usage` is then informational (it feeds the usage figures on /api/uw/budget).
It is still required, and the warmer stays paused until it is recorded, only if `plan_unlimited` is switched
off for a plan that does have a daily cap. The provider day resets at 8 pm US Eastern.
Live intraday trading is never held up by quota uncertainty.

The reading is stored in the shared cache of THIS environment (production on the server, a separate
namespace on a development machine) and the running service picks it up within 30 seconds.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv: list[str]) -> int:
    import database  # noqa: F401  (loads .env so APP_ENV selects the right namespace)
    import db_cache
    import uw_budget
    cmd = argv[0] if argv else "status"
    ctl = uw_budget.Controller(persist=False)          # only for the day id
    day = ctl.snapshot()["day"]
    ns = db_cache.namespace() or "production"
    if cmd == "usage" and len(argv) >= 2:
        used = int(argv[1])
        db_cache.put_sync("uwbudget:dashboard", {"used": used, "at": time.time(), "day": day})
        print(f"recorded: {used} requests used on provider day {day} ({ns}). "
              f"The service applies it within 30 s; the warmer may run if its budget allows.")
        return 0
    if cmd in ("pause-warmer", "resume-warmer"):
        db_cache.put_sync("uwbudget:control", {"paused": cmd == "pause-warmer", "at": time.time()})
        print(f"warmer {'paused' if cmd == 'pause-warmer' else 'resumed'} ({ns}); effective within 30 s")
        return 0
    if cmd == "status":
        d = db_cache.get("uwbudget:dashboard")
        c = db_cache.get("uwbudget:control")
        print(f"environment: {ns}   provider day: {day}   next reset: "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ctl.next_reset_epoch()))} (local time)")
        print("dashboard reading:", (d[1] if d and d[1].get("day") == day else "none for today -> warmer stays paused"))
        print("warmer switch:", (c[1] if c else "running"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
