"""C.2: copy our intraday trades into a user's own broker account, live.

The owner's decision of 2026-11-16 replaced the planned dry-run stage: a user who connects an account, ticks the
consent box and switches Auto-copy ON has real orders placed in that account. Whatever they connected - demo or
real - is what trades.

HOW IT IS WIRED
    demo_trade_service emits an ENTRY / EXIT event after each commit.  mt5_autotrade.on_paper_event sends OUR
    order; this module is registered as a SECOND listener and sends each copying user's. The tracker's hook
    dispatcher already isolates listeners from one another, and the work happens on this module's own thread, so
    nothing a user's broker does - a refusal, a hang, a dead session - can slow our book or the tracker.

ISOLATION, in three layers, because one user's broker failing must never touch anyone else:
    1. the event goes on a queue; one worker drains it, never the tracker thread;
    2. each user is handled inside its own try/except, so an exception ends that user's order and no other;
    3. every broker call carries that user's own session token (C.1), never the operator's.

SIZE
    The user sets ONE share count for every stock (default 10). The lot is computed from THEIR broker's contract
    size, step, minimum and maximum, read live from their own account - never assumed from ours, because brokers
    differ (100 shares is 1.0 lot at a contract size of 100 and 100.0 lots at 1). A share count that does not
    land exactly on their broker's volume grid is SKIPPED, never rounded to a different number of shares.

RAILS, per user: max open positions, a daily loss limit as a percent of their own equity, their own pause and
Auto-copy switches, an operator pause (one user or everyone), and the same 2.5% stop cap our book uses. Orders
across all users are rate-limited to MAX_ORDERS_PER_SECOND so one busy minute cannot flood the broker.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

import broker_rate
import demo_trade_service as dts
import mt5_autotrade as AT
import mt5_bridge as bridge
import user_mt5_service as usvc
import user_mt5_store as store

_log = logging.getLogger("user_copy")

MAX_ORDERS_PER_SECOND = 2.0          # owner's broker limit, across ALL users
QUEUE_MAX = 2000
EQUITY_TTL_S = 60.0
SYMBOL_TTL_S = 6 * 3600.0
PREVIEW_MAX = 80

# Why the worker is not running, when it is not. The page shows this instead of a bare "NOT running":
# a status with no reason is almost useless when something has gone wrong at boot.
_start_error: Optional[str] = None

_queue: deque = deque()
_wake = threading.Condition()
_worker: Optional[threading.Thread] = None
_stop = False

_rate_lock = threading.Lock()
_last_order_at = 0.0

_sym_cache: dict = {}                # (username, symbol) -> (broker_symbol, info, at)
_equity_cache: dict = {}             # username -> (equity, at)


# --------------------------------------------------------------------------- the rate limit
def _throttle() -> None:
    """Hold every order behind the ONE gate the operator's book also passes through.

    F: this used to be a user-only limiter. The broker counts orders per second across the whole account, so
    a user limiter the admin engine could not see never actually bounded the rate - on 2026-11-16 a GME order
    was rejected outright with "Max 3 orders per second".
    """
    broker_rate.acquire("user")


# --------------------------------------------------------------------------- the queue
def on_paper_event(ev: dict) -> None:
    """The tracker hook. Copies the event onto the queue and returns at once. Never raises into the tracker."""
    try:
        if (ev or {}).get("type") not in ("ENTRY", "EXIT"):
            return
        with _wake:
            if len(_queue) >= QUEUE_MAX:
                _log.error("copy queue full (%d): dropping %s %s", len(_queue), ev.get("type"), ev.get("symbol"))
                return
            _queue.append(dict(ev))
            _wake.notify()
    except Exception:  # noqa: BLE001 - a hook must never disturb the tracker
        _log.exception("could not queue a copy event")


def start() -> None:
    """Start the worker. Called once at startup; safe to call again.

    A failure here is recorded AND raised: the caller writes it to mt5.log, and `worker_error()` puts it on
    the operator's page. A copy engine that is quietly not running is the worst of the possible states.
    """
    global _worker, _stop, _start_error
    if _worker and _worker.is_alive():
        _start_error = None
        return
    try:
        _stop = False
        _worker = threading.Thread(target=_loop, name="user-copy", daemon=True)
        _worker.start()
        if not _worker.is_alive():
            raise RuntimeError("the worker thread did not come up")
        _start_error = None
    except Exception as exc:  # noqa: BLE001
        _start_error = "%s: %s" % (type(exc).__name__, exc)
        raise


def worker_error() -> Optional[str]:
    """The reason the worker is not running, or None. Also set when it died after a clean start."""
    if _worker and _worker.is_alive():
        return None
    if _start_error:
        return _start_error
    if _worker is None:
        return "the copy worker was never started (check mt5.log at boot)"
    return "the copy worker started and then stopped (check mt5.log)"


def stop() -> None:
    global _stop
    _stop = True
    with _wake:
        _wake.notify_all()


def queue_depth() -> int:
    return len(_queue)


def _loop() -> None:
    while not _stop:
        with _wake:
            while not _queue and not _stop:
                _wake.wait(5.0)
            ev = _queue.popleft() if _queue else None
        if ev is None:
            continue
        try:
            dispatch(ev)
        except Exception:  # noqa: BLE001
            _log.exception("copy dispatch failed for %s %s", ev.get("type"), ev.get("symbol"))


def dispatch(ev: dict) -> list:
    """Place this event for every user who is copying. One user's failure never reaches another's."""
    try:
        users = store.copying_users()
    except Exception:  # noqa: BLE001
        _log.exception("could not read the copying users")
        return []
    out = []
    for u in users:
        name = u.get("username")
        try:
            out.append(dict(_for_user(u, ev) or {}, username=name))
        except Exception as exc:  # noqa: BLE001 - layer 2 of the isolation
            _log.exception("copy failed for %s", name)
            _safe_log(name, "ERROR", symbol=ev.get("symbol"), admin_trade_id=ev.get("trade_id"),
                      reason="%s: %s" % (type(exc).__name__, exc))
            out.append({"username": name, "ok": False, "reason": "ERROR"})
    return out


def _for_user(u: dict, ev: dict) -> dict:
    if ev.get("type") == "ENTRY":
        return _open_for(u, ev)
    return _close_for(u, ev)


def _safe_log(username: str, event: str, **cols) -> None:
    try:
        store.log_order(username, event, **cols)
    except Exception:  # noqa: BLE001
        _log.exception("could not log %s for %s", event, username)


# --------------------------------------------------------------------------- the user's own broker symbol
def _broker_symbol(username: str, token: str, sym: str) -> tuple:
    """(broker_symbol, info) at THIS user's broker, or (None, reason).

    Our own mapping is only the first candidate: the user may be at a different broker with different names, so
    the configured suffixes are tried against their account until one answers with real volume rules.
    """
    key = (username, sym)
    hit = _sym_cache.get(key)
    if hit and time.time() - hit[2] < SYMBOL_TTL_S:
        return (hit[0], hit[1]) if hit[0] else (None, "NOT_AT_BROKER")

    ours = (AT.mapped(sym) or {}).get("broker_symbol")
    seen, cands = set(), []
    for c in ([ours] if ours else []) + [sym] + [sym + suf for suf in AT._suffixes() if suf]:  # noqa: SLF001
        if c and c.upper() not in seen:
            seen.add(c.upper())
            cands.append(c)
    for cand in cands:
        info = bridge.symbol(cand, token=token, strict_token=True)
        if not info.get("ok"):
            continue
        if info.get("volume_min") is None or info.get("volume_step") is None:
            continue
        _sym_cache[key] = (cand, info, time.time())
        return cand, info
    _sym_cache[key] = (None, None, time.time())
    return None, "NOT_AT_BROKER"


def forget_symbols(username: Optional[str] = None) -> None:
    for k in [k for k in list(_sym_cache) if username is None or k[0] == username]:
        _sym_cache.pop(k, None)


# --------------------------------------------------------------------------- the per-user rails
def _equity(username: str, token: str) -> Optional[float]:
    hit = _equity_cache.get(username)
    if hit and time.time() - hit[1] < EQUITY_TTL_S:
        return hit[0]
    res = bridge._call("AccountSummary", token=token, strict_token=True)                        # noqa: SLF001
    if not res.get("ok"):
        return None
    d = res.get("data") or {}
    eq = bridge._f(bridge._g(d, "equity", "Equity"))                         # noqa: SLF001
    if eq is None:
        return None
    _equity_cache[username] = (float(eq), time.time())
    return float(eq)


def _entry_refused(u: dict, ev: dict, token: str) -> Optional[str]:
    """The reason not to open for this user, or None. Ordered so the log says exactly which rail stopped it."""
    sym = ev.get("symbol")
    if AT.is_excluded(sym):
        return "EXCLUDED"
    capped = dts.stop_cap_reason(ev.get("entry"), ev.get("stop"))
    if capped:
        return capped                                                        # the same 2.5% cap our book uses
    open_now = store.open_tickets(u["username"])
    if len(open_now) >= int(u.get("max_positions") or store.DEFAULT_MAX_POSITIONS):
        return "MAX_OPEN_POSITIONS"
    pct = float(u.get("daily_loss_pct") or store.DEFAULT_DAILY_LOSS_PCT)
    realized = store.realized_today(u["username"])
    if realized < 0 and pct > 0:
        eq = _equity(u["username"], token)
        if eq is None:
            return "EQUITY_UNKNOWN"                                          # fail closed: no equity, no new risk
        if abs(realized) >= eq * pct / 100.0:
            return "DAILY_LOSS_LIMIT"
    return None


# --------------------------------------------------------------------------- open
def _open_for(u: dict, ev: dict) -> dict:
    name, sym, side, tid = u["username"], ev.get("symbol"), ev.get("side"), ev.get("trade_id")
    strat = ev.get("strategy")
    common = {"symbol": sym, "side": side, "admin_trade_id": tid, "strategy": strat,
              "shares": int(u.get("shares") or store.DEFAULT_SHARES), "admin_price": ev.get("entry")}
    if not bridge.available():
        _safe_log(name, "SKIPPED", reason="PROVIDER_UNAVAILABLE", **common)
        return {"ok": False, "reason": "PROVIDER_UNAVAILABLE"}
    # F.1: a session that is PROVEN to be this user's account, or nothing at all. Before this, a missing
    # token fell through to the operator's session inside _call and the order appeared in account 250086.
    token, err = usvc.session_for(name)
    if err:
        _safe_log(name, "SKIPPED", reason="%s: %s" % (err.get("reason"), err.get("detail") or ""), **common)
        return {"ok": False, "reason": err.get("reason")}

    why = _entry_refused(u, ev, token)
    if why:
        _safe_log(name, "SKIPPED", reason=why, **common)
        return {"ok": False, "reason": why.split(":")[0]}

    bsym, info = _broker_symbol(name, token, sym)
    if not bsym:
        _safe_log(name, "SKIPPED", reason="NOT_AT_BROKER: your broker does not offer %s" % sym, **common)
        return {"ok": False, "reason": "NOT_AT_BROKER"}

    shares = common["shares"]
    # F.1: ....94 had no margin, so nothing could ever have filled there - but we sent anyway and the order
    # went somewhere else entirely. An account with nothing to trade on is skipped with a clear reason.
    eq = _equity(name, token)
    if eq is not None and eq <= 0:
        _safe_log(name, "SKIPPED", broker_symbol=bsym, reason="NO_MARGIN: this account has no equity to trade with",
                  **common)
        return {"ok": False, "reason": "NO_MARGIN"}
    z = AT.sizing(info.get("contract_size"), info.get("volume_min"), info.get("volume_step"),
                  info.get("volume_max"), shares)
    if not z["valid"]:
        _safe_log(name, "SKIPPED", broker_symbol=bsym, reason="SKIPPED_SIZE: %s" % z["reason"], **common)
        return {"ok": False, "reason": "SKIPPED_SIZE"}

    t = bridge.tick(bsym, token=token, strict_token=True)
    ref = (t.get("ask") if side in ("BUY", "LONG") else t.get("bid")) if t.get("ok") else None
    _throttle()
    res = bridge.market_order(bsym, side, z["volume"], sl=ev.get("stop"), tp=ev.get("target"),
                              deviation_points=AT.DEVIATION_POINTS, magic=AT.magic_for(strat),
                              comment="TRDGO copy #%s" % tid, sym_info=info, token=token, strict_token=True)
    if not res.get("ok"):
        _safe_log(name, "REJECTED", broker_symbol=bsym, volume=z["volume"],
                  reason="%s (%s): %s" % (res.get("retcode_text") or res.get("reason"), res.get("retcode"),
                                          res.get("comment") or res.get("detail")), **common)
        return {"ok": False, "reason": res.get("retcode_text") or res.get("reason")}

    fill = res.get("price")
    _safe_log(name, "FILLED", broker_symbol=bsym, volume=float(res.get("volume") or z["volume"]),
              price=fill, slippage=_slip(ref, fill, side, opening=True),
              ticket=res.get("order") or res.get("deal"), **common)
    return {"ok": True, "ticket": res.get("order") or res.get("deal"), "volume": res.get("volume")}


def _slip(ref, fill, side, *, opening: bool) -> Optional[float]:
    """Adverse price movement in dollars per share, positive = worse for the user."""
    try:
        if ref is None or fill is None:
            return None
        sgn = 1 if side in ("BUY", "LONG") else -1
        diff = (float(fill) - float(ref)) if opening else (float(ref) - float(fill))
        return round(diff * sgn, 4)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- close
def _close_for(u: dict, ev: dict) -> dict:
    """Close the user's position when ours closes - score exit, stop, target or breakeven."""
    name, tid = u["username"], ev.get("trade_id")
    row = store.open_row(name, tid)
    if not row:
        return {"ok": True, "reason": "NOTHING_OPEN"}                        # we never opened one for this user
    return close_row(name, row, ev.get("reason") or "EXIT", admin_price=ev.get("exit_price"))


def close_row(name: str, row: dict, reason: str, *, admin_price=None) -> dict:
    """Close ONE recorded position in a user's account. Used by the exit event and by the EOD sweep."""
    tid = row.get("admin_trade_id")
    ev = {"reason": reason, "exit_price": admin_price}
    common = {"symbol": row.get("symbol"), "broker_symbol": row.get("broker_symbol"), "side": row.get("side"),
              "shares": row.get("shares"), "volume": row.get("volume"), "admin_trade_id": tid,
              "strategy": row.get("strategy"), "ticket": row.get("ticket"), "admin_price": admin_price}
    token, err = usvc.session_for(name)
    if err:
        _safe_log(name, "CLOSE_FAIL", reason="%s: %s" % (err.get("reason"), err.get("detail") or ""), **common)
        return {"ok": False, "reason": err.get("reason")}

    t = bridge.tick(row.get("broker_symbol"), token=token, strict_token=True)
    ref = (t.get("bid") if row.get("side") in ("BUY", "LONG") else t.get("ask")) if t.get("ok") else None
    last = {}
    for attempt in range(1, AT.MAX_CLOSE_ATTEMPTS + 1):
        _throttle()
        last = bridge.close_position(int(row["ticket"]), deviation_points=AT.DEVIATION_POINTS * attempt,
                                     magic=AT.magic_for(row.get("strategy")),
                                     comment="TRDGO copy %s #%s" % ((ev.get("reason") or "EXIT")[:10], tid),
                                     token=token, strict_token=True)
        if last.get("ok") and last.get("price") is not None:
            fill = last.get("price")
            _safe_log(name, "CLOSED", price=fill, slippage=_slip(ref, fill, row.get("side"), opening=False),
                      pnl=_pnl(row, fill), reason=ev.get("reason"), **common)
            return {"ok": True, "attempts": attempt}
        if last.get("ok"):
            # F.1: "ok" with no fill price means the broker could not see the ticket - which is what a close
            # aimed at the WRONG ACCOUNT looks like. On 2026-11-16 every one of these was written as CLOSED,
            # so the book read flat while 25 positions stayed open. It is now recorded as unconfirmed, the
            # row stays open so the sweep retries it, and it is visible on the page.
            _safe_log(name, "CLOSE_UNCONFIRMED", reason="the broker confirmed no fill for this ticket: %s"
                      % (last.get("retcode_text") or last.get("reason") or "no price returned"), **common)
            _log.error("unconfirmed close for %s ticket %s (%s): the position may still be open",
                       name, row.get("ticket"), row.get("symbol"))
            return {"ok": False, "reason": "CLOSE_UNCONFIRMED"}
        if last.get("retcode_text") == "NO_RETRY":
            break
        time.sleep(0.5 * attempt)
    _safe_log(name, "CLOSE_FAIL", reason="could not close after %d attempts; last: %s: %s -- STILL OPEN"
              % (AT.MAX_CLOSE_ATTEMPTS, last.get("retcode_text") or last.get("reason"),
                 last.get("comment") or last.get("detail")), **common)
    return {"ok": False, "reason": "CLOSE_FAILED"}


def _pnl(row: dict, fill) -> Optional[float]:
    """Realized dollars for the user: the move per share times THEIR share count."""
    try:
        if fill is None or row.get("price") is None:
            return None
        sgn = 1 if row.get("side") in ("BUY", "LONG") else -1
        return round((float(fill) - float(row["price"])) * sgn * float(row.get("shares") or 0), 2)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- what My Broker shows
def preview(username: str, limit: int = PREVIEW_MAX) -> dict:
    """For each stock we trade: the user's chosen share count and the lot that will be sent at THEIR broker."""
    st = store.settings_of(username)
    shares = int(st.get("shares") or store.DEFAULT_SHARES)
    if not st.get("has_account"):
        return {"ok": False, "reason": "NO_ACCOUNT", "shares": shares, "rows": []}
    if not bridge.available():
        return {"ok": False, "reason": "PROVIDER_UNAVAILABLE", "shares": shares, "rows": []}
    # F.1: the preview reads the user's own broker, so it uses the same verified session as a real order.
    token, err = usvc.session_for(username)
    if err:
        return dict(err, shares=shares, rows=[])
    rows, excluded = [], set(AT.excluded_symbols())
    for sym in AT.software_symbols()[:max(1, int(limit))]:
        if sym in excluded:
            continue
        bsym, info = _broker_symbol(username, token, sym)
        if not bsym:
            rows.append({"symbol": sym, "broker_symbol": None, "shares": shares, "volume": None,
                         "valid": False, "reason": "your broker does not offer it"})
            continue
        z = AT.sizing(info.get("contract_size"), info.get("volume_min"), info.get("volume_step"),
                      info.get("volume_max"), shares)
        rows.append({"symbol": sym, "broker_symbol": bsym, "shares": shares,
                     "contract_size": info.get("contract_size"), "volume": z["volume"],
                     "valid": z["valid"], "reason": z["reason"]})
    return {"ok": True, "shares": shares, "rows": rows,
            "tradable": sum(1 for r in rows if r["valid"]), "total": len(rows)}


def user_status(username: str) -> dict:
    """The copy half of what My Broker shows: the switches, the rails and how many positions are open."""
    st = store.settings_of(username)
    try:
        open_now = store.open_tickets(username) if st.get("has_account") else []
        realized = store.realized_today(username) if st.get("has_account") else 0.0
    except Exception:  # noqa: BLE001
        open_now, realized = [], 0.0
    copying = bool(st.get("auto_copy") and st.get("enabled") and not st.get("admin_paused"))
    # C.2b: never report "off" without the reason. Four things must hold, and a user who cannot see which one
    # is missing has no way to act - which is exactly what happened with the pause on 2026-11-16.
    if not st.get("has_account"):
        blocked = "Connect an account first."
    elif not st.get("consent_at"):
        blocked = "Accept the risk notice first."
    elif not st.get("auto_copy"):
        blocked = "Auto-trade is off. Switch it on to start trading."
    elif st.get("admin_paused"):
        blocked = "An administrator has paused trading for this account."
    elif not st.get("enabled"):
        blocked = "Auto-trade is on, but this account is paused. Press Resume to start trading."
    else:
        blocked = None
    running = bool(_worker and _worker.is_alive())
    return {"stage": "C.2", "live": True, "copying": copying, "blocked_reason": blocked, "settings": st,
            "open_positions": len(open_now), "realized_today": round(realized, 2),
            "worker_running": running, "worker_error": (None if running else worker_error()),
            "note": ("Orders are being placed in your own account." if copying
                     else "No orders are being placed. %s" % blocked)}


# --------------------------------------------------------------------------- end of day, on OUR clock
# The same window the admin book uses (A.22). Imported, not copied, so the two cannot drift apart.
SWEEP_POLL_S = 30.0
_sweep: Optional[threading.Thread] = None
_sweep_state: dict = {"last_sweep_date": None, "closed": 0, "failed": 0, "last_run": None,
                      "last_retry_date": None, "retried": 0}


def _et_now():
    return AT._et_now()                                                      # noqa: SLF001 - one clock only


def _in_window(now, start, end) -> bool:
    return (start[0], start[1]) <= (now.hour, now.minute) <= (end[0], end[1])


def open_rows_all_users() -> list:
    """(username, row) for every position this system opened and has not recorded closing.

    Read from the ORDER LOG, not from the copying-users list: a user who has since switched Auto-trade off,
    or been paused, still has our position in their account and it must still be closed.
    """
    out = []
    try:
        for acct in store.all_accounts():
            name = acct.get("username")
            try:
                for row in store.open_tickets(name):
                    out.append((name, row))
            except Exception:  # noqa: BLE001 - one unreadable account must not hide the rest
                _log.exception("could not read the open positions of %s", name)
    except Exception:  # noqa: BLE001
        _log.exception("could not list the accounts for the end-of-day sweep")
    return out


def sweep_now(reason: str = "EOD_SWEEP") -> dict:
    """Close every open user position. Safe to call twice: a row already closed is simply not in the list."""
    closed = failed = 0
    for name, row in open_rows_all_users():
        try:
            res = close_row(name, row, reason)
            if res.get("ok"):
                closed += 1
            else:
                failed += 1
        except Exception:  # noqa: BLE001 - one user's broker must never stop the others being closed
            failed += 1
            _log.exception("end-of-day close failed for %s", name)
    if closed or failed:
        _log.warning("user end-of-day sweep (%s): %d closed, %d still open", reason, closed, failed)
    return {"closed": closed, "failed": failed}


def sweep_once() -> dict:
    """One tick of the sweep clock. Does nothing outside the windows, and nothing at the weekend."""
    now = _et_now()
    if now.weekday() >= 5:
        return {"action": "NONE", "reason": "WEEKEND"}
    today = now.date().isoformat()
    _sweep_state["last_run"] = now.strftime("%Y-%m-%d %H:%M:%S")

    if _in_window(now, AT.EOD_SWEEP_START, AT.EOD_SWEEP_END):
        res = sweep_now("EOD_SWEEP")
        _sweep_state["closed"] += res["closed"]
        _sweep_state["failed"] = res["failed"]
        if res["failed"] == 0:
            _sweep_state["last_sweep_date"] = today
        return dict(res, action="EOD_SWEEP")

    # A close the broker refused with "market closed" leaves the row open; retry it next session.
    if (now.hour, now.minute) >= AT.PENDING_RETRY_FROM and now.hour < 12 \
            and _sweep_state.get("last_retry_date") != today:
        rows = open_rows_all_users()
        if rows:
            res = sweep_now("PENDING_RETRY")
            _sweep_state["retried"] += res["closed"]
            _sweep_state["last_retry_date"] = today
            return dict(res, action="PENDING_RETRY")
        _sweep_state["last_retry_date"] = today
    return {"action": "NONE", "reason": "OUTSIDE_WINDOW"}


def sweep_state() -> dict:
    d = dict(_sweep_state)
    d["running"] = bool(_sweep and _sweep.is_alive())
    d["window_et"] = "%02d:%02d-%02d:%02d" % (AT.EOD_SWEEP_START + AT.EOD_SWEEP_END)
    return d


def _sweep_loop() -> None:
    # Wait a poll BEFORE the first pass. Nothing needs closing in the first thirty seconds of a process, and
    # doing database work the instant the app boots makes start-up slower and less predictable - it also made
    # a provider-coalescing test fail, because every TestClient start-up ran a sweep.
    while not _stop:
        waited = 0.0
        while waited < SWEEP_POLL_S and not _stop:
            time.sleep(1.0)
            waited += 1.0
        if _stop:
            return
        try:
            sweep_once()
        except Exception:  # noqa: BLE001 - the sweep must outlive any single failure
            _log.exception("user end-of-day sweep tick failed")


def start_sweep() -> None:
    global _sweep
    if _sweep and _sweep.is_alive():
        return
    _sweep = threading.Thread(target=_sweep_loop, name="user-copy-eod", daemon=True)
    _sweep.start()
