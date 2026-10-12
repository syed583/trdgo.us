"""
The AI Trade board: the directional model run across a universe, ranked.

One symbol's call already exists on the analysis screen. This asks the same
question of many names at once and sorts the answers, which is the question
people actually arrive with -- not "what does the model think of NVDA" but
"what does it like today".

Three things this deliberately does not do:

  * It does not invent a shortlist. Every name scored is in the universe
    below, chosen once for liquidity, and the board reports how many of them
    actually returned data. A board that silently drops the names that failed
    looks confident about a market it only half read.

  * It does not promote a blocked reading. A symbol the model refuses to call
    -- thin coverage, parameters contradicting each other, confidence below
    the floor -- is held out of both columns and counted separately. The whole
    point of that gate is that the label carries more authority than the
    number beside it.

  * It never suggests size, entry or exit. This app does not trade.

Scoring a symbol means waking a dozen providers, so the scan runs
continuously in the background rather than on a request. Asking for the board
sorts the scores already in memory -- microseconds -- so a page can poll it
every minute and see each name move as the rolling scan reaches it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import directional_model as dm
import directional_score_service as ds
import foreground
from live_market_service import cache, market_clock

# Liquid, optionable, and spread across sectors -- the names the rest of the
# app already fetches, so most of this is answered from warm caches.
UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "AMD", "NFLX", "CRM", "ORCL", "ADBE", "INTC", "MU", "QCOM",
    "JPM", "BAC", "GS", "V", "MA",
    "XOM", "CVX", "COP",
    "UNH", "LLY", "JNJ", "PFE",
    "WMT", "COST", "HD", "NKE", "DIS",
    "CAT", "BA", "GE",
    "SPY", "QQQ",
]

# How long the board stands is no longer a question the ranking asks -- it is
# recomposed from live scores on every request. This is the pause between
# background scan passes: short, because the page polls every minute and a
# ranking that never changes between polls is not worth polling.
SCAN_PAUSE = 45.0

# A whole-market scan runs long. This is the ceiling on the whole board, not
# per symbol: whatever has finished when it expires is what gets returned,
# with the rest reported as pending rather than as absent.
BOARD_BUDGET = 150.0

# Enough parallelism to be useful, low enough not to stampede the providers
# that every worker shares -- and they are shared: one TWS connection serves
# this scan and every screen in the app, so a worker here is a worker not
# serving a page.
WORKERS = 4

# Symbols submitted per batch. The scan stands aside between batches, so this
# is also how long a page can wait behind it: smaller means the scan gives way
# sooner, at the cost of a little more scheduling overhead.
BATCH = 4

# What the pause between passes becomes when the market is shut. Nothing in
# the model moves overnight or at the weekend -- prices are closed, filings
# are not arriving -- so rescanning at the live cadence spends the provider
# budget to confirm yesterday's answer.
CLOSED_PAUSE = 900.0

# How many names each column shows.
TOP_N = 10

# How long one symbol's score stands on the board.
#
# A pass cannot reach the whole universe inside its budget, so scores are kept
# between passes and the board is composed from every score still fresh.
# Without this the tail of the universe would never appear: each pass cuts out
# at the same place, so the same names would be missing for ever.
#
# It is also the honesty limit. A score older than this is dropped rather than
# ranked, because a half-hour-old reading sitting in a list the page refreshes
# every minute reads as current when it is not.
ROW_TTL = 1800.0

# Where the next pass starts. Passes rotate through the universe so coverage
# accumulates instead of re-scoring the same head of the list.
_CURSOR = 0
_ROWS: dict[str, tuple[float, dict]] = {}
_ROWS_LOCK = threading.Lock()

# Where the scores are kept between runs of the server.
#
# They used to live only in memory, so every restart emptied the board and it
# took the rolling scan ten minutes to fill again -- opening the page in that
# window showed an app with nothing to say and no indication why. Scores keep
# their own timestamps here, so a restart resumes with what was known and the
# stale ones expire on the same rule as always.
_STATE = Path(__file__).parent / ".cache" / "ai_trade_rows.json"

# Held separately so the guard above can tell "the real file" from one a test
# has pointed somewhere harmless.
_DEFAULT_STATE = _STATE

# The TODAY board is a live intraday read, so once the session closes the
# rules-engine stops calling names and both columns empty out. Traders still
# want to see what the model called during the day, so the last non-empty set
# of TODAY buyers/sellers is held per ET date and shown after the close until
# the date rolls over at midnight ET (then the new day starts clean).
_HOLD = Path(__file__).parent / ".cache" / "ai_trade_today_hold.json"
_TODAY_HOLD: dict = {}  # {"date": "YYYY-MM-DD", "buyers": [...], "sellers": [...]}


def _save_rows() -> None:
    # Never write the real board from a test run. A scan with a fixture
    # universe saved "AAA" and "BBB" into the file the running app reloads,
    # and they then appeared on the live board as buy-rated names -- invented
    # tickers presented exactly like real ones, which is the worst thing this
    # screen could show.
    if os.environ.get("PYTEST_CURRENT_TEST") and _STATE == _DEFAULT_STATE:
        return
    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        with _ROWS_LOCK:
            payload = {"cursor": _CURSOR,
                       "rows": {s: [at, row] for s, (at, row) in _ROWS.items()}}
        # Written via a temporary file and moved into place: a half-written
        # board on disk would be read back as a corrupt one at the next start.
        fd, tmp = tempfile.mkstemp(dir=str(_STATE.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, _STATE)
    except Exception:  # noqa: BLE001 - the board works without a saved copy
        pass


def _load_rows() -> None:
    global _CURSOR

    try:
        if not _STATE.is_file():
            return
        payload = json.loads(_STATE.read_text(encoding="utf-8"))
        now = time.time()
        known = set(UNIVERSE)
        restored = {
            key: (float(at), row)
            for key, (at, row) in (payload.get("rows") or {}).items()
            # A score older than its window is not resurrected: a restart is
            # not a reason to show a reading the running app would have
            # dropped. Nor is anything outside the universe -- a stale file
            # from an older build, or a fixture, must not put a ticker on the
            # board that this app does not actually score.
            if now - float(at) <= ROW_TTL and _split(key)[1] in known
        }
        with _ROWS_LOCK:
            _ROWS.update(restored)
        _CURSOR = int(payload.get("cursor") or 0) % max(len(UNIVERSE), 1)
    except Exception:  # noqa: BLE001 - a bad file must not stop the server
        pass
    _load_hold()


def _et_date() -> str:
    """Today's date in US market time (ET). The TODAY hold is keyed on this so
    it clears when the trading day rolls over, not on the server's local day."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 - no tzdata: approximate ET as UTC-4
        from datetime import timezone, timedelta
        return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def _save_hold() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST") and _STATE == _DEFAULT_STATE:
        return
    try:
        _HOLD.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(_HOLD.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dict(_TODAY_HOLD), fh)
        os.replace(tmp, _HOLD)
    except Exception:  # noqa: BLE001
        pass


def _load_hold() -> None:
    try:
        if _HOLD.is_file():
            data = json.loads(_HOLD.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _TODAY_HOLD.clear()
                _TODAY_HOLD.update(data)
    except Exception:  # noqa: BLE001
        pass


def _hold_board(limit: int) -> Optional[dict]:
    """A board built from today's held TODAY calls, or None when there is no
    hold for the current ET date."""
    if _TODAY_HOLD.get("date") != _et_date():
        return None
    buyers = list(_TODAY_HOLD.get("buyers") or [])
    sellers = list(_TODAY_HOLD.get("sellers") or [])
    if not buyers and not sellers:
        return None
    return {
        "status": "OK", "horizon": "TODAY", "building": False, "held_eod": True,
        "buyers": buyers[:limit], "sellers": sellers[:limit],
        "waiting": [], "held": [], "universe": len(UNIVERSE),
        "scored": len(buyers) + len(sellers), "pending": [],
        "buy_count": len(buyers), "sell_count": len(sellers), "held_count": 0,
        "youngest_seconds": None, "oldest_seconds": None, "elapsed_seconds": 0.0,
        "ttl_seconds": int(ROW_TTL),
        "note": "Today's session is closed — showing the calls the model made "
                "during the day. These clear at midnight ET.",
    }


def _key(symbol: str, horizon: str) -> str:
    """
    Where a row lives. A swing row keeps the bare symbol it always had, so the
    saved board and everything written before horizons existed still reads
    back; the other horizons are prefixed.
    """
    return symbol if horizon == "SWING" else f"{horizon}:{symbol}"


def _split(key: str) -> tuple:
    if ":" in key:
        horizon, symbol = key.split(":", 1)
        return horizon, symbol
    return "SWING", key


def _remember(row: dict) -> None:
    if row.get("status") != "OK":
        return
    horizon = row.get("horizon") or "SWING"
    key = _key(row["symbol"], horizon)
    with _ROWS_LOCK:
        held = _ROWS.get(key)
        # A pass where a feed happened not to answer is not new information
        # about the stock. Withholding a name for thin coverage right after a
        # full-coverage read made it blink off the board and back on; keep
        # the good read until it ages out or a full read replaces it.
        if (held and _thin(row) and not _thin(held[1])
                and time.time() - held[0] < HOLD_GOOD_ROW):
            return
        _ROWS[key] = (time.time(), row)


# How long a full-coverage row stands against a later thin-coverage one.
HOLD_GOOD_ROW = 600.0


def _thin(row: dict) -> bool:
    return any("inputs returned data" in (r or "")
               for r in row.get("blocked_reasons") or [])


def _fresh_rows(horizon: Optional[str] = None) -> list[dict]:
    now = time.time()
    with _ROWS_LOCK:
        stale = [s for s, (at, _) in _ROWS.items() if now - at > ROW_TTL]
        for s in stale:
            del _ROWS[s]
        return [dict(r, age_seconds=round(now - at)) for at, r in _ROWS.values()
                if horizon is None or (r.get("horizon") or "SWING") == horizon]


def _score_one(symbol: str) -> dict:
    """One symbol's call, reduced to what a row needs."""
    scored = ds.get_directional_score(symbol)
    # Kept with its working. Throttled inside: a board call is only written
    # when it changes or has gone stale, not on every pass.
    try:
        import calls_service

        if not os.environ.get("PYTEST_CURRENT_TEST"):
            calls_service.record_async(symbol, scored, origin="board")
    except Exception:  # noqa: BLE001
        pass
    _score_horizons(symbol, scored)
    return _as_row(symbol, scored)


def _horizons_now() -> list:
    """
    Which short horizons are worth scoring at this moment.

    TODAY only while the session is open -- before the bell there is no
    session to read, and after it the question has already been answered.
    TOMORROW always: its inputs are the day's close and what followed it.
    """
    session = (market_clock() or {}).get("session")
    return (["TODAY", "TOMORROW"] if session in ("OPEN", "PRE_MARKET")
            else ["TOMORROW"])


def _score_horizons(symbol: str, base: dict) -> None:
    """
    Score the short horizons from the swing result already in hand.

    Never on a test run: these read live quotes and intraday bars, and a
    fixture universe must not reach a real provider.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not base or base.get("status") == "NO_DATA":
        return
    import horizon_model

    for horizon in _horizons_now():
        try:
            result = horizon_model.score_horizon(symbol, horizon, base=base,
                                                 cached=True)
        except Exception:  # noqa: BLE001 - one horizon must not cost the others
            continue
        _remember(_as_row(symbol, result, horizon))
        try:
            import calls_service

            calls_service.record_async(symbol, result, horizon=horizon,
                                       origin="board")
        except Exception:  # noqa: BLE001
            pass


# SPY/QQQ position vs their own VWAP, computed once per 5-min slot (the gates
# read it for every name, so it must not re-fetch per symbol).
_INDEX_VWAP: dict[str, tuple] = {}


def _above_vwap(symbol: str) -> Optional[bool]:
    """Is `symbol` trading above its intraday VWAP right now? None if unknown."""
    try:
        import horizon_model as hz
        import live_market_service as market
        intraday = hz._intraday(symbol, cached=True)
        bars = hz._today_bars(intraday)
        if not bars:
            return None
        num = den = 0.0
        for b in bars:
            v = float(b.get("volume") or 0)
            hi, lo, cl = b.get("high"), b.get("low"), b.get("close")
            if v <= 0 or None in (hi, lo, cl):
                continue
            num += (float(hi) + float(lo) + float(cl)) / 3.0 * v
            den += v
        price = (market.get_quote(symbol) or {}).get("price")
        if not den or price is None:
            return None
        return float(price) > num / den
    except Exception:  # noqa: BLE001
        return None


def _index_vwap_status(slot: str) -> tuple:
    """(spy_above_vwap, qqq_above_vwap) cached for the current 5-min slot."""
    cached = _INDEX_VWAP.get("v")
    if cached and cached[0] == slot:
        return cached[1], cached[2]
    spy, qqq = _above_vwap("SPY"), _above_vwap("QQQ")
    _INDEX_VWAP["v"] = (slot, spy, qqq)
    return spy, qqq


def _evidence(signals: list, name: str) -> dict:
    for s in signals:
        if s.get("name") == name:
            return s.get("evidence") or {}
    return {}


def _apply_intraday_engine(symbol: str, horizon: str, signals: list, ev: dict) -> dict:
    """
    For the TODAY (intraday) horizon, resolve the shown signal through the rules
    engine: hard gates, time rules, candle-close, entry and the state ladder.
    The two scores are NOT changed -- only which signal/state is shown. Any
    failure falls back to the plain dual-score read.
    """
    if horizon != "TODAY":
        return ev
    try:
        import intraday_signal_engine as eng
        import signal_state_store as store
        import signal_audit_log as audit

        win = eng.session_window()
        now = win.now_et
        slot = now.strftime("%Y-%m-%d ") + f"{now.hour:02d}:{(now.minute // 5) * 5:02d}"

        vw = _evidence(signals, "vwap")
        orn = _evidence(signals, "opening_range")
        rv = _evidence(signals, "relative_volume")
        vwap = vw.get("vwap")
        dist = vw.get("distance_pct")
        price = (vwap * (1 + dist / 100.0)) if (vwap is not None and dist is not None) else None
        spy_above, qqq_above = _index_vwap_status(slot)

        gates = eng.Gates(
            price=price, vwap=vwap,
            spy_above_vwap=spy_above, qqq_above_vwap=qqq_above,
            rvol=rv.get("relative_volume"), or_low=orn.get("or_low"),
            or_high=orn.get("or_high"),
        )

        prev = store.get(horizon, symbol)
        candle_closed = prev.get("candle") != slot
        dec = eng.decide(
            ev.get("buy_score"), ev.get("sell_score"), gates,
            prior_active=prev.get("active"), candle_closed=candle_closed,
            window=win, prior_state=prev.get("state") or eng.NO_TRADE,
        )

        store.update(horizon, symbol, active=dec.active_after,
                     state=dec.state, candle=slot)
        audit.record([{
            "time_et": win.et_str(), "time_dubai": win.dubai_str(),
            "horizon": horizon, "symbol": symbol, "state": dec.state,
            "side": dec.side, "size": dec.size,
            "buy_score": ev.get("buy_score"), "sell_score": ev.get("sell_score"),
            "spy_vs_vwap": "above" if spy_above else "below" if spy_above is False else "?",
            "qqq_vs_vwap": "above" if qqq_above else "below" if qqq_above is False else "?",
            "price_vs_vwap": "above" if gates.price_above_vwap else "below" if gates.price_below_vwap is False else "?",
            "rvol": gates.rvol,
            "buy_gates_pass": dec.gates.get("buy_gates_pass"),
            "sell_gates_pass": dec.gates.get("sell_gates_pass"),
            "gate_fails": (dec.gates.get("buy_gate_fails") or []) + (dec.gates.get("sell_gate_fails") or []),
            "blocked_reason": dec.blocked_reason,
        }])

        out = dict(ev)
        out["decision"] = dec.state
        out["side"] = dec.side
        out["color"] = dec.color
        out["full_size"] = (dec.size == "FULL SIZE")
        out["intraday_size"] = dec.size
        out["intraday_blocked"] = dec.blocked_reason
        out["square_off"] = dec.square_off
        return out
    except Exception:  # noqa: BLE001 - the engine must never break the board
        return ev


def _as_row(symbol: str, d: dict, horizon: str = "SWING") -> dict:
    """
    Shape one directional score into a board row.

    Shared with `record_score` on purpose: a row built two different ways is
    a row that can disagree with itself.
    """
    if not d or d.get("status") == "NO_DATA":
        return {"symbol": symbol, "status": "NO_DATA"}

    decision = d.get("decision") or "WAIT"
    blocked = decision == dm.NO_TRADE or not d.get("actionable", True)

    # Intraday dual-score read (BUY score + SELL score + signal state), per the
    # operator's intraday model. This drives the Buy/Sell classification and the
    # card colour on the Trdgo Stock page; the single directional score above is
    # kept for everything else.
    import intraday_model_service as im
    import intraday_inputs as imi
    signals = list(d.get("signals") or [])
    try:
        signals += imi.extra_signals(symbol)   # market direction, sector, analyst
    except Exception:  # noqa: BLE001 - extras are additive, never break scoring
        pass
    ev = im.evaluate(signals)
    # TODAY only: run the dual scores through the intraday rules engine (gates,
    # time rules, candle close, entry, state ladder). Scores are left untouched.
    ev = _apply_intraday_engine(symbol, horizon, signals, ev)

    return {
        "symbol": symbol,
        "horizon": horizon,
        "status": "OK",
        "decision": decision,
        "blocked": blocked,
        "blocked_reasons": d.get("blocked_reasons") or [],
        "direction_score": d.get("direction_score"),
        # Score is centred on 50: above is bullish, below bearish. The lean is
        # what the columns sort on, so both read "strongest first".
        "lean": round((d.get("direction_score") or 50.0) - 50.0, 1),
        "confidence": d.get("confidence"),
        "agreement_pct": d.get("agreement_pct"),
        "coverage_pct": d.get("coverage_pct"),
        "top_reasons": (d.get("reasons") or [])[:3],
        # Intraday model fields.
        "buy_score": ev.get("buy_score"),
        "sell_score": ev.get("sell_score"),
        "im_decision": ev.get("decision"),
        "im_side": ev.get("side"),
        "im_color": ev.get("color"),
        "im_full_size": ev.get("full_size"),
        "im_size": ev.get("intraday_size"),
        "im_blocked": ev.get("intraday_blocked"),
        "square_off": ev.get("square_off", False),
    }


# One build at a time, process-wide.
#
# Without this every request that arrives while a build is running starts its
# own 38-symbol scan: six workers each, all hammering the same providers, all
# producing the same answer. The first caller builds; everyone else is told a
# build is running and gets whatever the last one produced.
_BUILDING = threading.Lock()


def _starting_reply() -> dict:
    """What to say before anything at all has been scored."""
    return {
        "status": "BUILDING",
        "building": True,
        "buyers": [], "sellers": [], "waiting": [], "held": [],
        "universe": len(UNIVERSE), "scored": 0, "pending": list(UNIVERSE),
        "buy_count": 0, "sell_count": 0, "held_count": 0,
        "elapsed_seconds": 0.0, "ttl_seconds": int(ROW_TTL),
        "youngest_seconds": None, "oldest_seconds": None,
        "note": (
            "Scoring the universe now. Each name wakes a dozen providers, so "
            "the first names appear within a minute and the board fills in "
            "from there."
        ),
    }


def next_pause() -> float:
    """
    How long to wait before the next pass.

    Long when the market is shut: nothing the model reads changes overnight,
    so a scan then costs provider budget and contention to confirm an answer
    that has not moved.
    """
    session = (market_clock() or {}).get("session")
    return SCAN_PAUSE if session in ("OPEN", "PRE_MARKET", "AFTER_HOURS") else CLOSED_PAUSE


def is_building() -> bool:
    return _BUILDING.locked()


def prewarm() -> None:
    """Kick off a build in the background if one is not already running."""
    if _BUILDING.locked():
        return
    threading.Thread(
        target=lambda: get_board(refresh=True),
        daemon=True, name="ai-trade-build").start()


def record_score(symbol: str, scored: dict, horizon: str = "SWING") -> None:
    """
    Take a score somebody just ran and put it on the board.

    Without this the two screens disagree for as long as the rolling scan
    takes to come back round: the row still shows the reading from ten minutes
    ago while the analysis the reader just watched run shows today's. Both
    were honest and they still did not match, which is indistinguishable from
    a bug. A freshly run score is the best one available, so the board takes
    it.
    """
    if not scored or (symbol or "").upper() not in set(UNIVERSE):
        return
    row = _as_row((symbol or "").upper(), scored, horizon)
    if row.get("status") == "OK":
        _remember(row)
        _save_rows()


def get_board(limit: int = TOP_N, refresh: bool = False,
              wait: bool = False, horizon: str = "SWING") -> dict:
    """
    Rank the universe by the model's own direction score.

    Composing the board is cheap -- it is a sort over scores already held in
    memory -- and only the scan behind it is slow. They are separate here for
    that reason: a caller always gets the freshest ranking that exists right
    now, without waiting for, or triggering, a market-wide rescan. The scan
    runs continuously in the background and each finished symbol updates the
    ranking the next caller sees.

    ``refresh=True`` is the background scanner asking for a pass. ``wait=True``
    blocks for one, and is only for a caller that genuinely wants to sit
    through a full scan.
    """
    horizon = (horizon or "SWING").upper()
    if refresh or (wait and not _fresh_rows(horizon)):
        if not _BUILDING.acquire(blocking=False):
            return _compose(limit, building=True, horizon=horizon)
        try:
            built = _build(limit)
            if horizon == "SWING":
                return built
            out = _compose(limit, horizon=horizon)
            out["elapsed_seconds"] = built.get("elapsed_seconds")
            return out
        finally:
            _BUILDING.release()

    if (horizon == "TODAY" and not _fresh_rows(horizon)
            and "TODAY" not in _horizons_now()):
        # Session is over and nothing is scored live -- but if the model made
        # calls earlier today, show that held set (clears at midnight ET)
        # instead of an empty board.
        hold = _hold_board(limit)
        if hold is not None:
            return hold
        # Today is only scored during the session, so outside it there is
        # nothing coming -- say so instead of spinning until the bell.
        clock = market_clock() or {}
        reply = _starting_reply()
        reply.update({
            "status": "SESSION_CLOSED", "horizon": horizon, "building": False,
            "pending": [], "session": clock.get("session"),
            "note": (
                f"Today's session is over or has not started "
                f"({clock.get('label') or 'closed'}, {clock.get('time_et') or ''}). "
                "Today calls begin at 4:00 AM ET pre-market, from yesterday's "
                "close and the pre-market move, and switch to the live read at "
                "9:30 AM ET. Use Tomorrow or Swing until then."
            ),
        })
        return reply

    if not _fresh_rows(horizon):
        # Nothing scored for this horizon yet: start the scan and say so
        # rather than blocking the request thread on it.
        prewarm()
        reply = _starting_reply()
        reply["horizon"] = horizon
        return reply

    return _compose(limit, building=_BUILDING.locked(), horizon=horizon)


def _build(limit: int) -> dict:
    """The scan itself. Only ever called with the build lock held."""
    global _CURSOR

    started = time.monotonic()
    deadline = started + BOARD_BUDGET

    # Start where the last pass gave up, so every pass scores a different
    # slice and the whole universe is covered over a few of them. Names the
    # deadline cuts off are the next pass's first work, not permanently
    # missing.
    order = UNIVERSE[_CURSOR:] + UNIVERSE[:_CURSOR]
    reached: set[str] = set()

    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        # Submitted in batches rather than all at once, so the scan has a
        # point at which it can stand aside. Queueing all 38 up front hands
        # the whole universe to the pool and there is no longer any moment at
        # which the scan is not competing with the screen.
        for start in range(0, len(order), BATCH):
            if time.monotonic() >= deadline:
                break
            foreground.yield_to_foreground()

            batch = order[start:start + BATCH]
            jobs = {pool.submit(_score_one, sym): sym for sym in batch}
            remaining = max(1.0, deadline - time.monotonic())
            try:
                for job in as_completed(jobs, timeout=remaining):
                    reached.add(jobs[job])
                    try:
                        _remember(job.result())
                    except Exception:  # noqa: BLE001
                        # One provider failing for one symbol must not take
                        # the board down; that name keeps its previous score
                        # until it goes stale.
                        pass
            except TimeoutError:
                break
    finally:
        # Never `with`: the context manager joins every worker on exit, which
        # would make the deadline above decorative.
        pool.shutdown(wait=False, cancel_futures=True)

    _CURSOR = (_CURSOR + max(len(reached), 1)) % len(UNIVERSE)
    _save_rows()

    out = _compose(limit)
    out["elapsed_seconds"] = round(time.monotonic() - started, 1)
    out["scanned_this_pass"] = len(reached)
    return out


def _compose(limit: int = TOP_N, building: bool = False,
             horizon: str = "SWING") -> dict:
    """Rank whatever scores are currently held. No provider is touched."""
    scored = _fresh_rows(horizon)
    have = {r["symbol"] for r in scored}
    pending = [s for s in UNIVERSE if s not in have]
    # Classification comes entirely from the intraday dual-score model here --
    # the single directional model's own NO-TRADE gate does not apply to this
    # page. A name is a buyer when its signal state is a BUY state (Active or
    # Weakening), ranked by its BUY score; a seller likewise by SELL score;
    # everything else is the model's NO TRADE (withheld).
    if horizon == "TODAY":
        # Intraday horizon: the dual-score / rules-engine call (im_side).
        buyers = sorted(
            [r for r in scored if r.get("im_side") == "buy"],
            key=lambda r: r.get("buy_score") or 0.0, reverse=True)
        sellers = sorted(
            [r for r in scored if r.get("im_side") == "sell"],
            key=lambda r: r.get("sell_score") or 0.0, reverse=True)
        held = [r for r in scored if r.get("im_side") not in ("buy", "sell")]
    else:
        # TOMORROW / SWING: use the DIRECTIONAL call (the same decision stored in
        # the daily snapshots and shown in the past-day view), so the board's
        # sellers match the history instead of the stricter intraday read.
        def _dside(r: dict) -> str:
            dec = (r.get("decision") or "").upper()
            if "BUY" in dec:
                return "buy"
            if "SELL" in dec:
                return "sell"
            return "none"
        for r in scored:
            side = _dside(r)
            r["im_side"] = side
            r["im_color"] = "green" if side == "buy" else "red" if side == "sell" else "none"
            # Show the directional call text (STRONG BUY / BUY / SELL / ...).
            r["im_decision"] = r.get("decision") if side in ("buy", "sell") else "NEUTRAL"
        buyers = sorted(
            [r for r in scored if r.get("im_side") == "buy"],
            key=lambda r: r.get("direction_score") or 0.0, reverse=True)
        sellers = sorted(
            [r for r in scored if r.get("im_side") == "sell"],
            key=lambda r: r.get("direction_score") or 100.0)   # most bearish first
        held = [r for r in scored if r.get("im_side") not in ("buy", "sell")]
    waiting: list[dict] = []

    # Portfolio limits (intraday / TODAY only): at most 4 active signals and at
    # most 2 per sector, and never a BUY and a SELL in the same sector. Strongest
    # first; anything over a limit is shown as WAITING - LIMIT FULL.
    if horizon == "TODAY":
        try:
            from intraday_inputs import _TICKER_ETF
        except Exception:  # noqa: BLE001
            _TICKER_ETF = {}
        MAX_ACTIVE, MAX_PER_SECTOR = 4, 2
        strength = lambda r: (r.get("buy_score") if r.get("im_side") == "buy"
                              else r.get("sell_score")) or 0.0
        cands = sorted(buyers + sellers, key=strength, reverse=True)
        admitted: list[dict] = []
        sec_count: dict = {}
        sec_side: dict = {}
        for r in cands:
            sec = _TICKER_ETF.get(r["symbol"]) or r["symbol"]
            side = r.get("im_side")
            full = len(admitted) >= MAX_ACTIVE
            clash = sec in sec_side and sec_side[sec] != side
            crowded = sec_count.get(sec, 0) >= MAX_PER_SECTOR
            if full or clash or crowded:
                r["im_decision"] = "WAITING - LIMIT FULL"
                r["im_side"] = "none"
                r["im_color"] = "none"
                r["limit_waiting"] = True
                waiting.append(r)
                continue
            admitted.append(r)
            sec_count[sec] = sec_count.get(sec, 0) + 1
            sec_side[sec] = side
        buyers = [r for r in admitted if r.get("im_side") == "buy"]
        sellers = [r for r in admitted if r.get("im_side") == "sell"]

    # TODAY persistence: while the session produces calls, keep the latest
    # non-empty set as "today's calls". Once it empties out (session closed),
    # show that held set instead of a blank board, until midnight ET.
    held_eod = False
    if horizon == "TODAY":
        today = _et_date()
        if buyers or sellers:
            _TODAY_HOLD.clear()
            _TODAY_HOLD.update({"date": today, "buyers": buyers[:limit],
                                "sellers": sellers[:limit]})
            _save_hold()
        elif (_TODAY_HOLD.get("date") == today
              and (_TODAY_HOLD.get("buyers") or _TODAY_HOLD.get("sellers"))):
            buyers = list(_TODAY_HOLD.get("buyers") or [])
            sellers = list(_TODAY_HOLD.get("sellers") or [])
            held_eod = True

    ages = [r.get("age_seconds") or 0 for r in scored]

    return {
        "status": "OK" if (scored or held_eod) else "BUILDING",
        "horizon": horizon,
        "building": building or _BUILDING.locked(),
        "held_eod": held_eod,
        "buyers": buyers[:limit],
        "sellers": sellers[:limit],
        "waiting": sorted(waiting, key=lambda r: abs(r.get("lean") or 0.0),
                          reverse=True)[:limit],
        "held": sorted(held, key=lambda r: r.get("confidence") or 0.0,
                       reverse=True),
        "universe": len(UNIVERSE),
        "scored": len(scored),
        "pending": pending,
        "buy_count": len(buyers),
        "sell_count": len(sellers),
        "held_count": len(held),
        # How old the scores behind this ranking actually are. The page can
        # poll every minute; that does not make a score a minute old, and
        # saying so is the difference between fresh and merely re-fetched.
        "youngest_seconds": min(ages) if ages else None,
        "oldest_seconds": max(ages) if ages else None,
        "elapsed_seconds": 0.0,
        "ttl_seconds": int(ROW_TTL),
        "note": (
            "The same model the analysis screen runs, over a fixed universe. "
            "Names the model declines to call are held out of both columns "
            "rather than ranked low. The scan runs continuously in the "
            "background, so the ranking updates as each name is rescored."
        ),
    }

