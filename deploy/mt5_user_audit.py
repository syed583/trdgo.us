"""READ-ONLY audit: one user's MT5 mirror against the software's own trades.

    cd C:\\apps\\us-stock-reader\\backend
    .venv\\Scripts\\python.exe C:\\SmartMoveRun\\mt5_user_audit.py --login 250094 --days 5

What it does
    Opens ONE database session, sets the transaction READ ONLY, runs four SELECTs, closes it.
    It calls no broker, places nothing, changes nothing, and never selects the stored password.
    It prints a report and writes the per-signal detail to a CSV beside this script.

What it reads
    user_mt5_accounts   the user's settings (never the `secret` column)
    user_symbol_map     which of our symbols the user has mapped to their broker
    demo_trades         the software's signals for the last N trading days
    user_mt5_orders     what was done in this user's account for each of them

How a signal is matched to an order
    user_mt5_orders.admin_trade_id = demo_trades.id. Entry outcome = the FILLED / SKIPPED / REJECTED /
    ERROR row for that id; close outcome = the CLOSED / CLOSE_FAIL / CLOSE_UNCONFIRMED row(s).
    NO ROW AT ALL means the user was not in the copying set when the signal fired (auto-trade off,
    paused, not consented) or the copy worker was not running: nothing is logged in those cases.

Money is compared at the USER'S share count: software P&L per share x the shares the user trades.
The software's own book is sized by notional, so its raw dollar figure is not comparable.

Not available from the database, and said so in the report: commission and swap charged by the
broker, and the fate of positions the broker closed itself (the order carries a stop and a target).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def f2(v, nd=2):
    return "" if v is None else ("%." + str(nd) + "f") % float(v)


def utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):                      # a driver that hands timestamps back as text
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def secs(a, b):
    a, b = utc(a), utc(b)
    return None if a is None or b is None else (a - b).total_seconds()


def reason_class(event, reason):
    """Who owns a missed entry: the USER's settings, OUR software, the BROKER, or a RULE working as designed."""
    r = (reason or "").upper()
    if event == "NONE":
        return "NOT_IN_COPYING_SET", "USER/OURS (auto-trade off, paused, or the copy worker was not running - not logged which)"
    if event == "ERROR":
        return "ERROR", "OURS"
    if event == "REJECTED":
        return "REJECTED", "BROKER"
    if r.startswith("NOT_MAPPED"):
        return "NOT_MAPPED", "USER (symbol not mapped)"
    if r.startswith("PROVIDER_UNAVAILABLE"):
        return "PROVIDER_UNAVAILABLE", "OURS (MT5 gateway unavailable)"
    if r.startswith("NO_MARGIN"):
        return "NO_MARGIN", "USER (no equity)"
    if r.startswith("SKIPPED_SIZE"):
        return "SKIPPED_SIZE", "BROKER GRID (share count does not fit the broker's lot step)"
    if r.startswith("MAX_OPEN_POSITIONS"):
        return "MAX_OPEN_POSITIONS", "RULE (user's position ceiling)"
    if r.startswith("DAILY_LOSS_LIMIT"):
        return "DAILY_LOSS_LIMIT", "RULE (user's daily loss limit)"
    if r.startswith("EQUITY_UNKNOWN"):
        return "EQUITY_UNKNOWN", "OURS/BROKER (equity could not be read)"
    if "STOP" in r and "CAP" in r:
        return "STOP_CAP", "RULE (2.5% stop cap)"
    head = (reason or "").split(":")[0].strip()[:40] or "UNKNOWN"
    return head, "SESSION/OTHER (see reason text)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", required=True, help="the MT5 account number, e.g. 250094")
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from sqlalchemy import text
    from database import SessionLocal

    db = SessionLocal()
    try:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
        except Exception:  # noqa: BLE001 - not Postgres; the statements below are SELECTs either way
            db.rollback()
        acct = db.execute(text(
            "SELECT username, login, server, consent_at, enabled, auto_copy, admin_paused, max_shares, max_positions,"
            " daily_loss_pct, status, last_error, checked_at, created_at, updated_at"
            " FROM user_mt5_accounts WHERE login = :l"), {"l": str(a.login)}).mappings().all()
        if len(acct) != 1:
            print("accounts found for login %s: %d - stopping (expected exactly one)" % (a.login, len(acct)))
            return 2
        acct = dict(acct[0]); user = acct["username"]
        mapped = [dict(r) for r in db.execute(text(
            "SELECT symbol, broker_symbol, contract_size, volume_min, volume_step, created_at"
            " FROM user_symbol_map WHERE username = :u ORDER BY symbol"), {"u": user}).mappings().all()]
        days = [r[0] for r in db.execute(text(
            "SELECT DISTINCT session_date FROM demo_trades ORDER BY session_date DESC LIMIT :n"),
            {"n": max(1, min(a.days, 30))}).all()]
        if not days:
            print("no software trades found"); return 2
        first = min(days)
        trades = [dict(r) for r in db.execute(text(
            "SELECT id, strategy, symbol, side, shares, session_date, entry_time, entry_price, stop, target, status,"
            " exit_time, exit_price, profit FROM demo_trades WHERE session_date >= :d ORDER BY entry_time"),
            {"d": first}).mappings().all()]
        orders = [dict(r) for r in db.execute(text(
            "SELECT id, at, event, symbol, broker_symbol, side, shares, volume, price, admin_price, slippage, pnl,"
            " ticket, admin_trade_id, strategy, reason FROM user_mt5_orders WHERE username = :u AND at >= :d"
            " ORDER BY at"), {"u": user, "d": first}).mappings().all()]
        first_order = db.execute(text("SELECT MIN(at), COUNT(*) FROM user_mt5_orders WHERE username = :u"),
                                 {"u": user}).first()
        # --- the position ceiling: what OUR LOG believes is open (this is what MAX_OPEN_POSITIONS counts)
        fills_all = [dict(r) for r in db.execute(text(
            "SELECT admin_trade_id, at, symbol, side, ticket FROM user_mt5_orders"
            " WHERE username = :u AND event = 'FILLED' AND ticket IS NOT NULL ORDER BY at"), {"u": user}).mappings().all()]
        closed_all = [dict(r) for r in db.execute(text(
            "SELECT admin_trade_id, at FROM user_mt5_orders WHERE username = :u AND event = 'CLOSED'"),
            {"u": user}).mappings().all()]
        fail_reasons = [dict(r) for r in db.execute(text(
            "SELECT event, SUBSTR(reason, 1, 110) AS why, COUNT(*) AS n, COUNT(DISTINCT admin_trade_id) AS trades,"
            " MIN(at) AS first_at, MAX(at) AS last_at FROM user_mt5_orders"
            " WHERE username = :u AND event IN ('CLOSE_FAIL', 'CLOSE_UNCONFIRMED', 'CLOSE_UNCONFIRME', 'ERROR')"
            " GROUP BY event, SUBSTR(reason, 1, 110) ORDER BY COUNT(*) DESC LIMIT 12"), {"u": user}).mappings().all()]
        skip_reasons = [dict(r) for r in db.execute(text(
            "SELECT SUBSTR(reason, 1, 150) AS why, COUNT(*) AS n, MIN(at) AS first_at, MAX(at) AS last_at"
            " FROM user_mt5_orders WHERE username = :u AND event = 'SKIPPED' AND at >= :d"
            " AND reason NOT LIKE 'MAX_OPEN_POSITIONS%' AND reason NOT LIKE 'DAILY_LOSS_LIMIT%'"
            " GROUP BY SUBSTR(reason, 1, 150) ORDER BY COUNT(*) DESC LIMIT 8"), {"u": user, "d": first}).mappings().all()]
        sw_open_now = db.execute(text("SELECT COUNT(*) FROM demo_trades WHERE status = 'OPEN'")).scalar()
        sw_status = {str(r[0]): r[1] for r in db.execute(text(
            "SELECT id, status FROM demo_trades WHERE id IN (SELECT CAST(admin_trade_id AS INTEGER) FROM user_mt5_orders"
            " WHERE username = :u AND event = 'FILLED' AND admin_trade_id IS NOT NULL)"), {"u": user}).all()}
    finally:
        db.rollback()
        db.close()

    by_tid = defaultdict(list)
    for o in orders:
        by_tid[str(o.get("admin_trade_id"))].append(o)
    mapped_syms = {m["symbol"] for m in mapped}
    user_shares = int(acct.get("max_shares") or 10)

    print("=" * 100)
    print("MT5 MIRROR AUDIT (read-only)   account %s on %s" % (acct["login"], acct["server"]))
    print("user ..%s   window: %s to %s (%d trading days)" % (str(user)[-4:], first, max(days), len(days)))
    print("settings NOW: enabled=%s auto_copy=%s admin_paused=%s shares=%s max_positions=%s daily_loss_pct=%s"
          % (acct["enabled"], acct["auto_copy"], acct["admin_paused"], acct["max_shares"], acct["max_positions"],
             acct["daily_loss_pct"]))
    print("last check: status=%s at %s   last_error=%s" % (acct["status"], acct["checked_at"],
                                                         (acct["last_error"] or "")[:160]))
    print("connected %s, settings last changed %s; first order ever %s; orders ever %s"
          % (acct["created_at"], acct["updated_at"], first_order[0], first_order[1]))
    print("symbols mapped NOW: %d -> %s" % (len(mapped), ", ".join(sorted(mapped_syms)) or "(none)"))
    print("software signals in window: %d   order-log rows in window: %d" % (len(trades), len(orders)))
    print("order-log events: %s" % dict(Counter(o["event"] for o in orders)))

    rows = []
    tot = defaultdict(float); cnt = Counter(); owners = Counter(); miss_money = defaultdict(float)
    rej_reasons = Counter(); close_kinds = Counter()
    for t in trades:
        sgn = 1 if str(t["side"]).upper() in ("LONG", "BUY") else -1
        sw_ps = None
        if t.get("exit_price") is not None and t.get("entry_price") is not None:
            sw_ps = (float(t["exit_price"]) - float(t["entry_price"])) * sgn
        os_ = by_tid.get(str(t["id"]), [])
        entry = next((o for o in os_ if o["event"] in ("FILLED", "SKIPPED", "REJECTED", "ERROR")), None)
        closed = next((o for o in os_ if o["event"] == "CLOSED"), None)
        fails = [o for o in os_ if o["event"] in ("CLOSE_FAIL", "CLOSE_UNCONFIRMED")]
        ev = entry["event"] if entry else "NONE"
        shares = int((entry or {}).get("shares") or user_shares)
        sw_user = None if sw_ps is None else sw_ps * shares
        cnt["signals"] += 1
        if sw_user is not None:
            tot["software_all"] += sw_user; cnt["signals_closed_in_software"] += 1
        r = {"trade_id": t["id"], "day": t["session_date"], "agent": t["strategy"], "symbol": t["symbol"], "side": t["side"],
             "signal_time_utc": utc(t["entry_time"]), "sw_entry": t["entry_price"], "sw_exit": t.get("exit_price"),
             "sw_exit_time_utc": utc(t.get("exit_time")), "sw_status": t["status"], "sw_pnl_per_share": sw_ps,
             "user_shares": shares, "sw_pnl_at_user_size": sw_user, "entry_event": ev,
             "entry_reason": (entry or {}).get("reason"), "mapped_now": t["symbol"] in mapped_syms}
        if ev != "FILLED":
            klass, owner = reason_class(ev, (entry or {}).get("reason"))
            r["class"] = klass; r["owner"] = owner
            cnt["not_filled"] += 1; owners[(klass, owner)] += 1
            if sw_user is not None:
                bucket = "rejected" if ev == "REJECTED" else "missed"
                tot[bucket] += sw_user; miss_money[klass] += sw_user
            if ev == "REJECTED":
                rej_reasons[((entry or {}).get("reason") or "")[:110]] += 1
            rows.append(r); continue

        cnt["filled"] += 1
        fill = entry.get("price"); delay = secs(entry.get("at"), t["entry_time"])
        e_diff = None if fill is None else (float(fill) - float(t["entry_price"])) * sgn      # + = worse for the user
        e_exec = entry.get("slippage")                                                         # fill vs broker quote just before
        r.update({"mt5_fill": fill, "fill_logged_utc": utc(entry.get("at")), "entry_delay_s": delay,
                  "entry_diff_per_share": e_diff, "entry_exec_slip_per_share": e_exec,
                  "entry_quote_gap_per_share": None if (e_diff is None or e_exec is None) else e_diff - float(e_exec),
                  "volume_sent": entry.get("volume"), "broker_symbol": entry.get("broker_symbol")})
        if delay is not None:
            tot["entry_delay_sum"] += delay; cnt["entry_delay_n"] += 1
        if sw_user is not None:
            tot["software_filled_set"] += sw_user
        if closed:
            cfill = closed.get("price"); cdelay = secs(closed.get("at"), t.get("exit_time"))
            x_diff = None
            if cfill is not None and t.get("exit_price") is not None:
                x_diff = (float(t["exit_price"]) - float(cfill)) * sgn                         # + = worse for the user
            mt5_pnl = closed.get("pnl")
            r.update({"mt5_close": cfill, "close_logged_utc": utc(closed.get("at")), "close_delay_s": cdelay,
                      "close_reason": closed.get("reason"), "exit_diff_per_share": x_diff,
                      "exit_exec_slip_per_share": closed.get("slippage"), "mt5_pnl": mt5_pnl,
                      "close_kind": "CLOSED"})
            close_kinds["CLOSED"] += 1
            if cdelay is not None:
                tot["close_delay_sum"] += cdelay; cnt["close_delay_n"] += 1
                if cdelay > 60:
                    cnt["closed_late_over_60s"] += 1
            if mt5_pnl is not None:
                tot["mt5_closed"] += float(mt5_pnl); cnt["mt5_closed_n"] += 1
            if sw_user is not None:
                tot["software_closed_set"] += sw_user
            if e_diff is not None:
                tot["entry_diff"] += e_diff * shares
                if e_exec is not None:
                    tot["entry_exec"] += float(e_exec) * shares; tot["entry_gap"] += (e_diff - float(e_exec)) * shares
            if x_diff is not None:
                tot["exit_diff"] += x_diff * shares
                xs = closed.get("slippage")
                if xs is not None:
                    tot["exit_exec"] += float(xs) * shares; tot["exit_gap"] += (x_diff - float(xs)) * shares
        else:
            kind = fails[-1]["event"] if fails else ("SOFTWARE_STILL_OPEN" if t["status"] == "OPEN" else "NO_CLOSE_RECORD")
            r.update({"close_kind": kind, "close_reason": (fails[-1].get("reason") if fails else None)})
            close_kinds[kind] += 1
            if sw_user is not None:
                tot["software_unclosed_set"] += sw_user
        rows.append(r)

    orphan = [o for o in orders if str(o.get("admin_trade_id")) not in {str(t["id"]) for t in trades}]

    # ---------------------------------------------------------------- the ceiling, reconstructed from the log
    closed_at = {}
    for c in closed_all:
        k = str(c["admin_trade_id"]); ca = utc(c["at"])
        if k not in closed_at or (ca and ca < closed_at[k]):
            closed_at[k] = ca
    log_open = [f for f in fills_all if str(f["admin_trade_id"]) not in closed_at]
    print("-" * 100)
    print("0. THE POSITION CEILING (max_positions=%s). It counts every FILLED row in OUR LOG with no CLOSED row."
          % acct["max_positions"])
    print("   our log believes OPEN right now: %d positions   (the software itself has %s open right now)"
          % (len(log_open), sw_open_now))
    stale = [f for f in log_open if sw_status.get(str(f["admin_trade_id"])) not in (None, "OPEN")]
    print("   of those, the software closed its own trade long ago (PHANTOMS in our log): %d" % len(stale))
    by_day = Counter(str(utc(f["at"]))[:10] for f in log_open)
    print("   log-open positions by the day they were filled: %s" % dict(sorted(by_day.items())))
    for d in sorted(days):
        carried = 0
        for f in fills_all:
            fa = utc(f["at"]); ca = closed_at.get(str(f["admin_trade_id"]))
            if fa is not None and str(fa)[:10] < str(d) and (ca is None or str(ca)[:10] >= str(d)):
                carried += 1
        print("   %s  log-open positions carried in from EARLIER days: %d of a ceiling of %s"
              % (d, carried, acct["max_positions"]))
    print("   why closes did not complete (all time, grouped): event | rows | distinct trades | first .. last | reason")
    for r in fail_reasons:
        print("   %-17s %5d %4d  %s .. %s  %s" % (r["event"], r["n"], r["trades"], str(r["first_at"])[:16],
                                                 str(r["last_at"])[:16], r["why"]))
    print("   other reasons entries were skipped in the window: rows | first .. last | reason")
    for r in skip_reasons:
        print("   %5d  %s .. %s  %s" % (r["n"], str(r["first_at"])[:16], str(r["last_at"])[:16], r["why"]))
    print("-" * 100)
    print("1. SENT OR NOT")
    print("   signals %d | filled %d | not filled %d" % (cnt["signals"], cnt["filled"], cnt["not_filled"]))
    for (klass, owner), n in owners.most_common():
        print("   %4d  %-26s  %-70s  software P&L forgone at user size: $%s" % (n, klass, owner, f2(miss_money[klass])))
    print("2. REJECTED BY THE BROKER: %d" % sum(rej_reasons.values()))
    for why, n in rej_reasons.most_common(8):
        print("   %4d  %s" % (n, why))
    print("3/4. FILLED TRADES: timing and price")
    if cnt["entry_delay_n"]:
        d = sorted(r["entry_delay_s"] for r in rows if r.get("entry_delay_s") is not None)
        print("   entry delay s (order logged - signal): avg %.1f | median %.1f | p90 %.1f | max %.1f"
              % (tot["entry_delay_sum"] / cnt["entry_delay_n"], d[len(d) // 2], d[min(len(d) - 1, int(len(d) * 0.9))], d[-1]))
    if cnt["close_delay_n"]:
        d = sorted(r["close_delay_s"] for r in rows if r.get("close_delay_s") is not None)
        print("   close delay s (close logged - software exit): avg %.1f | median %.1f | p90 %.1f | max %.1f | over 60 s: %d"
              % (tot["close_delay_sum"] / cnt["close_delay_n"], d[len(d) // 2], d[min(len(d) - 1, int(len(d) * 0.9))], d[-1],
                 cnt["closed_late_over_60s"]))
    print("5. SIZE: user setting now %d shares; shares on filled orders: %s"
          % (user_shares, dict(Counter(r["user_shares"] for r in rows if r["entry_event"] == "FILLED"))))
    print("6. CLOSE of filled trades: %s" % dict(close_kinds))
    print("   order-log rows whose signal is outside the window or unknown: %d" % len(orphan))
    print("-" * 100)
    print("P&L BRIDGE, dollars, everything at the user's share count")
    print("   A  software, ALL signals closed in the window ............ %10s   (%d signals)"
          % (f2(tot["software_all"]), cnt["signals_closed_in_software"]))
    print("   B  minus signals never sent (missed) ..................... %10s" % f2(-tot["missed"]))
    print("   C  minus signals the broker rejected ..................... %10s" % f2(-tot["rejected"]))
    print("   D  = software result on the signals that WERE filled ..... %10s" % f2(tot["software_filled_set"]))
    print("   E  minus filled trades with no recorded close ............ %10s   (%s)"
          % (f2(-tot["software_unclosed_set"]), dict((k, v) for k, v in close_kinds.items() if k != "CLOSED")))
    print("   F  = software result on trades filled AND closed here .... %10s   (%d trades)"
          % (f2(tot["software_closed_set"]), cnt["mt5_closed_n"]))
    print("   G  entry price worse than the software's ................. %10s" % f2(-tot["entry_diff"]))
    print("        of which fill vs the broker's quote at send (market)  %10s" % f2(-tot["entry_exec"]))
    print("        of which broker quote vs software price (delay+feed)  %10s" % f2(-tot["entry_gap"]))
    print("   H  exit price worse than the software's .................. %10s" % f2(-tot["exit_diff"]))
    print("        of which fill vs the broker's quote at send (market)  %10s" % f2(-tot["exit_exec"]))
    print("        of which broker quote vs software price (delay+feed)  %10s" % f2(-tot["exit_gap"]))
    print("   I  = F + G + H (should equal J) .......................... %10s"
          % f2(tot["software_closed_set"] - tot["entry_diff"] - tot["exit_diff"]))
    print("   J  MT5 result recorded in our log (price only) ........... %10s" % f2(tot["mt5_closed"]))
    print("   NOT IN THIS DATA: broker commission / swap, and any position the broker closed by its own stop or target.")
    print("-" * 100)

    out = os.path.join(HERE, "mt5_user_audit_%s.csv" % a.login)
    cols = ["trade_id", "day", "agent", "symbol", "side", "signal_time_utc", "sw_entry", "sw_exit", "sw_exit_time_utc",
            "sw_status", "sw_pnl_per_share", "user_shares", "sw_pnl_at_user_size", "entry_event", "class", "owner",
            "entry_reason", "mapped_now", "broker_symbol", "volume_sent", "mt5_fill", "fill_logged_utc", "entry_delay_s",
            "entry_diff_per_share", "entry_exec_slip_per_share", "entry_quote_gap_per_share", "close_kind", "close_reason",
            "mt5_close", "close_logged_utc", "close_delay_s", "exit_diff_per_share", "exit_exec_slip_per_share", "mt5_pnl"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows:
            w.writerow(r)
    print("per-signal detail written: %s (%d rows)" % (out, len(rows)))

    print("WORST 12 by dollars lost against the software (filled and closed):")
    worst = sorted((r for r in rows if r.get("mt5_pnl") is not None and r.get("sw_pnl_at_user_size") is not None),
                   key=lambda r: float(r["mt5_pnl"]) - float(r["sw_pnl_at_user_size"]))[:12]
    for r in worst:
        print("   #%s %s %-6s %-5s sw %s->%s  mt5 %s->%s  delay in %ss out %ss  sw $%s  mt5 $%s  (%s)"
              % (r["trade_id"], r["day"], r["symbol"], r["side"], f2(r["sw_entry"]), f2(r["sw_exit"]), f2(r.get("mt5_fill")),
                 f2(r.get("mt5_close")), f2(r.get("entry_delay_s"), 0), f2(r.get("close_delay_s"), 0),
                 f2(r["sw_pnl_at_user_size"]), f2(r["mt5_pnl"]), (r.get("close_reason") or "")[:24]))
    print("BY DAY: signals / filled / software $ at user size (all) / MT5 $ recorded")
    for d in sorted(days):
        dr = [r for r in rows if r["day"] == d]
        print("   %s  %3d / %3d   sw $%9s   mt5 $%9s" % (
            d, len(dr), sum(1 for r in dr if r["entry_event"] == "FILLED"),
            f2(sum(float(r["sw_pnl_at_user_size"]) for r in dr if r.get("sw_pnl_at_user_size") is not None)),
            f2(sum(float(r["mt5_pnl"]) for r in dr if r.get("mt5_pnl") is not None))))
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
