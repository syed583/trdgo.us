"""
Scoring and page-level aggregation for the Earnings Intelligence screen.

Two different scores live here and they are deliberately not the same thing:

* ``get_watchlist``  - a fast technical composite used for the ticker cards.
  One historical request per symbol, so ten cards stay responsive.

* ``get_trdgo_score`` - the full six-component TRDGO score, reusing the
  existing scoring services. Components whose provider is offline or whose
  only source is TEST data are reported as such rather than being scored,
  which is what the existing trdgo_score_service already gates on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import live_market_service as market
import live_options_analytics as opt_analytics
import live_options_service as options
from analysis_service import build_analysis
from confidence_service import calculate_confidence
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from database import SessionLocal
from earnings_history_score_service import score_earnings_history
from estimate_score_service import score_estimates
from fundamental_score_service import score_fundamentals
from market_environment_service import score_market_environment
from models import Company, EarningsEvent, EstimateSnapshot
from options_score_service import score_options
from risk_service import assess_risk
from sec_fundamental_service import get_clean_fundamentals
from technical_score_service import score_technicals
from technical_service import calculate_technicals
from trdgo_score_service import score_trdgo


def _ttl(open_secs: float, closed_secs: float) -> float:
    """Short cache while the market moves, long cache while it is shut."""
    return open_secs if market.market_clock()["is_open"] else closed_secs


WATCHLIST_TTL_OPEN = 60.0
WATCHLIST_TTL_CLOSED = 900.0
OVERVIEW_TTL_OPEN = 45.0
OVERVIEW_TTL_CLOSED = 900.0

# How long the overview waits for its providers before drawing what it has.
#
# The fetches already run against each other, so the page took as long as its
# slowest one -- and on a cold symbol that was the better part of a minute of
# blank screen. A panel that says it is still loading is worth far more than
# five panels held back by a sixth, so the page is served at this point and
# the stragglers land on the next poll.
OVERVIEW_BUDGET = 14.0

# The same idea one level down: the composite score's six sections run against
# each other, and the score is drawn from whatever answered. A section that
# misses this is reported unavailable and its weight leaves the total, which
# is what the model already does for a provider that is offline.
SCORE_BUDGET = 25.0

# How long a composite score stands.
#
# It was sharing the overview's 45-second window, which is shorter than the
# score takes to compute. The effect was a page that could never complete:
# every visit found the cache expired, started a 25-second rebuild, gave up
# waiting at 14 and showed the score as pending -- for ever. Nothing the score
# reads moves on a 45-second scale anyway: daily bars, SEC filings, analyst
# estimates and an option chain.
SCORE_TTL_OPEN = 300.0
SCORE_TTL_CLOSED = 1800.0

# A page missing panels must not be cached for the full window, or one slow
# moment freezes an incomplete screen for the next quarter of an hour.
PARTIAL_TTL = 20.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bars_for(symbol: str, range_key: str = "1Y") -> list[dict]:
    """Daily bars in the shape the existing technical services expect."""
    chart = market.get_chart(symbol, range_key)
    if chart.get("status") != "OK":
        return []
    return [
        {
            "date": b["t"],
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
            "volume": b["volume"],
        }
        for b in chart["bars"]
    ]


def _rating(display_score: float) -> str:
    if display_score >= 70:
        return "Strong Buy"
    if display_score >= 50:
        return "Buy"
    if display_score >= 20:
        return "Wait"
    if display_score >= -50:
        return "Sell"
    return "Strong Sell"


def _technical_composite(bars: list[dict]) -> Optional[dict]:
    """
    The 0-100 technical composite for the ticker cards.

    Delegates to the shared scorer. This used to be its own weighted blend,
    which meant the cards, the composite score and the backtest were each
    scoring price differently -- three answers to one question, and no way to
    validate any of them against the others.
    """
    from composite_score_service import evaluate

    return evaluate(bars)


# ---------------------------------------------------------------------------
# earnings calendar lookups (database)
# ---------------------------------------------------------------------------


def _timing_labels(reporting_time: Optional[str]) -> tuple[str, str]:
    """(long label, short label) for a reporting time."""
    code = (reporting_time or "").upper()
    if code == "AMC":
        return "After Market Close", "After Close"
    if code == "BMO":
        return "Before Market Open", "Before Open"
    return (reporting_time or "Unconfirmed"), "Unconfirmed"


def _earnings_rows(db, symbols: list[str]) -> dict[str, dict]:
    """
    _earnings_row for many symbols, in a fixed number of queries.

    Called once per symbol this cost ~1s each against a remote database -- ten
    cards spent ~10s on lookups alone. Benzinga's reader already accepts a
    symbol list, and the seed fallback needs only two more queries for the
    whole batch.
    """
    import benzinga_earnings_service as benzinga

    symbols = [s.upper() for s in symbols]
    today = datetime.now(timezone.utc).date()

    # One provider read for every symbol; cached rows only, never a call out.
    upcoming = benzinga.get_upcoming(today, today + timedelta(days=400), symbols)
    by_symbol: dict[str, dict] = {}
    for row in upcoming.get("rows") or []:
        sym = str(row.get("symbol") or "").upper()
        # Rows arrive earnings_date ascending, so the first per symbol is next.
        if sym and sym not in by_symbol:
            by_symbol[sym] = row

    out: dict[str, dict] = {}
    unresolved = []
    for symbol in symbols:
        row = by_symbol.get(symbol)
        if not row:
            unresolved.append(symbol)
            continue
        timing, short = _timing_labels(row.get("reporting_time"))
        out[symbol] = {
            "date": row.get("date"),
            "date_label": row.get("date_label"),
            "reporting_time": row.get("reporting_time"),
            "timing_label": timing,
            "short_label": short,
            "quarter_label": row.get("quarter_label"),
            "eps_estimate": row.get("eps_estimate"),
            "revenue_estimate": row.get("revenue_estimate"),
            "date_confirmed": row.get("date_confirmed", True),
            "lifecycle": row.get("lifecycle"),
            "status": "OK",
            "source": "BENZINGA",
        }

    if not unresolved:
        return out

    companies = {
        c.symbol.upper(): c for c in
        db.query(Company).filter(Company.symbol.in_(unresolved)).all()
    }
    company_ids = [c.id for c in companies.values()]
    events_by_company: dict[Any, Any] = {}
    if company_ids:
        events = (
            db.query(EarningsEvent)
            .filter(EarningsEvent.company_id.in_(company_ids))
            .filter(EarningsEvent.status == "scheduled")
            .order_by(EarningsEvent.earnings_date.asc())
            .all()
        )
        for event in events:
            events_by_company.setdefault(event.company_id, event)

    for symbol in unresolved:
        company = companies.get(symbol)
        if not company:
            out[symbol] = {
                "status": "NOT_TRACKED",
                "source": "BENZINGA",
                "detail": (
                    f"{symbol} is not in the Benzinga calendar cache. "
                    "Coverage follows the subscribed plan; run the calendar "
                    "sync in Settings to refresh it."
                ),
            }
            continue

        event = events_by_company.get(company.id)
        if not event:
            out[symbol] = {"status": "NO_SCHEDULED_EVENT", "source": "DATABASE"}
            continue

        timing, short = _timing_labels(event.reporting_time)
        out[symbol] = {
            "date": event.earnings_date.isoformat() if event.earnings_date else None,
            "date_label": (
                event.earnings_date.strftime("%b %d, %Y")
                if event.earnings_date else None
            ),
            "reporting_time": event.reporting_time,
            "timing_label": timing,
            "short_label": short,
            "eps_estimate": float(event.eps_estimate) if event.eps_estimate else None,
            "revenue_estimate": (
                float(event.revenue_estimate) if event.revenue_estimate else None
            ),
            "status": "TEST_DATA",
            "source": "DATABASE_SEED",
            "detail": (
                "This date comes from the seed database, not a provider. "
                "It is not a verified earnings schedule."
            ),
        }
    return out


def _earnings_row(db, symbol: str) -> dict:
    """
    Next scheduled earnings event for a symbol.

    Benzinga is consulted first because it is the verified source. The legacy
    `earnings_events` table is seed data, so a row from it is returned marked
    TEST_DATA rather than presented as a schedule.

    This order matters: the seed table used to be the *only* source consulted,
    so on the Earnings screen the single ticker showing a date was showing a
    fixture, while the tickers that had real provider rows showed "No date".
    """
    import benzinga_earnings_service as benzinga

    symbol = symbol.upper()
    today = datetime.now(timezone.utc).date()

    # Cached provider rows only - this never triggers a provider call.
    upcoming = benzinga.get_upcoming(today, today + timedelta(days=400), [symbol])
    rows = upcoming.get("rows") or []
    if rows:
        row = rows[0]
        timing, short = _timing_labels(row.get("reporting_time"))
        return {
            "date": row.get("date"),
            "date_label": row.get("date_label"),
            "reporting_time": row.get("reporting_time"),
            "timing_label": timing,
            "short_label": short,
            "quarter_label": row.get("quarter_label"),
            "eps_estimate": row.get("eps_estimate"),
            "revenue_estimate": row.get("revenue_estimate"),
            "date_confirmed": row.get("date_confirmed", True),
            "lifecycle": row.get("lifecycle"),
            "status": "OK",
            "source": "BENZINGA",
        }

    company = db.query(Company).filter(Company.symbol == symbol).first()
    if not company:
        return {
            "status": "NOT_TRACKED",
            "source": "BENZINGA",
            "detail": (
                f"{symbol} is not in the Benzinga calendar cache. "
                "Coverage follows the subscribed plan; run the calendar sync "
                "in Settings to refresh it."
            ),
        }

    event = (
        db.query(EarningsEvent)
        .filter(EarningsEvent.company_id == company.id)
        .filter(EarningsEvent.status == "scheduled")
        .order_by(EarningsEvent.earnings_date.asc())
        .first()
    )
    if not event:
        return {"status": "NO_SCHEDULED_EVENT", "source": "DATABASE"}

    timing, short = _timing_labels(event.reporting_time)

    # Seed row: labelled so the UI can mark it, never reported as OK.
    return {
        "date": event.earnings_date.isoformat() if event.earnings_date else None,
        "date_label": (
            event.earnings_date.strftime("%b %d, %Y") if event.earnings_date else None
        ),
        "reporting_time": event.reporting_time,
        "timing_label": timing,
        "short_label": short,
        "eps_estimate": float(event.eps_estimate) if event.eps_estimate else None,
        "revenue_estimate": (
            float(event.revenue_estimate) if event.revenue_estimate else None
        ),
        "status": "TEST_DATA",
        "source": "DATABASE_SEED",
        "detail": (
            "This date comes from the seed database, not a provider. "
            "It is not a verified earnings schedule."
        ),
    }


# ---------------------------------------------------------------------------
# watchlist / ticker cards
# ---------------------------------------------------------------------------


# A cold composite costs one SEC companyfacts fetch (~9-17s per symbol), so it
# never runs on the request path. The strip serves whatever is already cached
# and warms the rest on a background pool; cards fill in on the next refresh.
def _scored_names(final: dict) -> list[str]:
    """Component names that contributed, i.e. everything not gated out."""
    missing = set(final.get("missing_components") or [])
    return [n for n in ("fundamentals", "estimates", "technicals",
                        "earnings_history", "options", "market_environment")
            if n not in missing] or ["none"]


STRIP_SCORE_WORKERS = 3

_warming: set[str] = set()
_warm_lock = threading.Lock()
_warm_pool = ThreadPoolExecutor(max_workers=STRIP_SCORE_WORKERS,
                                thread_name_prefix="strip-warm")


def _warm_symbol(symbol: str) -> None:
    try:
        get_trdgo_score(symbol)
    except Exception:  # noqa: BLE001 - warming must never surface an error
        pass
    finally:
        with _warm_lock:
            _warming.discard(symbol)


def warm_scores(symbols: list[str]) -> list[str]:
    """
    Queue composites for symbols that have none cached yet.

    Returns the symbols still pending. Never blocks: the request returns at
    once and the cards fill in as the frontend refreshes.
    """
    pending = []
    for symbol in symbols:
        if get_cached_score(symbol) is not None:
            continue
        pending.append(symbol)
        with _warm_lock:
            if symbol in _warming:
                continue
            _warming.add(symbol)
        _warm_pool.submit(_warm_symbol, symbol)
    return pending


def get_cached_score(symbol: str) -> Optional[dict]:
    """The stored composite for `symbol`, or None if it is not warm yet."""
    cached = market.cache.get(
        f"trdgo:{symbol.upper()}",
        _ttl(OVERVIEW_TTL_OPEN, OVERVIEW_TTL_CLOSED),
    )
    return (cached or {}).get("final") if cached else None


def get_watchlist(symbols: list[str]) -> dict:
    key = "watchlist:" + ",".join(symbols)
    cached = market.cache.get(key, _ttl(WATCHLIST_TTL_OPEN, WATCHLIST_TTL_CLOSED))
    if cached:
        return cached

    batch = market.get_batch(symbols)
    rows = batch.get("symbols", {})

    # Symbols with no usable quote need the composite fallback; work them out
    # up front so they can be scored concurrently instead of one at a time.
    # A quote can now succeed while bars are absent: the fallback chain serves
    # prices from OptionData but carries no history. Keyed on status alone,
    # those symbols skipped the composite and scored nothing at all, so test
    # for the bars the technical composite actually needs.
    needs_composite = [
        s for s in symbols
        if rows.get(s, {}).get("status") != "OK"
        or not rows.get(s, {}).get("bars")
    ]
    composites = {s: f for s in needs_composite
                  if (f := get_cached_score(s)) is not None}
    warm_scores([s for s in needs_composite if s not in composites])

    db = SessionLocal()
    cards = []
    try:
        # Both of these used to run once per symbol against a remote database
        # (~1s each). Batched, the whole row costs two round trips.
        names = {
            c.symbol.upper(): c.company_name
            for c in db.query(Company).filter(Company.symbol.in_(symbols)).all()
            if c.company_name
        }
        earnings = _earnings_rows(db, symbols)

        for symbol in symbols:
            row = rows.get(symbol, {})
            card: dict[str, Any] = {
                "symbol": symbol,
                "name": symbol,
                "price": row.get("price"),
                "change": row.get("change"),
                "change_percent": row.get("change_percent"),
                "quote_status": row.get("status", "NO_DATA"),
            }

            if row.get("status") == "OK" and row.get("bars"):
                composite = _technical_composite(row.get("bars") or [])
                if composite:
                    card.update(
                        {
                            "score": composite["display_score"],
                            "score_raw": composite["score"],
                            "rating": composite["rating"],
                            "score_basis": composite["basis"],
                        }
                    )
                else:
                    card.update({"score": None, "rating": None,
                                 "score_basis": "INSUFFICIENT_HISTORY"})
            else:
                # No IBKR bars for a technical composite. Fall back to the full
                # gated score, which still has fundamentals, earnings history
                # and estimates - the card was reading "--" while the app knew
                # plenty about the symbol.
                final = composites.get(symbol)
                if final and final.get("direction_score") is not None:
                    display = final["direction_score"]
                    card.update({
                        "score": display,
                        "score_raw": display,
                        # The composite's own decision, NOT _rating(): that
                        # maps a -100..100 technical composite, so a 16/100
                        # TRDGO score came out as "Sell" when the engine
                        # actually said WAIT. A gated-low score is "not enough
                        # evidence", never a short signal.
                        "rating": (final.get("decision") or "WAIT").title(),
                        "score_basis": (
                            "Composite score from the components currently "
                            "available (" + ", ".join(
                                final.get("scored_components")
                                or _scored_names(final)) + ")."
                        ),
                        "score_confidence": final.get("confidence_score"),
                        "missing_components": final.get("missing_components"),
                    })
                else:
                    card.update({"score": None, "rating": None,
                                 "score_basis": row.get("status", "NO_DATA")})

            name = names.get(symbol)
            if name:
                card["name"] = name

            card["earnings"] = earnings.get(symbol) or {
                "status": "NO_SCHEDULED_EVENT", "source": "DATABASE"}
            cards.append(card)
    finally:
        db.close()

    result = {
        "symbols": symbols,
        "cards": cards,
        "market": market.market_clock(),
        "score_basis": (
            "Technical composite (trend, momentum, range position, rate of "
            "change) from live IBKR daily bars."
        ),
        "status": "OK",
        "source": "IBKR+DATABASE",
    }

    # Only cache a fully-scored row for the normal TTL. If the budget expired
    # with cards still unscored, caching would freeze those "--" in place for
    # the whole window even though the background threads are about to finish
    # warming them, so leave it uncached and let the next request pick them up.
    unscored = [c["symbol"] for c in cards
                if c.get("score") is None and c["symbol"] in needs_composite]
    if not unscored:
        market.cache.put(key, result)
    else:
        result["scoring"] = {
            "pending": unscored,
            "detail": (
                f"{len(unscored)} card(s) still scoring; they fill in on the "
                "next refresh once the composite finishes."
            ),
        }
    return result


# ---------------------------------------------------------------------------
# full TRDGO six-component score
# ---------------------------------------------------------------------------


def _options_payload(symbol: str, chain: dict, metrics: dict, flow: dict) -> dict:
    """
    Shape the live chain into the payload options_score_service consumes.

    That scorer reads call/put volume, call/put open interest and average ATM
    IV, and it gates everything behind an explicit ``confidence``. The
    confidence here is real data coverage - what fraction of the quoted
    contracts actually came back with a price, an open interest and a solved
    implied volatility - so a thin or half-quoted chain scores conservatively
    instead of pretending to a signal.
    """
    rows = chain.get("rows") or []
    if not rows:
        return {"symbol": symbol, "status": "NO_DATA"}

    total = len(rows)
    priced = sum(1 for r in rows if r.get("mid"))
    with_oi = sum(1 for r in rows if r.get("open_interest"))
    with_iv = sum(1 for r in rows if r.get("iv"))

    coverage = (
        0.45 * (priced / total)
        + 0.35 * (with_oi / total)
        + 0.20 * (with_iv / total)
    )
    confidence = round(coverage * 100)

    # Volume measured off the previous session's tape is real but partial, so
    # it should not carry full confidence.
    tiles = opt_analytics.get_tiles(chain, None, flow)
    if tiles.get("volume_basis") != "SESSION":
        confidence = min(confidence, 70)

    atm_call_iv = next(
        (r["iv"] for r in rows
         if r["right"] == "C" and r["strike"] == metrics.get("atm_call_strike")
         and r.get("iv")),
        None,
    )
    atm_put_iv = next(
        (r["iv"] for r in rows
         if r["right"] == "P" and r["strike"] == metrics.get("atm_put_strike")
         and r.get("iv")),
        None,
    )

    return {
        "symbol": symbol,
        "status": "OK",
        "confidence": confidence,
        "call_volume": tiles.get("call_volume"),
        "put_volume": tiles.get("put_volume"),
        "call_open_interest": tiles.get("call_oi"),
        "put_open_interest": tiles.get("put_oi"),
        "call_iv": round(atm_call_iv * 100, 2) if atm_call_iv else None,
        "put_iv": round(atm_put_iv * 100, 2) if atm_put_iv else None,
        "average_iv_percent": metrics.get("implied_volatility"),
        "expected_move_percent": metrics.get("expected_move_percent"),
        "expected_move_dollars": metrics.get("expected_move_dollars"),
        "iv_percentile": metrics.get("iv_percentile"),
        "underlying_price": chain.get("spot"),
    }


def _component_unavailable(max_score: int, status: str, reason: str) -> dict:
    return {
        "status": status,
        "score": None,
        "max_score": max_score,
        "bias": "INSUFFICIENT_DATA",
        "confidence": 0,
        "reasons": [reason],
        "warnings": [reason],
        "source_quality": status,
    }


def _stamp_status(component: dict) -> dict:
    """
    Give a component an honest `status` before the gate sees it.

    The scorers signal "I had nothing to work with" through `bias` (NO_DATA,
    INSUFFICIENT_DATA) and a zero confidence, but several return no `status`
    key at all. Defaulting those to "OK" told score_trdgo the component was
    verified, so a 0 that meant "no data" was counted as a real bearish zero
    and the component never appeared in missing_components -- the same defect
    that made /market/score disagree with /api/score. Deriving the status from
    the bias keeps "absent" and "genuinely zero" distinct.
    """
    if component.get("status"):
        return component

    bias = str(component.get("bias") or "").upper()
    if bias == "TEST_DATA":
        component["status"] = "TEST_DATA"
    elif bias in {"NO_DATA", "INSUFFICIENT_DATA"}:
        component["status"] = "UNAVAILABLE"
    else:
        component["status"] = "OK"
    return component


def get_trdgo_score(symbol: str) -> dict:
    symbol = symbol.upper()
    key = f"trdgo:{symbol}"
    cached = market.cache.get(key, _ttl(SCORE_TTL_OPEN, SCORE_TTL_CLOSED))
    if cached:
        return cached

    components: dict[str, dict] = {}
    providers: dict[str, str] = {}

    import earnings_intelligence_service as earnings_intel

    # Six independent readings. Run in series they totalled about eighty
    # seconds on a cold symbol -- the single slowest thing in the app, and the
    # reason every screen that needs a score felt broken. None of them needs
    # another's answer; only the scoring at the end needs all of them.
    #
    # Each section owns its own database session. A SQLAlchemy session is not
    # safe to share across threads, and passing one in was the reason this
    # could not simply be parallelised before.

    def _technicals() -> tuple:
        bars = _bars_for(symbol)
        if bars:
            return score_technicals(calculate_technicals(bars)), "OK"
        return _component_unavailable(
            20, "PROVIDER_OFFLINE", "IBKR historical bars unavailable"), "PROVIDER_OFFLINE"

    def _fundamentals() -> tuple:
        try:
            return score_fundamentals(get_clean_fundamentals(symbol)), "OK"
        except Exception as exc:  # noqa: BLE001
            return _component_unavailable(
                20, "UNAVAILABLE", f"SEC fundamentals unavailable: {exc}"), "UNAVAILABLE"

    def _estimates() -> tuple:
        db = SessionLocal()
        try:
            return score_estimates(db, symbol), "OK"
        except Exception as exc:  # noqa: BLE001
            return _component_unavailable(
                25, "UNAVAILABLE", f"Estimate data unavailable: {exc}"), "UNAVAILABLE"
        finally:
            db.close()

    def _earnings_history() -> tuple:
        # Gated on verified rows. Scoring a beat rate off seed data would turn
        # a fixture into a trading signal, which is exactly what the TEST_DATA
        # status exists to prevent.
        try:
            intel = earnings_intel.get_history(symbol, quarters=8)
            if intel["stats"]["basis"] == "VERIFIED":
                return score_earnings_history(
                    earnings_intel.as_score_history(intel)), "OK"
            status = "TEST_DATA" if intel["seed_count"] else "UNAVAILABLE"
            return _component_unavailable(
                15, status, intel["stats"]["detail"]), status
        except Exception as exc:  # noqa: BLE001
            return _component_unavailable(
                15, "UNAVAILABLE", f"Earnings history unavailable: {exc}"), "UNAVAILABLE"

    def _options() -> tuple:
        chain = options.load_chain(symbol)
        if chain.get("status") != "OK":
            return _component_unavailable(
                15, "PROVIDER_OFFLINE",
                f"Options chain unavailable: {chain.get('status')}"), "PROVIDER_OFFLINE"
        flow = options.get_flow(symbol, chain=chain)
        metrics = opt_analytics.get_metrics(
            chain, options.get_iv_history(symbol), None, flow)
        return score_options(
            _options_payload(symbol, chain, metrics, flow)), "OK"

    def _environment() -> tuple:
        try:
            return score_market_environment(), "OK"
        except Exception as exc:  # noqa: BLE001
            return _component_unavailable(
                5, "UNAVAILABLE", f"Market environment unavailable: {exc}"), "UNAVAILABLE"

    sections = [
        ("technicals", "ibkr_history", _technicals, 20,
         "IBKR historical bars unavailable"),
        ("fundamentals", "sec", _fundamentals, 20,
         "SEC fundamentals did not answer in time"),
        ("estimates", "estimates", _estimates, 25,
         "Estimate data did not answer in time"),
        ("earnings_history", "earnings_history", _earnings_history, 15,
         "Earnings history did not answer in time"),
        ("options", "ibkr_options", _options, 15,
         "Options chain did not answer in time"),
        ("market_environment", "market_environment", _environment, 5,
         "Market environment did not answer in time"),
    ]

    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="score")
    try:
        jobs = {name: pool.submit(fn) for name, _, fn, _, _ in sections}
        deadline = time.time() + SCORE_BUDGET
        for name, provider_key, _, weight, late_detail in sections:
            try:
                component, status = jobs[name].result(
                    timeout=max(deadline - time.time(), 0.1))
            except FuturesTimeout:
                # A section that misses the budget is reported as unavailable,
                # not silently scored as zero: the weight it carries is taken
                # out of the total rather than counted against the symbol.
                component, status = _component_unavailable(
                    weight, "SLOW_PROVIDER", late_detail), "SLOW_PROVIDER"
            except Exception as exc:  # noqa: BLE001
                component, status = _component_unavailable(
                    weight, "UNAVAILABLE", f"{name} failed: {exc}"), "UNAVAILABLE"
            components[name] = component
            _stamp_status(components[name])
            providers[provider_key] = status
    finally:
        # The work keeps running: these sections cache their own answers, so
        # what misses this request warms the next one.
        pool.shutdown(wait=False)

    final = score_trdgo({"symbol": symbol, "components": components})
    confidence = calculate_confidence({"symbol": symbol, "components": components})
    risk = assess_risk({"symbol": symbol, "components": components})

    result = {
        "symbol": symbol,
        "final": final,
        "components": components,
        "confidence": confidence,
        "risk": risk,
        "providers": providers,
        "status": "OK",
        "source": "IBKR+SEC+DATABASE",
    }
    market.cache.put(key, result)
    return result


# ---------------------------------------------------------------------------
# analyst estimates & earnings history for the right-hand panels
# ---------------------------------------------------------------------------


def _estimate_trend(db, symbol: str) -> dict:
    """The Current / 7 / 30 / 60 / 90 days ago analyst estimate table."""
    company = db.query(Company).filter(Company.symbol == symbol.upper()).first()
    if not company:
        return {"rows": [], "status": "NOT_TRACKED", "source": "DATABASE"}

    snaps = (
        db.query(EstimateSnapshot)
        .filter(EstimateSnapshot.company_id == company.id)
        .order_by(EstimateSnapshot.snapshot_time.desc())
        .limit(40)
        .all()
    )
    if not snaps:
        return {"rows": [], "status": "NO_DATA", "source": "DATABASE"}

    latest = snaps[0]
    now = latest.snapshot_time

    def nearest(days: int):
        if days == 0:
            return latest
        target = now.timestamp() - days * 86400
        return min(snaps, key=lambda s: abs(s.snapshot_time.timestamp() - target))

    rows = []
    for days, label in ((0, "Current"), (7, "7 Days Ago"), (30, "30 Days Ago"),
                        (60, "60 Days Ago"), (90, "90 Days Ago")):
        snap = nearest(days)
        rows.append(
            {
                "label": label,
                "days_ago": days,
                "eps_estimate": float(snap.eps_estimate) if snap.eps_estimate else None,
                "revenue_estimate_b": (
                    round(float(snap.revenue_estimate) / 1e9, 2)
                    if snap.revenue_estimate else None
                ),
                "analyst_count": snap.analyst_count,
                "snapshot_time": snap.snapshot_time.isoformat(),
                "is_distinct": snap.id != latest.id or days == 0,
            }
        )

    oldest = rows[-1]
    change_pct = None
    if oldest["eps_estimate"] and rows[0]["eps_estimate"]:
        change_pct = round(
            (rows[0]["eps_estimate"] - oldest["eps_estimate"])
            / oldest["eps_estimate"] * 100, 1
        )

    sources = {s.source for s in snaps}
    test_only = sources.issubset({"TEST", None})

    return {
        "rows": rows,
        "eps_change_90d": change_pct,
        "distinct_snapshots": len({r["snapshot_time"] for r in rows}),
        "sources": sorted(x for x in sources if x),
        "status": "TEST_DATA" if test_only else "OK",
        "source": "DATABASE",
    }


def _earnings_history_panel(db, symbol: str) -> dict:
    """Estimate vs actual bars plus beat rate / average surprise."""
    company = db.query(Company).filter(Company.symbol == symbol.upper()).first()
    if not company:
        return {"quarters": [], "status": "NOT_TRACKED", "source": "DATABASE"}

    events = (
        db.query(EarningsEvent)
        .filter(EarningsEvent.company_id == company.id)
        .filter(EarningsEvent.status == "reported")
        .order_by(EarningsEvent.earnings_date.asc())
        .all()
    )
    if not events:
        return {"quarters": [], "status": "NO_DATA", "source": "DATABASE"}

    quarters = []
    surprises = []
    beats = 0
    for e in events:
        est = float(e.eps_estimate) if e.eps_estimate else None
        act = float(e.eps_actual) if e.eps_actual else None
        surprise = None
        if est and act:
            surprise = round((act - est) / abs(est) * 100, 2)
            surprises.append(surprise)
            if act > est:
                beats += 1
        quarters.append(
            {
                "label": f"Q{(e.earnings_date.month - 1) // 3 + 1} {e.earnings_date.year}",
                "date": e.earnings_date.isoformat(),
                "estimate": est,
                "actual": act,
                "surprise_percent": surprise,
                "beat": bool(est and act and act > est),
            }
        )

    consecutive = 0
    for q in reversed(quarters):
        if q["beat"]:
            consecutive += 1
        else:
            break

    scored = len(surprises)
    return {
        "quarters": quarters,
        "beat_rate": round(beats / scored * 100) if scored else None,
        "average_surprise": round(sum(surprises) / scored, 2) if scored else None,
        "consecutive_beats": consecutive,
        "sample_size": len(quarters),
        "status": "OK",
        "source": "DATABASE",
    }


# ---------------------------------------------------------------------------
# the whole Earnings Intelligence screen in one call
# ---------------------------------------------------------------------------


def _collect_reasons(components: dict, options_metrics: dict) -> tuple[list, list]:
    """
    Split the component scorers' own reasons into bullish and bearish lists.

    build_analysis expects these to be supplied; deriving them from each
    component's real bias keeps the two panels honest rather than narrating
    a conclusion the scores do not support.
    """
    bullish: list[str] = []
    bearish: list[str] = []

    for name, comp in (components or {}).items():
        if not isinstance(comp, dict):
            continue
        label = name.replace("_", " ").title()
        bias = str(comp.get("bias") or "").upper()
        status = str(comp.get("status") or "").upper()

        if status not in ("OK",):
            bearish.append(f"{label}: {status.replace('_', ' ').title()} - not scored")
            continue

        for reason in (comp.get("reasons") or [])[:3]:
            if "BULL" in bias:
                bullish.append(f"{label}: {reason}")
            elif "BEAR" in bias:
                bearish.append(f"{label}: {reason}")

        for warning in (comp.get("warnings") or [])[:2]:
            bearish.append(f"{label}: {warning}")

    em = options_metrics.get("expected_move_percent")
    iv_pct = options_metrics.get("iv_percentile")
    realized = options_metrics.get("realized_move_percent")

    if em is not None:
        bearish.append(
            f"Options price a +/-{em}% move by {options_metrics.get('expiry_label')}."
        )
    if iv_pct is not None and iv_pct >= 70:
        bearish.append(f"Implied volatility sits in the {iv_pct:.0f}th percentile of the last year.")
    elif iv_pct is not None and iv_pct <= 25:
        bullish.append(f"Implied volatility is cheap at the {iv_pct:.0f}th percentile of the last year.")
    if em is not None and realized is not None and realized > em:
        bearish.append(
            f"Realised moves have averaged {realized}%, above the {em}% currently implied."
        )

    return bullish, bearish


def get_earnings_overview(symbol: str, chart_range: str = "6M") -> dict:
    symbol = symbol.upper()

    key = f"earnings_overview:{symbol}:{chart_range}"
    cached = market.cache.get(key, _ttl(OVERVIEW_TTL_OPEN, OVERVIEW_TTL_CLOSED))
    if cached:
        # A page served with panels still loading stands for seconds, not for
        # the full window: the stragglers are finishing in the background and
        # the next request should pick them up.
        if cached.get("status") == "PARTIAL":
            until = market.cache.get(f"{key}:partial-until", PARTIAL_TTL * 4)
            if not until or time.time() > until:
                cached = None
        if cached:
            return cached

    # Six independent fetches. Run one after another on a cold symbol they
    # total well past two minutes, which is longer than the proxy in front of
    # this app will wait -- the page showed "Backend unreachable" rather than
    # a slow panel. Nothing here needs another's answer except the option
    # metrics, which need the chain.
    import earnings_intelligence_service as earnings_intel

    def _db_rows() -> tuple:
        db = SessionLocal()
        try:
            return _earnings_row(db, symbol), _estimate_trend(db, symbol)
        finally:
            db.close()

    def _safe(fn, fallback):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return fallback

    import singleflight

    # Not a ``with`` block: the context manager joins every worker on exit, so
    # the deadline below would be decorative -- the request would still sit
    # there until the slowest provider finished.
    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="earn")
    late: list[str] = []
    try:
        # Each fetch runs under its own flight key, so two requests for the
        # same symbol share one fetch instead of racing. Abandoning a slow
        # provider only helps if the next request joins the work already
        # running rather than starting a second copy of it.
        starts = {
            "quote": lambda: _safe(lambda: market.get_quote(symbol), {}),
            "chart": lambda: _safe(
                lambda: market.get_chart(symbol, chart_range), {}),
            "score": lambda: _safe(lambda: get_trdgo_score(symbol), {}),
            "chain": lambda: _safe(lambda: options.load_chain(symbol), {}),
            "rows": lambda: _safe(_db_rows, (None, None)),
            "history": lambda: _safe(
                lambda: earnings_intel.get_history(symbol, quarters=8),
                {"quarters": [], "stats": {}}),
        }
        jobs = {
            name: pool.submit(
                singleflight.call, f"overview:{name}:{symbol}:{chart_range}",
                fn, OVERVIEW_BUDGET)
            for name, fn in starts.items()
        }
        deadline = time.time() + OVERVIEW_BUDGET + 1.0

        def settle(name: str, fallback):
            """Whatever arrived in time; the fallback and a note if not."""
            try:
                value, done = jobs[name].result(
                    timeout=max(deadline - time.time(), 0.1))
                if not done or value is None:
                    late.append(name)
                    return fallback
                return value
            except FuturesTimeout:
                late.append(name)
                return fallback
            except Exception:  # noqa: BLE001 - one panel, not the page
                late.append(name)
                return fallback

        quote = settle("quote", {"status": "PENDING"})
        chart = settle("chart", {"status": "PENDING", "bars": []})
        # An explicit pending shape, not an empty dict: the panels read these
        # keys, and "not yet" has to look different from "scored zero".
        score = settle("score", {
            "status": "PENDING", "final": {}, "components": {},
            "confidence": {}, "risk": {}, "providers": {},
        })
        chain = settle("chain", {"status": "PENDING"})
        earnings, estimates = settle("rows", (None, None))
        history_payload = settle("history", {"quarters": [], "stats": {}})
    finally:
        # The work keeps running in the background, so the next request finds
        # it cached rather than starting again from cold.
        pool.shutdown(wait=False)

    if chain.get("status") == "OK":
        # These two depend on the chain, so they follow it -- but they run
        # against each other rather than in sequence.
        opt_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="earnopt")
        try:
            iv_job = opt_pool.submit(_safe, lambda: options.get_iv_history(symbol), {})
            rm_job = opt_pool.submit(
                _safe,
                lambda: options.get_realized_move(
                    symbol, int(chain.get("dte") or 7) or 7), {})
            opt_deadline = time.time() + OVERVIEW_BUDGET / 2
            try:
                iv_history = iv_job.result(
                    timeout=max(opt_deadline - time.time(), 0.1))
            except Exception:  # noqa: BLE001
                iv_history, _ = {}, late.append("iv_history")
            try:
                realized = rm_job.result(
                    timeout=max(opt_deadline - time.time(), 0.1))
            except Exception:  # noqa: BLE001
                realized, _ = {}, late.append("realized_move")
        finally:
            opt_pool.shutdown(wait=False)
        opt_metrics = opt_analytics.get_metrics(chain, iv_history, realized)
        risk_zones = opt_analytics.get_risk_zones(chain)
    else:
        opt_metrics = {"status": chain.get("status"), "error": chain.get("error")}
        risk_zones = {"status": chain.get("status")}

    # Read defensively. When the history provider cannot answer -- rate
    # limited, not configured, a symbol it does not carry -- it returns a
    # payload with a status and no statistics. Indexing that dict raised
    # KeyError inside a request handler, which the browser received as a 500
    # and rendered as an entirely blank page: no history, and no earnings
    # screen either. A missing figure is a missing figure; it is not a
    # reason to lose the chart, the chain and the score alongside it.
    stats = history_payload.get("stats") or {}
    history = {
        "quarters": history_payload.get("quarters") or [],
        "beat_rate": stats.get("beat_rate"),
        "average_surprise": stats.get("average_surprise"),
        "consecutive_beats": stats.get("consecutive_beats"),
        "average_move": stats.get("average_move"),
        "median_move": stats.get("median_move"),
        "largest_move": stats.get("largest_move"),
        "sample_size": stats.get("sample_size"),
        "basis": stats.get("basis"),
        "basis_detail": stats.get("detail") or history_payload.get("detail"),
        "verified_count": history_payload.get("verified_count", 0),
        "seed_count": history_payload.get("seed_count", 0),
        "provider": history_payload.get("provider"),
        "status": history_payload.get("status", "DATA_UNAVAILABLE"),
    }
    lifecycle = earnings_intel.get_event_lifecycle(symbol)

    components = score.get("components", {})
    final = score.get("final", {})
    bullish, bearish = _collect_reasons(components, opt_metrics)

    # Alpha Vantage takes over the estimates panel once it is configured.
    import alpha_vantage_estimates_service as av_estimates
    import provider_config as pcfg

    revisions = (
        av_estimates.get_revisions(symbol)
        if pcfg.ALPHA_VANTAGE.configured
        else {
            "symbol": symbol, "rows": [], "horizons_available": [],
            **pcfg.ALPHA_VANTAGE.status(),
        }
    )

    analysis = build_analysis(
        {
            "symbol": symbol,
            "components": components,
            "direction_score": final.get("direction_score"),
            "decision": final.get("decision"),
            "confidence_score": final.get("confidence_score"),
            "risk": score.get("risk"),
            "expected_move": {
                "percent": opt_metrics.get("expected_move_percent"),
                "dollars": opt_metrics.get("expected_move_dollars"),
                "range": opt_metrics.get("expected_range"),
            },
            "bullish_reasons": bullish,
            "bearish_reasons": bearish,
            "warnings": final.get("warnings") or [],
            "data_quality": score.get("confidence"),
            "provider_status": score.get("providers"),
        }
    )

    result = {
        "symbol": symbol,
        "quote": quote,
        "chart": chart,
        "score": score,
        "options": opt_metrics,
        "risk_zones": risk_zones,
        "earnings": earnings,
        "estimates": estimates,
        "revisions": revisions,
        "lifecycle": lifecycle,
        "history": history,
        "analysis": analysis,
        "market": market.market_clock(),
        "status": "OK",
        "source": "IBKR+SEC+DATABASE",
    }

    # Say which panels did not arrive in time, so the screen can mark them as
    # still loading rather than as empty -- "no data" and "not yet" are
    # different answers and only one of them is true here.
    if late:
        result["pending_panels"] = sorted(set(late))
        result["status"] = "PARTIAL"

    if late:
        # A partial page gets a short window only. Caching it for the full one
        # would freeze an incomplete screen for the next quarter of an hour
        # over a single slow moment, and the work that missed the deadline is
        # still running -- the next request will find it finished.
        market.cache.put(key, result)
        market.cache.put(f"{key}:partial-until",
                         time.time() + PARTIAL_TTL)
    else:
        market.cache.put(key, result)
    return result
