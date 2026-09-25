"""
Assembles the directional score for one symbol.

Fetches every provider the model needs, converts each reading into a signal
that carries its own explanation, and returns direction and confidence as two
separate numbers.

Kept apart from the earnings composite on purpose: that one answers "how does
this company look going into a report", this one answers "which way is the
stock leaning now". A stock with no earnings due still has a directional
reading, and an earnings-specific signal should not move it.

Providers are fetched concurrently and every one of them is optional. A
provider that fails costs coverage -- and therefore confidence -- rather than
failing the request or quietly scoring neutral.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Optional

import directional_signals as sig
from directional_model import score_direction, volatility_context

# Ceiling for any single provider. The score is worth having with eleven of
# sixteen parameters; it is not worth a minute of waiting for the twelfth.
PROVIDER_BUDGET = 20.0

# The option chain is the exception, because it alone supplies thirteen of the
# sixteen parameters. Timing it out drops coverage from 96% to 26% and turns a
# usable reading into a blocked one -- so a cold chain is worth waiting for in
# a way that no other single provider is.
OVERVIEW_BUDGET = 75.0

CACHE_TTL_OPEN = 120.0
CACHE_TTL_CLOSED = 900.0

# A thin reading is not cached at all, so the next request retries instead of
# being stuck with it. Storing 26% coverage for fifteen minutes means one cold
# fetch blanks the panel for a quarter of an hour.
THIN_COVERAGE_PCT = 70.0

BENCHMARK = "SPY"


# Which analysis-screen stages each fetch satisfies. One provider often
# answers several stages at once -- the option chain alone supplies flow,
# gamma, volume and the greeks -- so the mapping is one to many.
STAGE_MAP: dict[str, list[str]] = {
    "overview": ["options_flow", "gex", "iv_greeks", "volume"],
    "oi": ["gex"],
    "form4": ["sec_filings", "insider"],
    "ownership": ["sec_filings"],
    "bars": ["price_action"],
    "bench": ["relative_strength", "macro"],
    "events": ["news"],
    "institutional": ["institutional"],
    "earnings_history": ["earnings"],
}


def _safe(fn, fallback=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return fallback


def _gather(symbol: str, progress=None) -> dict:
    """Every provider reading the model needs, fetched concurrently."""
    import live_market_service as market
    import live_options_analytics as options
    import uw_flow_service as odflow
    import price_action_service as pa
    import sec_filings_service as filings
    from technical_service import calculate_technicals

    out: dict[str, Any] = {}
    seen: set[str] = set()

    def report(job: str, ok: bool) -> None:
        """Tell the caller which screen stages this job just satisfied."""
        if not progress:
            return
        for stage in STAGE_MAP.get(job, []):
            if stage in seen:
                continue
            seen.add(stage)
            progress(stage, "complete" if ok else "unavailable")

    # Deliberately not a `with` block. Leaving one calls shutdown(wait=True),
    # which waits for every straggler however long it takes -- so the budget
    # below was decorative: a single slow provider held the whole analysis
    # until the stream gave up at two minutes, with two stages still
    # spinning on screen. The pool is shut down without waiting instead, and
    # a straggler finishes into its own cache with nobody watching.
    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="dir")
    try:
        jobs = {
            "overview": pool.submit(_safe, lambda: options.get_overview(symbol), {}),
            "oi": pool.submit(_safe, lambda: odflow.daily_oi_change(symbol), {}),
            "form4": pool.submit(
                _safe,
                lambda: __import__("uw_ownership_service")
                .insider_transactions_preferred(symbol), {}),
            "ownership": pool.submit(
                _safe, lambda: filings.ownership_filings(symbol), {}),
            "bars": pool.submit(
                _safe, lambda: market._fallback_bars(symbol, "1 Y"), ([], None)),
            "bench": pool.submit(
                _safe, lambda: market._fallback_bars(BENCHMARK, "1 Y"), ([], None)),
            "events": pool.submit(
                _safe, lambda: __import__("event_radar_service")
                .get_event_radar(symbol), {}),
            "filings": pool.submit(
                _safe, lambda: __import__("corporate_events_service")
                .get_events(symbol), {}),
            "dividends": pool.submit(
                _safe, lambda: __import__("dividends_service")
                .get_dividends(symbol), {}),
            "disparity": pool.submit(
                _safe, lambda: __import__("disparity_service")
                .get_disparity(symbol), {}),
        }
        # Collected in completion order rather than submission order. Waiting
        # on jobs in the order they were submitted means the first call blocks
        # until it finishes, by which time the rest have finished too -- so
        # every stage appears to land at the same instant. as_completed
        # reports each one when it actually arrives, which is both the honest
        # picture and the useful one.
        pending = dict(jobs)
        deadline = time.time() + OVERVIEW_BUDGET
        try:
            for job in as_completed(list(jobs.values()),
                                    timeout=OVERVIEW_BUDGET):
                name = next(n for n, j in pending.items() if j is job)
                pending.pop(name, None)
                try:
                    out[name] = job.result()
                    report(name, bool(out[name]))
                except Exception:  # noqa: BLE001
                    out[name] = {} if name not in ("bars", "bench") else ([], None)
                    report(name, False)
        except FuturesTimeout:
            pass

        # Anything still outstanding ran past the budget.
        for name, job in pending.items():
            remaining = max(0.0, deadline - time.time())
            try:
                out[name] = job.result(timeout=remaining)
                report(name, bool(out[name]))
            except Exception:  # noqa: BLE001
                out[name] = {} if name not in ("bars", "bench") else ([], None)
                report(name, False)
    finally:
        # cancel_futures clears anything still queued; whatever is already
        # running is left to finish on its own time.
        pool.shutdown(wait=False, cancel_futures=True)

    bars = (out.get("bars") or ([], None))[0]
    bench = (out.get("bench") or ([], None))[0]

    out["technicals"] = calculate_technicals(bars) if bars else None
    out["price_action"] = (
        pa.score_price_action(pa.analyse(bars, bench or None)) if bars else {})

    # 13F is read separately: it is a database lookup rather than a provider
    # call, and it is the one input that is a quarter old, so it must never
    # hold up the rest.
    out["live_filings"] = _safe(
        lambda: __import__("funds_live_service").recent(10, symbol), {})
    out["institutional"] = _safe(
        lambda: __import__("institutional_service")
        .get_institutional_activity(symbol), {})
    report("institutional", (out["institutional"] or {}).get("status") == "OK")

    # Earnings history: Benzinga first because it is already cached in the
    # database and costs no quota, then Alpha Vantage. Benzinga's plan covers
    # a restricted universe -- a full year of its calendar returns 116 events
    # for the whole market, and neither NVDA nor TSLA appears in it at all.
    # Unusual Whales first: it carries every reported quarter with the
    # estimate each was measured against, per ticker, with no calendar window
    # to fall outside of. Finviz and Benzinga stay behind it rather than
    # being removed -- a provider that answers for a company the others do
    # not is worth keeping.
    history = _safe(
        lambda: __import__("uw_company_service").earnings_history(symbol, 8), {})
    out["earnings_history"] = history
    report("earnings_history", (history or {}).get("status") == "OK")

    return out


def build_signals(data: dict) -> list:
    """Turn provider readings into the model's parameters."""
    overview = data.get("overview") or {}
    flow = overview.get("flow") or {}
    if overview.get("unusual") and not flow.get("unusual"):
        flow = {**flow, "unusual": overview.get("unusual")}

    return [
        sig.unusual_activity(flow),
        sig.options_flow(flow),
        sig.insider_activity(data.get("form4") or {},
                             data.get("ownership") or {},
                             data.get("institutional") or {}),
        sig.earnings_results(data.get("earnings_history") or {}),
        sig.event_radar(data.get("events") or {}),
        sig.merger_activity(data.get("filings") or {}),
        sig.funding_activity(data.get("filings") or {}),
        sig.fund_flows(data.get("institutional") or {},
                       data.get("live_filings") or {}),
        sig.dividend_trend(data.get("dividends") or {}),
        sig.disparity(data.get("disparity") or {}),

        sig.implied_volatility(overview.get("metrics") or {}),
        sig.expected_move(overview.get("metrics") or {}),

        sig.ema_trend(data.get("technicals") or {}),
        sig.rsi(data.get("technicals") or {}),
        sig.price_action(data.get("price_action") or {}),

        sig.volume_pcr(overview.get("tiles") or {}),
        sig.oi_positioning(overview.get("tiles") or {}),
        sig.daily_oi_change(data.get("oi") or {}),
        sig.flow_by_expiry(overview.get("expiration_flow") or {}),
        sig.key_levels(overview.get("risk_zones") or {}, overview.get("spot")),
    ]


def _symbol_exists(symbol: str) -> bool:
    """
    Is this a real US listing?

    Answered only to refuse a score, so the benefit of the doubt goes to the
    symbol: if the check itself cannot be made, the answer is yes. Calling a
    real company unknown because a provider was down is the worse mistake of
    the two.
    """
    try:
        import symbol_service as symbols

        out = symbols.validate(symbol)
    except Exception:  # noqa: BLE001
        return True
    if out.get("status") in ("OK", "SYMBOL_NOT_FOUND"):
        return bool(out.get("valid"))
    return True


def get_directional_score(symbol: str, progress=None) -> dict:
    """
    Direction and confidence for one symbol, with every parameter explained.

    Each signal carries the rule that produced it and the numbers that rule
    was applied to, so the UI can answer "why is this a buy" with the actual
    arithmetic rather than a restated label.
    """
    import live_market_service as market

    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"symbol": symbol, "status": "INVALID_SYMBOL"}

    key = f"directional:{symbol}"
    cached = market.cache.get(
        key, market.session_ttl(CACHE_TTL_OPEN, CACHE_TTL_CLOSED))
    if cached:
        # A cached answer still reports every stage, so the screen completes
        # rather than sitting at zero -- it simply completes immediately,
        # which is the honest depiction of cached data.
        if progress:
            for stages in STAGE_MAP.values():
                for stage in stages:
                    progress(stage, "complete", "cached")
        return cached

    data = _gather(symbol, progress)
    signals = build_signals(data)
    scored = score_direction(signals)

    # A score for something that is not a stock is worse than no score. ZZZZZ
    # came back "DO NOT TRADE" with a straight face and one live parameter --
    # "Pays no dividend", which is true of every string that is not a company.
    # Only asked when the reading is empty anyway, so a real symbol in an
    # outage never pays for this.
    if (scored.get("coverage_pct") or 0.0) < THIN_COVERAGE_PCT             and not (data.get("bars") or ([], None))[0]             and not _symbol_exists(symbol):
        return {"symbol": symbol, "status": "UNKNOWN_SYMBOL", "signals": [],
                "detail": f"No US stock listed as {symbol}.",
                "market": market.market_clock(), "model": "GENERAL_DIRECTIONAL"}

    result = {
        "symbol": symbol,
        **scored,
        "volatility_context": volatility_context(signals),
        "market": market.market_clock(),
        # Said plainly: this is a positioning read, not an earnings view, and
        # the two live in different models.
        "model": "GENERAL_DIRECTIONAL",
        "note": ("Separate from the pre- and post-earnings models. "
                 "Volatility parameters carry weight but cast no "
                 "directional vote."),
        "status": "OK" if scored.get("direction_score") is not None
                  else "NO_DATA",
    }
    # Only a well-covered reading is worth keeping. Caching a thin one would
    # freeze a blocked score in place for the whole TTL, when the next request
    # would very likely find the providers warm.
    if (scored.get("coverage_pct") or 0.0) >= THIN_COVERAGE_PCT:
        market.cache.put(key, result)
    return result
