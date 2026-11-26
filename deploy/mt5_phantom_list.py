"""READ-ONLY: positions our order log believes are open, for one user or for every user.

    cd C:\\apps\\us-stock-reader\\backend
    .venv\\Scripts\\python.exe C:\\SmartMoveRun\\mt5_phantom_list.py                 every user, counts only
    .venv\\Scripts\\python.exe C:\\SmartMoveRun\\mt5_phantom_list.py --login 250094  that user's rows, one per line
    .venv\\Scripts\\python.exe C:\\SmartMoveRun\\mt5_phantom_list.py --login 250094 --broker
                                                                                    ...and ask the broker about each ticket

A "log-open" position is a FILLED row with a ticket and no CLOSED row for the same signal. That is exactly
what the position ceiling counts today (user_mt5_store.open_tickets).

Without --broker: one read-only database session, SELECTs only, no broker call.

With --broker (needs a working gateway; use it only when the owner says so): for that ONE user it opens the
user's own session the way the service does, then makes two kinds of GET and nothing else:
    OpenedOrders                 the broker's list of positions really open in that account
    HistoryDealsByPositionId     how and when each missing ticket was closed (stop, target, manual)
It sends no order and no close, and prints no password and no session token.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", help="MT5 account number; omit for the all-users summary")
    ap.add_argument("--broker", action="store_true", help="also ask the broker (read-only GETs) - one user only")
    a = ap.parse_args()
    if a.broker and not a.login:
        print("--broker needs --login: the broker is only ever asked about one named account"); return 2

    sys.path.insert(0, os.getcwd())
    from sqlalchemy import text
    from database import SessionLocal

    db = SessionLocal()
    try:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
        except Exception:  # noqa: BLE001
            db.rollback()
        accts = [dict(r) for r in db.execute(text(
            "SELECT username, login, server, enabled, auto_copy, admin_paused, max_positions, status"
            " FROM user_mt5_accounts ORDER BY login")).mappings().all()]
        fills = [dict(r) for r in db.execute(text(
            "SELECT username, admin_trade_id, at, symbol, broker_symbol, side, shares, volume, price, ticket, strategy"
            " FROM user_mt5_orders WHERE event = 'FILLED' AND ticket IS NOT NULL ORDER BY at")).mappings().all()]
        closed = {(r[0], str(r[1])) for r in db.execute(text(
            "SELECT username, admin_trade_id FROM user_mt5_orders WHERE event = 'CLOSED'")).all()}
        tries = defaultdict(Counter)
        for r in db.execute(text(
                "SELECT username, admin_trade_id, event, COUNT(*) FROM user_mt5_orders"
                " WHERE event LIKE 'CLOSE_%' AND event <> 'CLOSED' GROUP BY username, admin_trade_id, event")).all():
            tries[(r[0], str(r[1]))][r[2]] += int(r[3])
        sw = {str(r[0]): (r[1], r[2], r[3]) for r in db.execute(text(
            "SELECT id, status, exit_time, exit_price FROM demo_trades")).all()}
    finally:
        db.rollback()
        db.close()

    log_open = defaultdict(list)
    for f in fills:
        if (f["username"], str(f["admin_trade_id"])) not in closed:
            log_open[f["username"]].append(f)

    print("=" * 96)
    print("LOG-OPEN POSITIONS PER ACCOUNT (read-only). 'phantom' = the software closed its own trade already.")
    print("%-10s %-8s %-9s %-7s %8s %9s %8s  %s" % ("login", "enabled", "auto", "paused", "ceiling", "log-open", "phantom", "last status"))
    for acc in accts:
        rows = log_open.get(acc["username"], [])
        ph = sum(1 for f in rows if sw.get(str(f["admin_trade_id"]), ("?",))[0] not in ("OPEN", "?"))
        print("%-10s %-8s %-9s %-7s %8s %9d %8d  %s" % (acc["login"], acc["enabled"], acc["auto_copy"], acc["admin_paused"],
                                                       acc["max_positions"], len(rows), ph, acc["status"]))
    print("accounts: %d   accounts with at least one phantom: %d" % (
        len(accts), sum(1 for acc in accts if any(sw.get(str(f["admin_trade_id"]), ("?",))[0] not in ("OPEN", "?")
                                                  for f in log_open.get(acc["username"], [])))))
    if not a.login:
        print("=" * 96); return 0

    mine = [acc for acc in accts if str(acc["login"]) == str(a.login)]
    if len(mine) != 1:
        print("accounts found for login %s: %d - stopping" % (a.login, len(mine))); return 2
    user = mine[0]["username"]; rows = log_open.get(user, [])
    print("-" * 96)
    print("ACCOUNT %s: %d log-open rows" % (a.login, len(rows)))
    print("%-10s %-6s %-11s %-5s %6s %9s  %-16s %-12s %-16s %s" % (
        "ticket", "symbol", "broker sym", "side", "shares", "fill", "filled (UTC)", "software", "sw exit (UTC)", "close tries"))
    for f in rows:
        st = sw.get(str(f["admin_trade_id"]), ("?", None, None))
        print("%-10s %-6s %-11s %-5s %6s %9s  %-16s %-12s %-16s %s" % (
            f["ticket"], f["symbol"], f["broker_symbol"], f["side"], f["shares"], f["price"], str(f["at"])[:16],
            st[0], str(st[1])[:16] if st[1] else "-", dict(tries.get((user, str(f["admin_trade_id"])), {}))))

    if not a.broker:
        print("=" * 96); return 0

    # ------------------------------------------------------------------ broker, read-only, this one user
    print("-" * 96)
    print("BROKER CHECK (read-only GETs: OpenedOrders, HistoryDealsByPositionId)")
    import mt5_bridge as bridge
    import user_mt5_service as usvc
    token, err = usvc.session_for(user)
    if err:
        print("no session for this user: %s - %s" % (err.get("reason"), str(err.get("detail") or "")[:200]))
        print("nothing was asked of the broker."); print("=" * 96); return 3
    live = bridge.positions(token=token, strict_token=True)
    if not live.get("ok"):
        print("OpenedOrders failed: %s - %s" % (live.get("reason"), str(live.get("detail") or "")[:200]))
        print("=" * 96); return 3
    open_now = {int(p["ticket"]): p for p in live["positions"] if p.get("ticket") is not None}
    print("positions REALLY open at the broker now: %d" % len(open_now))
    for t, p in sorted(open_now.items()):
        print("   %-10s %-12s %-5s vol %-6s open %-9s profit %-8s %s" % (t, p.get("symbol"), p.get("type"), p.get("volume"),
                                                                    p.get("price_open"), p.get("profit"), (p.get("comment") or "")[:30]))
    ours = {int(f["ticket"]) for f in rows}
    print("log-open tickets that ARE open at the broker: %s" % (sorted(ours & set(open_now)) or "none"))
    print("open at the broker but NOT in our log-open list: %s" % (sorted(set(open_now) - ours) or "none"))
    print("log-open tickets that are NOT open at the broker (phantoms), with the broker's own closing deal:")
    for f in rows:
        tk = int(f["ticket"])
        if tk in open_now:
            continue
        res = bridge._call("HistoryDealsByPositionId", {"ticket": tk}, token=token, strict_token=True)   # noqa: SLF001
        if not res.get("ok"):
            print("   %-10s %-6s history not available: %s %s" % (tk, f["symbol"], res.get("reason"), str(res.get("detail") or "")[:90]))
            continue
        data = res.get("data")
        deals = data if isinstance(data, list) else (bridge._g(data, "deals", "items") or [])                 # noqa: SLF001
        outs = []
        for d in deals or []:
            direction = bridge._g(d, "direction", "entry", "dealEntry")                                       # noqa: SLF001
            comment = str(bridge._g(d, "comment") or "")                                                      # noqa: SLF001
            if direction in (1, 2, 3, "1", "2", "3", "Out", "OUT", "InOut", "OutBy", "out") or "[sl" in comment.lower() \
                    or "[tp" in comment.lower():
                outs.append("%s @ %s profit %s (%s)" % (bridge._g(d, "openTime", "historyTime", "time"),      # noqa: SLF001
                                                        bridge._g(d, "price", "openPrice"), bridge._g(d, "profit"),  # noqa: SLF001
                                                        comment[:24]))
        print("   %-10s %-6s %s" % (tk, f["symbol"], "; ".join(outs) if outs else "no closing deal returned (%d deals)" % len(deals or [])))
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
