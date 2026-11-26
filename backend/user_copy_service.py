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

import json
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
import user_symbol_map as symmap

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

# ----------------------------------------------------------------------------- V
LIVE_TTL_S = 5.0                     # how long one read of a user's open positions is reused
RECONCILE_EVERY_S = 300.0            # a user's log is checked against the broker at least this often while trading
# A position filled moments ago may not be in the broker's list yet. Reconciliation must never mistake that
# for "closed at the broker": it would drop a live position from our log and leave it open with nobody
# watching it. So a row younger than this is left alone by reconcile_user and still counts toward the cap.
RECONCILE_MIN_AGE_S = 120.0
_live_cache: dict = {}               # username -> (set of open tickets | None, at)
_last_reconcile: dict = {}           # username -> epoch
_closing: set = set()                # (username, ticket) with a close in flight - never two at once
_closing_lock = threading.Lock()
_user_queues: dict = {}              # username -> {"exits": deque, "entries": deque, "cv": Condition, "thread": Thread}
_user_queues_lock = threading.Lock()
_tl = threading.local()              # per-thread step timings for the row being written


def _steps() -> Optional[dict]:
    return getattr(_tl, "steps", None)


def _mark(label: str, started: float) -> None:
    st = _steps()
    if st is not None:
        st[label] = st.get(label, 0) + int(round((time.time() - started) * 1000))


def _timed(label: str, fn, *args, **kw):
    t0 = time.time()
    try:
        return fn(*args, **kw)
    finally:
        _mark(label, t0)

_equity_cache: dict = {}             # username -> (equity, at)


# --------------------------------------------------------------------------- the rate limit
def _throttle() -> None:
    """Hold every order behind the ONE gate the operator's book also passes through.

    F: this used to be a user-only limiter. The broker counts orders per second across the whole account, so
    a user limiter the admin engine could not see never actually bounded the rate - on 2026-11-16 a GME order
    was rejected outright with "Max 3 orders per second".
    """
    t0 = time.time()
    broker_rate.acquire("user")
    _mark("rate_gate_ms", t0)


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
            _queue.append(dict(ev, _queued_at=time.time()))
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
            fan_out(ev)
        except Exception:  # noqa: BLE001
            _log.exception("copy dispatch failed for %s %s", ev.get("type"), ev.get("symbol"))


def fan_out(ev: dict) -> int:
    """V: hand the event to each copying user's OWN worker and return at once.

    Before this one thread walked every user in turn, so user B's close waited for all of user A's gateway
    calls (three calls of up to 12 s each). Now a slow or failing account delays only itself. Within one user
    exits go before entries: getting out is never queued behind getting in.
    """
    try:
        users = store.copying_users()
    except Exception:  # noqa: BLE001
        _log.exception("could not read the copying users")
        return 0
    _log_not_copying(ev)
    for u in users:
        _enqueue_for(u, ev)
    return len(users)


def _log_not_copying(ev: dict) -> None:
    """V: an entry that a connected account does not take is written to that account's log with the reason.
    No broker call is made for these accounts."""
    if ev.get("type") != "ENTRY":
        return
    try:
        for a in store.not_copying_accounts():
            _safe_log(a["username"], "SKIPPED", symbol=ev.get("symbol"), side=ev.get("side"),
                      admin_trade_id=ev.get("trade_id"), strategy=ev.get("strategy"),
                      shares=int(a.get("shares") or store.DEFAULT_SHARES), admin_price=ev.get("entry"),
                      reason=a["why"])
    except Exception:  # noqa: BLE001
        _log.exception("could not record the accounts that are not copying")


def _enqueue_for(u: dict, ev: dict) -> None:
    name = u.get("username")
    with _user_queues_lock:
        q = _user_queues.get(name)
        if q is None or not q["thread"].is_alive():
            q = {"exits": deque(), "entries": deque(), "cv": threading.Condition()}
            q["thread"] = threading.Thread(target=_user_loop, args=(name, q), name="user-copy-%s" % str(name)[-4:], daemon=True)
            _user_queues[name] = q
            q["thread"].start()
    with q["cv"]:
        (q["exits"] if ev.get("type") == "EXIT" else q["entries"]).append((dict(u), dict(ev)))
        q["cv"].notify()


def _user_loop(name: str, q: dict) -> None:
    while not _stop:
        with q["cv"]:
            while not q["exits"] and not q["entries"] and not _stop:
                q["cv"].wait(5.0)
            item = q["exits"].popleft() if q["exits"] else (q["entries"].popleft() if q["entries"] else None)
        if item is None:
            continue
        u, ev = item
        try:
            _for_user(u, ev)
        except Exception as exc:  # noqa: BLE001 - layer 2 of the isolation
            _log.exception("copy failed for %s", name)
            _safe_log(name, "ERROR", symbol=ev.get("symbol"), admin_trade_id=ev.get("trade_id"),
                      reason="%s: %s" % (type(exc).__name__, exc))


def user_queue_depths() -> dict:
    with _user_queues_lock:
        return {str(n)[-4:]: len(q["exits"]) + len(q["entries"]) for n, q in _user_queues.items()}


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
    # V: every row this call writes carries how long each step took, starting with the wait in the queue.
    _tl.steps = {}
    if ev.get("_queued_at"):
        _tl.steps["queue_wait_ms"] = int(round((time.time() - float(ev["_queued_at"])) * 1000))
    try:
        if ev.get("type") == "ENTRY":
            return _open_for(u, ev)
        return _close_for(u, ev)
    finally:
        _tl.steps = None


def _safe_log(username: str, event: str, **cols) -> None:
    try:
        st = _steps()
        if st and "timing" not in cols:
            cols["timing"] = json.dumps(st, separators=(",", ":"))
        store.log_order(username, event, **cols)
    except Exception:  # noqa: BLE001
        _log.exception("could not log %s for %s", event, username)


# --------------------------------------------------------------------------- the user's own broker symbol
def _broker_symbol(username: str, token: str, sym: str) -> tuple:
    """(broker_symbol, info) from the user's OWN mapping, or (None, "NOT_MAPPED").

    G: an order goes only to a symbol this user has mapped and we verified against their broker. C.2 used to
    guess - our name first, then a list of suffixes - and a near-miss suffix can be a different instrument,
    which is how the operator's own book took wrong-symbol fills in A.18. Guessing is the wrong default when
    the result is a real order in someone else's account.

    Unmapped is not a failure: the signal still shows on screen for everyone, and the order is skipped with a
    reason the user can see and act on from My Broker.
    """
    row = symmap.get(username, sym)
    if not row:
        return None, "NOT_MAPPED"
    # I: `ok` is what market_order checks FIRST - `if not info.get("ok"): return info`. Without it every
    # user order returned this dict unchanged before any request was sent, and the refusal read
    # "None (None): None". The mapping is verified against the broker at map time, so ok=True is the truth.
    info = {"ok": True, "symbol": row["broker_symbol"],
            "contract_size": row.get("contract_size"), "volume_min": row.get("volume_min"),
            "volume_step": row.get("volume_step"), "volume_max": row.get("volume_max"),
            "digits": row.get("digits")}
    if info["volume_min"] is None or info["volume_step"] is None:
        return None, "NOT_MAPPED"
    return row["broker_symbol"], info


def forget_symbols(username: Optional[str] = None) -> None:
    """G: the broker symbol LIST is cached in user_mt5_service; the per-symbol probe cache is gone, because
    the user's stored mapping is now the source of truth. Kept so callers need not change."""
    usvc.forget_symbols(username) if username else None


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
    # G: the operator's exclusion list was hand-written for OUR broker (and contains "ORACLE", which matches
    # nothing). A user's own verified mapping decides what their account can trade; an unmapped symbol is
    # skipped in _open_for with NOT_MAPPED. The operator's own list is unchanged.
    capped = dts.stop_cap_reason(ev.get("entry"), ev.get("stop"))
    if capped:
        return capped                                                        # the same 2.5% cap our book uses
    cap = int(u.get("max_positions") or store.DEFAULT_MAX_POSITIONS)
    really_open = open_count(u["username"], token)
    if really_open >= cap:
        return "MAX_OPEN_POSITIONS: %d open, your limit is %d" % (really_open, cap)
    pct = float(u.get("daily_loss_pct") or store.DEFAULT_DAILY_LOSS_PCT)
    realized = store.realized_today(u["username"])
    if realized < 0 and pct > 0:
        eq = _equity(u["username"], token)
        if eq is None:
            return "EQUITY_UNKNOWN"                                          # fail closed: no equity, no new risk
        if abs(realized) >= eq * pct / 100.0:
            return "DAILY_LOSS_LIMIT"
    return None


# --------------------------------------------------------------------------- V: the broker is the truth
def live_tickets(username: str, token: str, *, fresh: bool = False) -> Optional[set]:
    """Tickets really open in the user's account right now, or None when the broker could not be read."""
    hit = _live_cache.get(username)
    if hit and not fresh and time.time() - hit[1] < LIVE_TTL_S:
        return hit[0]
    live = _timed("positions_ms", bridge.positions, token=token, strict_token=True)
    if not live.get("ok"):
        _live_cache[username] = (None, time.time())
        return None
    out = set()
    for p in live.get("positions") or []:
        try:
            out.add(int(p.get("ticket")))
        except (TypeError, ValueError):
            continue
    _live_cache[username] = (out, time.time())
    return out


def _fill_epoch(row: dict) -> float:
    at = row.get("at")
    try:
        if isinstance(at, str):
            from datetime import datetime
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        return at.timestamp() if at is not None else time.time() - 30 * 86400.0
    except Exception:  # noqa: BLE001
        return time.time() - 30 * 86400.0


def settle_closed_at_broker(name: str, row: dict, token: str, *, software_reason: Optional[str] = None,
                            admin_price=None) -> dict:
    """Record a position the BROKER closed (its own stop, target or a manual close) as CLOSED.

    Called only when the broker's own open-position list was read successfully and this ticket is not in it.
    Until kit V such a row was written CLOSE_UNCONFIRMED and left open for ever; on account ..7036 twenty of
    them filled the position ceiling and blocked 414 signals in five days.

    The price comes from the broker's order history when it has one. When it does not, the row is still
    closed - the position does not exist - with the price and P&L left empty and the reason saying so.
    """
    common = {"symbol": row.get("symbol"), "broker_symbol": row.get("broker_symbol"), "side": row.get("side"),
              "shares": row.get("shares"), "volume": row.get("volume"), "admin_trade_id": row.get("admin_trade_id"),
              "strategy": row.get("strategy"), "ticket": row.get("ticket"), "admin_price": admin_price}
    hist = _timed("history_ms", bridge.closed_order, int(row["ticket"]), _fill_epoch(row), token=token, strict_token=True)
    if hist.get("ok") and hist.get("found"):
        fill = hist.get("price")
        _safe_log(name, "CLOSED", price=fill, pnl=_pnl(row, fill),
                  reason="AT_BROKER:%s at %s%s" % (hist.get("how") or "OTHER", hist.get("time") or "?",
                                                   ("; software exit: %s" % software_reason) if software_reason else ""),
                  **common)
        return {"ok": True, "at_broker": True, "price": fill}
    why = "no closed order with this ticket in the broker's history" if hist.get("ok") \
        else "the broker's history could not be read (%s)" % (hist.get("reason") or "unknown")
    _safe_log(name, "CLOSED", price=None, pnl=None,
              reason="AT_BROKER:PRICE_UNKNOWN - the position is not open at the broker; %s%s"
              % (why, ("; software exit: %s" % software_reason) if software_reason else ""), **common)
    return {"ok": True, "at_broker": True, "price": None}


def reconcile_user(name: str, token: Optional[str] = None) -> dict:
    """Make our log agree with the broker: every log-open row whose ticket is not open there is settled.

    READS the broker's position list and order history; sends no order and no close. Safe to call at any time
    and any number of times: a row settled once is no longer log-open, so it is never settled twice.
    """
    if token is None:
        token, err = usvc.session_for(name)
        if err:
            return {"ok": False, "reason": err.get("reason"), "detail": err.get("detail")}
    rows = store.open_tickets(name)
    if not rows:
        _last_reconcile[name] = time.time()
        return {"ok": True, "log_open": 0, "really_open": 0, "settled": 0}
    live = live_tickets(name, token, fresh=True)
    if live is None:
        return {"ok": False, "reason": "POSITIONS_UNREADABLE", "detail": "the broker's open positions could not be read"}
    settled = 0
    for row in rows:
        try:
            if int(row["ticket"]) in live:
                continue
        except (TypeError, ValueError):
            continue
        if time.time() - _fill_epoch(row) < RECONCILE_MIN_AGE_S:
            continue                                       # too new to judge: see RECONCILE_MIN_AGE_S
        if not _begin_close(name, row["ticket"]):
            continue                                       # a close for this ticket is in flight right now
        try:
            settle_closed_at_broker(name, row, token)
            settled += 1
        finally:
            _end_close(name, row["ticket"])
    _last_reconcile[name] = time.time()
    if settled:
        _log.warning("reconciled %s: %d position(s) were closed at the broker and are now recorded closed", str(name)[-4:], settled)
    return {"ok": True, "log_open": len(rows), "really_open": len(rows) - settled, "settled": settled}


def open_count(name: str, token: str) -> int:
    """How many of OUR positions are really open in this user's account - what the position cap counts.

    The log says which positions are ours; the broker says which of them still exist. When the broker cannot
    be read the log's own count is used, which can only over-count: the cap fails closed, never open.
    """
    rows = store.open_tickets(name)
    if not rows:
        return 0
    stale = time.time() - float(_last_reconcile.get(name) or 0) >= RECONCILE_EVERY_S
    live = live_tickets(name, token)
    if live is None:
        return len(rows)
    missing = [r for r in rows if _ticket_int(r) not in live and time.time() - _fill_epoch(r) >= RECONCILE_MIN_AGE_S]
    if missing or stale:
        res = reconcile_user(name, token)
        if res.get("ok"):
            return int(res["really_open"])
    return len(rows) - len(missing)


def _ticket_int(row: dict) -> Optional[int]:
    try:
        return int(row.get("ticket"))
    except (TypeError, ValueError):
        return None


def _begin_close(name: str, ticket) -> bool:
    with _closing_lock:
        key = (name, str(ticket))
        if key in _closing:
            return False
        _closing.add(key)
        return True


def _end_close(name: str, ticket) -> None:
    with _closing_lock:
        _closing.discard((name, str(ticket)))


def _margin_refusal(name: str, token: str, bsym: str, side: str, volume: float) -> Optional[str]:
    """V: "Not enough margin", decided from the broker's own figures before the order is sent.

    Free margin from the account summary, the requirement from the broker's RequiredMargin for this exact
    order. When either cannot be read the order is NOT blocked here: the broker still decides, and its own
    "not enough money" refusal is recorded under the same plain reason.
    """
    if not token:
        return None                                        # never read an account without the user's own session
    acct = _timed("account_ms", bridge.account, token=token)
    free = (acct.get("account") or {}).get("margin_free") if acct.get("ok") else None
    need = _timed("margin_ms", bridge.required_margin, bsym, side, volume, token=token, strict_token=True)
    if free is None or not need.get("ok"):
        return None
    if float(need["margin"]) > float(free):
        return "NOT_ENOUGH_MARGIN: this order needs %.2f and %.2f is free" % (float(need["margin"]), float(free))
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
        _safe_log(name, "SKIPPED", reason="NOT_MAPPED: map %s to your broker's symbol on My Broker to trade it"
                  % sym, **common)
        return {"ok": False, "reason": "NOT_MAPPED"}

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

    short = _margin_refusal(name, token, bsym, side, z["volume"])
    if short:
        _safe_log(name, "SKIPPED", broker_symbol=bsym, volume=z["volume"], reason=short, **common)
        return {"ok": False, "reason": "NOT_ENOUGH_MARGIN"}

    t = _timed("quote_ms", bridge.tick, bsym, token=token, strict_token=True)
    ref = (t.get("ask") if side in ("BUY", "LONG") else t.get("bid")) if t.get("ok") else None
    _throttle()
    res = _timed("order_ms", bridge.market_order, bsym, side, z["volume"], sl=ev.get("stop"), tp=ev.get("target"),
                 deviation_points=AT.DEVIATION_POINTS, magic=AT.magic_for(strat),
                 comment="TRDGO copy #%s" % tid, sym_info=info, token=token, strict_token=True)
    _live_cache.pop(name, None)                           # the position list just changed
    if not res.get("ok"):
        why = _refusal_text(res)
        if any(w in why.lower() for w in ("not enough money", "no money", "margin")):
            _safe_log(name, "SKIPPED", broker_symbol=bsym, volume=z["volume"],
                      reason="NOT_ENOUGH_MARGIN: the broker refused the order (%s)" % why[:120], **common)
            return {"ok": False, "reason": "NOT_ENOUGH_MARGIN"}
        _safe_log(name, "REJECTED", broker_symbol=bsym, volume=z["volume"], reason=why, **common)
        return {"ok": False, "reason": res.get("retcode_text") or res.get("reason") or "REJECTED"}

    fill = res.get("price")
    _safe_log(name, "FILLED", broker_symbol=bsym, volume=float(res.get("volume") or z["volume"]),
              price=fill, slippage=_slip(ref, fill, side, opening=True),
              ticket=res.get("order") or res.get("deal"), **common)
    return {"ok": True, "ticket": res.get("order") or res.get("deal"), "volume": res.get("volume")}


def _refusal_text(res: dict) -> str:
    """Why the broker refused, in words. Never "None (None): None".

    F promised every refusal names itself. A format string of three missing fields keeps that promise in
    form and breaks it in substance - which is exactly what the owner saw after G. If the result carries
    nothing we can name, say so and show its shape, so the next person has something to work with.
    """
    bits = []
    for key in ("retcode_text", "reason", "retcode", "comment", "detail", "stage", "note"):
        v = res.get(key)
        if v not in (None, "", {}, []):
            bits.append("%s=%s" % (key, str(v)[:120]))
    if bits:
        return "; ".join(bits)
    return "the broker answered without a reason (keys: %s)" % ", ".join(sorted(res.keys())) or "no detail"


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
    if not _begin_close(name, row.get("ticket")):
        return {"ok": False, "reason": "CLOSE_IN_PROGRESS"}        # the sweep and an exit event met: one of them closes
    try:
        return _close_row_locked(name, row, ev, common, tid)
    finally:
        _end_close(name, row.get("ticket"))
        _live_cache.pop(name, None)


def _close_row_locked(name: str, row: dict, ev: dict, common: dict, tid) -> dict:
    token, err = usvc.session_for(name)
    if err:
        _safe_log(name, "CLOSE_FAIL", reason="%s: %s" % (err.get("reason"), err.get("detail") or ""), **common)
        return {"ok": False, "reason": err.get("reason")}

    t = _timed("quote_ms", bridge.tick, row.get("broker_symbol"), token=token, strict_token=True)
    ref = (t.get("bid") if row.get("side") in ("BUY", "LONG") else t.get("ask")) if t.get("ok") else None
    last = {}
    for attempt in range(1, AT.MAX_CLOSE_ATTEMPTS + 1):
        _throttle()
        last = _timed("close_ms", bridge.close_position, int(row["ticket"]), deviation_points=AT.DEVIATION_POINTS * attempt,
                      magic=AT.magic_for(row.get("strategy")),
                      comment="TRDGO copy %s #%s" % ((ev.get("reason") or "EXIT")[:10], tid),
                      token=token, strict_token=True)
        if last.get("ok") and last.get("price") is not None:
            fill = last.get("price")
            _safe_log(name, "CLOSED", price=fill, slippage=_slip(ref, fill, row.get("side"), opening=False),
                      pnl=_pnl(row, fill), reason=ev.get("reason"), **common)
            return {"ok": True, "attempts": attempt}
        if last.get("ok"):
            # "ok" with no fill price means the broker's own open-position list was read and this ticket is
            # not in it. F.1 (2026-11-16) recorded that as unconfirmed and left the row open, because closes
            # were then being aimed at the WRONG account. Since F.1 every call here carries this user's own
            # verified session (session_for, strict_token), so "not open in this account" is what it says.
            # V: check once more with a fresh read, then record what the broker did. Never left open again.
            live = live_tickets(name, token, fresh=True)
            if live is not None and _ticket_int(row) in live:
                continue                                   # it IS still open: the first read was stale, try again
            if live is None:
                _safe_log(name, "CLOSE_FAIL", reason="the broker's open positions could not be re-read to confirm "
                          "this close; it will be checked again", **common)
                return {"ok": False, "reason": "POSITIONS_UNREADABLE"}
            settle_closed_at_broker(name, row, token, software_reason=ev.get("reason"), admin_price=common.get("admin_price"))
            return {"ok": True, "at_broker": True, "attempts": attempt}
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
    rows = []
    for sym in AT.software_symbols()[:max(1, int(limit))]:
        bsym, info = _broker_symbol(username, token, sym)
        if not bsym:
            rows.append({"symbol": sym, "broker_symbol": None, "shares": shares, "volume": None,
                         "valid": False, "mapped": False, "reason": "not mapped yet"})
            continue
        z = AT.sizing(info.get("contract_size"), info.get("volume_min"), info.get("volume_step"),
                      info.get("volume_max"), shares)
        rows.append({"symbol": sym, "broker_symbol": bsym, "shares": shares, "mapped": True,
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
    gateway = bridge.gateway_status()
    if blocked is None and not gateway.get("ok"):
        blocked = gateway.get("message")
    return {"stage": "C.2", "live": True, "copying": copying and bool(gateway.get("ok")), "blocked_reason": blocked,
            "gateway": gateway, "settings": st,
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
    # V: first make every account's log agree with the broker, so the sweep only tries to close positions
    # that exist. Before this each phantom row was retried every 30 s for the whole window, every day.
    try:
        for acct in store.all_accounts():
            name = acct.get("username")
            try:
                if store.open_tickets(name):
                    res = reconcile_user(name)
                    closed += int(res.get("settled") or 0) if res.get("ok") else 0
            except Exception:  # noqa: BLE001
                _log.exception("could not reconcile %s before the sweep", str(name)[-4:])
    except Exception:  # noqa: BLE001
        _log.exception("could not list the accounts to reconcile before the sweep")
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
