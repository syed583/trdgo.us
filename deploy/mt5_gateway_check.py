"""READ-ONLY: does the MT5 gateway answer with our licence key, and (optionally) can one account connect?

    cd C:\apps\us-stock-reader\backend
    .venv\Scripts\python.exe C:\SmartMoveRun\mt5_gateway_check.py                  Ping + Version, no trading account
    .venv\Scripts\python.exe C:\SmartMoveRun\mt5_gateway_check.py --login 250094   ...and open that user's own session

It sends no order. It prints the host, whether a key is configured (never the key), and what the gateway said.
"CloudTrial failed: Trial expired" appears here as problem=LICENCE if it is still happening.
"""
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--login"); a = ap.parse_args()
    sys.path.insert(0, os.getcwd())
    from dotenv import load_dotenv
    load_dotenv()
    import mt5_bridge as bridge
    h = bridge.gateway_health()
    print("host: %s   paid host configured: %s   key configured: %s" % (h["host"], h["paid_host_configured"], h["key_configured"]))
    print("Ping: %s" % str(h["ping"])[:160]); print("Version: %s" % str(h["version"])[:160])
    print("RESULT: %s%s" % ("GATEWAY OK" if h["ok"] else "GATEWAY PROBLEM", "" if h["ok"] else " (%s)" % (h["problem"] or "see above")))
    if not a.login or not h["ok"]:
        return 0 if h["ok"] else 3
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT username FROM user_mt5_accounts WHERE login = :l"), {"l": str(a.login)}).all()
    finally:
        db.rollback(); db.close()
    if len(rows) != 1:
        print("accounts found for login %s: %d" % (a.login, len(rows))); return 2
    import user_mt5_service as usvc
    token, err = usvc.session_for(rows[0][0])
    if err:
        kind = bridge.problem_kind(err)
        print("ACCOUNT %s: could not open a session: %s - %s%s" % (a.login, err.get("reason"), str(err.get("detail") or "")[:200],
                                                                  "   <-- gateway problem: %s" % kind if kind else ""))
        return 3
    acct = bridge.account(token=token)
    live = bridge.positions(token=token, strict_token=True)
    print("ACCOUNT %s: session opened; account summary %s; open positions read %s (%s open)" % (
        a.login, "OK" if acct.get("ok") else "FAILED: %s" % acct.get("detail"),
        "OK" if live.get("ok") else "FAILED: %s" % live.get("detail"), len(live.get("positions") or []) if live.get("ok") else "?"))
    print("RESULT: %s" % ("ACCOUNT OK - the trial error is gone" if acct.get("ok") and live.get("ok") else "ACCOUNT PROBLEM"))
    return 0 if acct.get("ok") and live.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
