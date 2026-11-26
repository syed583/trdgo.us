r"""One user's order log made to agree with the broker. DRY RUN unless --apply is given with the confirm word.

    cd C:\apps\us-stock-reader\backend
    .venv\Scripts\python.exe C:\SmartMoveRun\mt5_reconcile_user.py --login 250094
    .venv\Scripts\python.exe C:\SmartMoveRun\mt5_reconcile_user.py --login 250094 --apply --confirm YES-RECONCILE-250094

DRY RUN  reads our log, the broker's open positions and its order history (GETs only) and prints what WOULD
         be recorded. It writes nothing.
APPLY    for each log-open row whose ticket is NOT open at the broker, INSERTS one CLOSED row (the broker's
         own close price when its history has it, otherwise flagged PRICE_UNKNOWN). Nothing is updated or
         deleted, no order and no close is sent, and a position really open at the broker is left alone.
         It is the same routine the service itself runs (user_copy_service.reconcile_user).
"""
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", required=True); ap.add_argument("--apply", action="store_true"); ap.add_argument("--confirm", default="")
    a = ap.parse_args()
    sys.path.insert(0, os.getcwd())
    from dotenv import load_dotenv
    load_dotenv()
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT username FROM user_mt5_accounts WHERE login = :l"), {"l": str(a.login)}).all()
    finally:
        db.rollback(); db.close()
    if len(rows) != 1:
        print("accounts found for login %s: %d - stopping" % (a.login, len(rows))); return 2
    user = rows[0][0]
    import mt5_bridge as bridge
    import user_copy_service as CP
    import user_mt5_service as usvc
    import user_mt5_store as store
    token, err = usvc.session_for(user)
    if err:
        print("no session: %s - %s. Nothing was read or written." % (err.get("reason"), str(err.get("detail") or "")[:200])); return 3
    log_open = store.open_tickets(user)
    live = CP.live_tickets(user, token, fresh=True)
    if live is None:
        print("the broker's open positions could not be read. Nothing was written."); return 3
    gone = [r for r in log_open if CP._ticket_int(r) not in live]                    # noqa: SLF001
    print("account %s: log-open %d | really open at the broker %d | to settle %d" % (a.login, len(log_open), len(log_open) - len(gone), len(gone)))
    for r in log_open:
        tk = CP._ticket_int(r)                                                       # noqa: SLF001
        if tk in live:
            print("  KEEP    %-9s %-6s %-5s  really open at the broker - left alone" % (tk, r.get("symbol"), r.get("side"))); continue
        h = bridge.closed_order(tk, CP._fill_epoch(r), token=token, strict_token=True)   # noqa: SLF001
        if h.get("ok") and h.get("found"):
            print("  SETTLE  %-9s %-6s %-5s  broker closed it at %s on %s (%s), broker profit %s" % (
                tk, r.get("symbol"), r.get("side"), h.get("price"), h.get("time"), h.get("how"), h.get("profit")))
        else:
            print("  SETTLE  %-9s %-6s %-5s  not open at the broker; close price UNKNOWN (%s)" % (
                tk, r.get("symbol"), r.get("side"), "no such closed order in the history" if h.get("ok") else "history unreadable: %s" % h.get("reason")))
    want = "YES-RECONCILE-%s" % a.login
    if not a.apply:
        print("DRY RUN - nothing was written. To apply: --apply --confirm %s" % want); return 0
    if a.confirm != want:
        print("NOT APPLIED: --confirm must be exactly %s. Nothing was written." % want); return 2
    res = CP.reconcile_user(user, token)
    print("APPLIED: %s" % res)
    print("log-open now: %d" % len(store.open_tickets(user)))
    return 0 if res.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
