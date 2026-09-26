"""
HTTP surface for the US-Stock Reader dashboard.

Additive by design: the historical /market/* and /companies/* endpoints in
main.py are untouched. Everything the React app consumes lives under /api and
carries a ``status`` plus a ``source`` so the UI can always distinguish real
data from an unavailable provider.
"""

from __future__ import annotations

import swr
import time
import input_validation as validate
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Optional

# Wall-clock ceiling for the calendar's detail panel. Well inside the proxy's
# limit: a row click that takes a minute is a row click nobody makes twice.
BRIEF_BUDGET = 20.0

from fastapi import APIRouter, Body, Query, Request
from sqlalchemy import text

import ai_insights_service as insights
import earnings_intelligence_service as earnings_intel
import provider_config as pcfg
import backtest_service as backtests
import earnings_calendar_service as calendar
import uw_scanner_service as scanner
import live_market_service as market
import live_options_analytics as opt_analytics
import live_options_service as options
import live_score_service as scores
import market_overview_service as overview
import provider_health as health
import symbol_service as symbols
import workspace_service as workspace
from database import engine

router = APIRouter(prefix="/api", tags=["dashboard"])


DEFAULT_STRIP = [
    "NVDA", "ORCL", "ADBE", "GME", "AVGO",
    "AMZN", "AAPL", "TSLA", "META", "AMD",
]


# ---------------------------------------------------------------------------
# health / status
# ---------------------------------------------------------------------------


@router.get("/status")
def status() -> dict:
    """Lightweight badge status."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db = {"connected": True, "error": None}
    except Exception:  # noqa: BLE001
        # The badge needs connected/not; the exception text names the DB host
        # and user, so it is logged rather than returned.
        import logging
        logging.getLogger(__name__).exception("status: database unreachable")
        db = {"connected": False, "error": "database unreachable"}

    # "live" used to mean "the TWS socket is open". It now means the market
    # feed is configured and answering, which is what the badge was really
    # telling anyone: whether the numbers on screen are coming from anywhere.
    import unusualwhales_service as uw

    feed = uw.provider_status()
    return {
        "feed": feed,
        "database": db,
        "market": market.market_clock(),
        "live": bool(feed.get("configured")) and not feed.get("blocked"),
    }


@router.get("/health")
def provider_matrix(deep: bool = False) -> dict:
    """Full provider matrix used by Settings and the Dashboard."""
    return health.get_health(deep=deep)


# ---------------------------------------------------------------------------
# symbols
# ---------------------------------------------------------------------------


@router.get("/symbols/search")
def symbol_search(q: str = Query(""), limit: int = 12) -> dict:
    return symbols.search(q, limit)


@router.get("/symbols/validate/{symbol}")
def symbol_validate(symbol: str) -> dict:
    return symbols.validate(symbol)


# ---------------------------------------------------------------------------
# quotes / charts / indices
# ---------------------------------------------------------------------------


@router.get("/quote/{symbol}")
def quote(symbol: str) -> dict:
    return market.get_quote(symbol)


@router.get("/chart/{symbol}")
def chart(
    symbol: str,
    range: str = Query("6M", description="1D 5D 1M 3M 6M YTD 1Y 5Y ALL"),
) -> dict:
    return market.get_chart(symbol, range)


@router.get("/indices")
def indices() -> dict:
    # Served stale-while-revalidate: the strip is on every page, and a poll
    # that lands the moment the cache expired should get the last answer at
    # once, not wait out a rebuild.
    return swr.serve("indices", market.get_indices, 120)


@router.get("/market/pulse")
def market_pulse() -> dict:
    """Indices, sectors, breadth, movers, macro proxies and sentiment."""
    import market_pulse_service as pulse

    return swr.serve("pulse", pulse.get_pulse, 180)


@router.get("/market/overview")
def market_overview() -> dict:
    # Stale-while-revalidate: the Market Overview build fans out to every
    # index and sector, so a synchronous rebuild on expiry blocked the page
    # for the length of that fan-out. Now the page gets the held answer
    # instantly and the refresh happens behind it.
    return swr.serve("market_overview", overview.get_market_overview, 180)


# ---------------------------------------------------------------------------
# ticker strip / scores
# ---------------------------------------------------------------------------


@router.get("/tickers/strip")
def ticker_strip(symbols: Optional[str] = None) -> dict:
    """
    The scored ticker strip on the Earnings screen.

    Defaults to the standard basket; the persisted watchlist takes over once
    the operator has saved symbols of their own.
    """
    if symbols:
        requested = validate.clean_symbols(symbols, limit=10)
    else:
        saved = [r["symbol"] for r in
                 workspace.list_watchlist(with_quotes=False)["rows"]]
        # Saved symbols lead, but a one-name watchlist should not leave the
        # strip almost empty - top it up from the default basket.
        requested = saved + [s for s in DEFAULT_STRIP if s not in saved]
    return scores.get_watchlist(requested[:10])


@router.get("/score/{symbol}")
def score(symbol: str) -> dict:
    # SWR: after the first build, serve the held score instantly and refresh in
    # the background, so a visitor never waits out the periodic recompute.
    sym = symbol.upper()
    return swr.serve(f"score:{sym}", lambda: scores.get_trdgo_score(sym), 120)


@router.get("/earnings/overview/{symbol}")
def earnings_overview(symbol: str, range: str = "6M") -> dict:
    sym = symbol.upper()
    return swr.serve(f"earnov:{sym}:{range}",
                     lambda: scores.get_earnings_overview(sym, chart_range=range), 180)


# ---------------------------------------------------------------------------
# earnings calendar
# ---------------------------------------------------------------------------


@router.get("/earnings/calendar")
def earnings_calendar(
    range: str = Query("THIS_WEEK"),
    sort: str = Query("DATE"),
    q: Optional[str] = None,
    limit: int = 60,
) -> dict:
    return calendar.get_calendar(range_key=range, sort=sort, query=q, limit=limit)


@router.get("/earnings/preview/{symbol}")
def earnings_preview(symbol: str) -> dict:
    """
    What the options market expects from this company's next report.

    The expected move beside what the stock has actually done on its last
    reports: the comparison is the reading, not either figure alone.
    """
    import uw_earnings_calendar as uwcal

    return swr.serve(f"earnprev:{symbol.upper()}",
                     lambda: uwcal.preview(symbol), 1800)


@router.get("/earnings/upcoming")
def earnings_upcoming(days: int = 14, start: str = "") -> dict:
    """Every scheduled report in a window, with the move priced into each."""
    import uw_earnings_calendar as uwcal

    return swr.serve(f"earnup:{start}:{days}",
                     lambda: uwcal.calendar(start=start or None, days=days), 900)


@router.get("/earnings/calendar/context")
def earnings_calendar_context() -> dict:
    """Counts, sector mix, beat rate and post-earnings moves for the screen."""
    return swr.serve("earnings:context", calendar.get_context, 600)


@router.get("/earnings/brief/{symbol}")
def earnings_brief(symbol: str) -> dict:
    """
    Everything the calendar's detail panel shows for one company.

    Gathered concurrently: the analyst feed and the earnings history are
    independent providers, and running them in series made selecting a row on
    the calendar feel slower than opening the full analysis screen.
    """
    import analyst_consensus_service as consensus

    symbol = symbol.upper()
    # Not a ``with`` block: the context manager joins its workers on exit, so a
    # provider that ignores the deadline below would still hold the request
    # open. The panel would rather show one section as unavailable than hang.
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        analysts = pool.submit(consensus.get_consensus, symbol)
        history = pool.submit(earnings_intel.get_history, symbol, 8)
        deadline = time.time() + BRIEF_BUDGET

        def settle(future, label: str) -> dict:
            try:
                return future.result(timeout=max(deadline - time.time(), 0.1))
            except FuturesTimeout:
                return {"status": "SLOW_PROVIDER",
                        "detail": (f"{label} did not answer within "
                                   f"{BRIEF_BUDGET:.0f}s.")}
            except Exception as exc:  # noqa: BLE001 - one panel, not the page
                return {"status": "ERROR",
                        "detail": f"{label} failed: {type(exc).__name__}"}

        return {
            "symbol": symbol,
            "analysts": settle(analysts, "Analyst consensus"),
            "history": settle(history, "Earnings history"),
        }
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


@router.get("/earnings/calendar/source")
def earnings_calendar_source() -> dict:
    return calendar.calendar_source_status()


@router.get("/earnings/history/{symbol}")
def earnings_history(symbol: str, quarters: int = 8) -> dict:
    """Previous earnings plus statistics from verified rows only."""
    return earnings_intel.get_history(symbol, quarters)


@router.get("/earnings/lifecycle/{symbol}")
def earnings_lifecycle(symbol: str) -> dict:
    return earnings_intel.get_event_lifecycle(symbol)


@router.get("/estimates/revisions/{symbol}")
def estimate_revisions(symbol: str) -> dict:
    """
    Which way the estimate for the next quarter is moving.

    The current estimate and how many analysts moved it in the last week.

    The snapshot comparison this screen was built around -- stored estimates
    7, 30, 60 and 90 days apart -- came from Alpha Vantage and went with it.
    That was the better measurement and it is worth saying so: this one is a
    single week's revision count, and the payload says which it is.
    """
    import uw_company_service as uwc

    return uwc.estimate_revisions(symbol)


# ---------------------------------------------------------------------------
# external providers
# ---------------------------------------------------------------------------


@router.get("/providers")
def providers(request: Request) -> dict:
    """Configuration state of every external provider. Never returns a key.

    Admin only: this is the provider settings panel, not something a handed-out
    user account should see.
    """
    import auth_service as auth
    auth.require_admin(request)
    import unusualwhales_service as uw

    # provider_matrix() only knows whether a key is configured. The per-service
    # status also weighs the last fetch, so a rejected key reports as rejected
    # instead of a green OK; let that win where they overlap.
    matrix = pcfg.provider_matrix()
    # Not in the shared matrix: its key is read from the environment directly
    # rather than through a ProviderSpec, so it reports for itself.
    matrix["UNUSUAL_WHALES_API_KEY"] = uw.provider_status()

    return {"providers": matrix, "status": "OK"}


@router.get("/directional/{symbol}")
def directional(symbol: str, horizon: Optional[str] = None) -> dict:
    """
    Direction and confidence as two separate numbers, every parameter
    carrying the rule and the raw figures behind its score.

    ``horizon`` is TODAY, TOMORROW or SWING. Left out, the original swing read
    is returned unchanged, so nothing that already calls this moves.
    """
    import directional_score_service as ds

    # SWR the base read so the panel never blocks on the periodic recompute; the
    # horizon transform below is cheap and applied to whatever it serves.
    sym = symbol.upper()
    base = swr.serve(f"dir:{sym}", lambda: ds.get_directional_score(sym), 120)
    if base.get("status") == "UNKNOWN_SYMBOL":
        return base
    h = (horizon or "").upper()
    if h in ("TODAY", "TOMORROW"):
        import horizon_model

        return horizon_model.score_horizon(symbol, h, base=base)
    if h == "SWING":
        return {**base, "horizon": "SWING"}
    return base


@router.get("/events/{symbol}")
def event_radar(symbol: str) -> dict:
    """Analyst actions, news tone and earnings proximity for one symbol."""
    import event_radar_service as er

    return er.get_event_radar(symbol)


# ---------------------------------------------------------------------------
# score snapshots -- the record that will eventually validate the weights
# ---------------------------------------------------------------------------


@router.get("/snapshots/status")
def snapshot_status() -> dict:
    import snapshot_service as snap

    return snap.status()


@router.post("/snapshots/capture")
def snapshot_capture(payload: dict = Body(default={})) -> dict:
    """
    Store today's reading for a set of symbols.

    Defaults to the watchlist, then the standard basket, because the record is
    only useful for symbols someone actually looks at.
    """
    import snapshot_service as snap

    symbols = payload.get("symbols")
    if not symbols:
        saved = [r["symbol"] for r in
                 workspace.list_watchlist(with_quotes=False)["rows"]]
        symbols = saved + [s for s in DEFAULT_STRIP if s not in saved]
    return snap.capture([s.upper() for s in symbols][:25])


@router.post("/snapshots/fill")
def snapshot_fill(payload: dict = Body(default={})) -> dict:
    """Record what happened after snapshots old enough to have an outcome."""
    import snapshot_service as snap

    return snap.fill_forward_returns(limit=int(payload.get("limit") or 200))


@router.get("/snapshots/evaluate")
def snapshot_evaluate(horizon: int = 10) -> dict:
    """Per-parameter hit rate and edge against the base rate."""
    import snapshot_service as snap

    return snap.evaluate_parameters(horizon=horizon)


@router.get("/analyze/{symbol}/stream")
def analyze_stream(symbol: str, refresh: bool = False,
                   horizon: Optional[str] = None):
    """
    Stream the analysis as each provider answers.

    Server-sent events rather than one slow response: a cold symbol takes
    thirty to ninety seconds and a spinner for that long looks broken.

    ``refresh=true`` re-reads every provider instead of using what is cached,
    which is what a deliberate press of Analyse is asking for.
    """
    from fastapi.responses import StreamingResponse

    import analysis_stream_service as stream

    return StreamingResponse(
        stream.stream_analysis(symbol, refresh=refresh,
                               horizon=(horizon or "SWING").upper()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Proxies that buffer would defeat the point of streaming.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# stored calls: every buy/sell the app made, with the working behind it
# ---------------------------------------------------------------------------


@router.get("/calls/scorecard")
def calls_scorecard(horizon: Optional[str] = None, days: int = 30) -> dict:
    """How often stored calls were right, by decision and by score band."""
    import calls_service

    return calls_service.scorecard((horizon or "").upper() or None, days)


@router.post("/calls/evaluate")
def calls_evaluate() -> dict:
    """Fill in outcomes for calls whose session has closed."""
    import calls_service

    return calls_service.evaluate_pending()


@router.get("/calls/history")
def calls_history(symbol: Optional[str] = None, horizon: Optional[str] = None,
                  limit: int = 50) -> dict:
    import calls_service

    rows = calls_service.history(symbol, (horizon or "").upper() or None, limit)
    return {"status": "OK", "count": len(rows), "calls": rows}


@router.get("/calls/latest/{symbol}")
def calls_latest(symbol: str, horizon: Optional[str] = None) -> dict:
    """The most recent stored call for a symbol, with why it was made."""
    import calls_service

    row = calls_service.latest(symbol, (horizon or "").upper() or None)
    return ({"status": "OK", "call": row} if row
            else {"status": "NO_DATA", "call": None})


@router.post("/calls/{call_id}/explain")
def calls_explain(call_id: int) -> dict:
    """
    Plain-English reasons for a stored call, written by Claude from the
    call's own figures, plus the tone of the symbol's recent headlines.
    """
    import calls_service

    return calls_service.explain(call_id)


@router.get("/calls/{call_id}")
def calls_get(call_id: int) -> dict:
    import calls_service

    row = calls_service.get(call_id)
    return ({"status": "OK", "call": row} if row
            else {"status": "NO_DATA", "call": None})


@router.get("/ai-trade/board")
def ai_trade_board(limit: int = 10, refresh: bool = False,
                   horizon: Optional[str] = None) -> dict:
    """Top buy- and sell-rated names, scored by the directional model."""
    import ai_trade_service as board

    # Never blocks. The ranking is composed from scores already in memory, so
    # this is a sort, not a scan; `refresh` asks the background scanner to
    # start another pass rather than making the caller sit through one.
    if refresh:
        board.prewarm()
    return board.get_board(limit=limit, horizon=(horizon or "SWING").upper())


@router.get("/directional-model/weights")
def directional_weights() -> dict:
    """The weight table, so the UI can show what each parameter can score."""
    import directional_model as dm

    return {
        "weights": dm.WEIGHTS,
        "total": dm.TOTAL,
        "directional_weight": dm.DIRECTIONAL_WEIGHT,
        "non_directional": sorted(dm.NON_DIRECTIONAL),
        "insider_split": dm.INSIDER_SPLIT,
        "decisions": list(dm.DECISIONS),
    }


# ---------------------------------------------------------------------------
# price action
# ---------------------------------------------------------------------------


@router.get("/price-action/{symbol}")
def price_action(symbol: str, benchmark: str = "SPY") -> dict:
    """
    Structure, levels, volume, breakout and relative strength from daily bars.

    Pure functions over a bar window, so the identical call runs inside a
    backtest for any historical date.
    """
    import live_market_service as market
    import price_action_service as pa
    from technical_service import calculate_technicals

    bars, source = market._fallback_bars(symbol.upper(), "1 Y")
    if not bars:
        return {"symbol": symbol.upper(), "status": "NO_DATA",
                "source": source or "PROVIDER"}

    bench_bars: list = []
    if benchmark:
        bench_bars, _ = market._fallback_bars(benchmark.upper(), "1 Y")

    analysis = pa.analyse(bars, bench_bars or None)
    technicals = calculate_technicals(bars) or {}
    scored = pa.score_price_action(analysis)

    return {
        "symbol": symbol.upper(),
        "benchmark": benchmark.upper() if benchmark else None,
        **analysis,
        "score": scored,
        "confidence": pa.confidence_inputs(analysis, technicals.get("atr_14")),
        "source": source or "PROVIDER",
    }


# ---------------------------------------------------------------------------
# SEC ownership and insider filings
# ---------------------------------------------------------------------------


@router.get("/sec/insiders/{symbol}")
def sec_insiders(symbol: str, days: int = 180) -> dict:
    """Form 4 activity, with scheduled plans and grants separated out."""
    symbol = validate.clean_symbol(symbol)
    days = validate.clamp_int(days, low=1, high=3650, default=180)
    import uw_ownership_service as own

    return own.insider_transactions_preferred(symbol, days=days)


@router.get("/sec/ownership/{symbol}")
def sec_ownership(symbol: str, days: int = 1460) -> dict:
    """13D (activist) and 13G (passive) stakes above five percent."""
    import sec_filings_service as filings

    return filings.ownership_filings(symbol, days=days)


# ---------------------------------------------------------------------------
# institutional holdings (Form 13F)
# ---------------------------------------------------------------------------


@router.get("/institutional/{symbol}")
def institutional_activity(symbol: str) -> dict:
    """
    Quarter-over-quarter institutional change for one ticker.

    Reads stored holdings only -- ingestion is a separate, deliberate step,
    because a dataset is ninety megabytes and nobody should trigger that by
    typing a ticker.
    """
    # Unusual Whales leads: it returns the current quarter's 13F census on
    # demand, where the SEC path needs a 90 MB dataset ingested first and is
    # only as current as the last ingest. SEC stays as the fallback for when
    # the feed is not configured or has no coverage for a name.
    symbol = validate.clean_symbol(symbol)
    import uw_ownership_service as uw_own

    if uw_own.configured():
        out = uw_own.institutional_activity(symbol)
        if out.get("status") == "OK":
            return out

    import institutional_service as inst

    return inst.get_institutional_activity(symbol)


@router.get("/institutional-ingest/status")
def institutional_ingest_status() -> dict:
    import institutional_service as inst

    return inst.ingest_status()


@router.post("/institutional-ingest/run")
def institutional_ingest_run(payload: dict = Body(default={})) -> dict:
    """
    Load the newest quarterly datasets. Minutes, not seconds -- intended to be
    run once a quarter from Settings, not on a page load.
    """
    import institutional_service as inst

    return inst.ingest_latest(count=int(payload.get("count") or 2))


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


@router.get("/options/overview/{symbol}")
def options_overview(symbol: str) -> dict:
    return opt_analytics.get_overview(symbol)


@router.get("/options/chain/{symbol}")
def options_chain(symbol: str, expiry: Optional[str] = None) -> dict:
    """Full calls|strike|puts ladder for one expiry."""
    chain = options.load_chain(symbol, expiry=expiry)
    table = opt_analytics.get_chain_table(chain)

    # The ladder carries its own quote. The block that stood here overlaid
    # live TWS prices on top, because the chain this page used to read was
    # fifteen minutes behind and carried no bid or ask at all -- so nothing on
    # the page moved. The chain it reads now publishes bid, ask, mid, last,
    # IV and greeks together, from one request, which is what the overlay was
    # reaching for.
    return table


@router.get("/options/expirations/{symbol}")
def options_expirations(symbol: str) -> dict:
    chain = options.load_chain(symbol)
    return {
        "symbol": symbol.upper(),
        "expirations": chain.get("expirations", []),
        "labels": chain.get("expiration_labels", []),
        "selected": chain.get("expiry"),
        "status": chain.get("status"),
    }


@router.get("/options/metrics/{symbol}")
def options_metrics(symbol: str) -> dict:
    chain = options.load_chain(symbol)
    if chain.get("status") != "OK":
        return {"symbol": symbol.upper(), "status": chain.get("status"),
                "error": chain.get("error")}
    return opt_analytics.get_metrics(
        chain,
        options.get_iv_history(symbol),
        options.get_realized_move(symbol, int(chain.get("dte") or 7) or 7),
    )


# ---------------------------------------------------------------------------
# market-wide options flow
# ---------------------------------------------------------------------------


@router.get("/flow/market/summary")
def flow_market_summary() -> dict:
    """Call/put premium, net premium and sentiment across the watched tape."""
    import market_flow_service as mflow

    return swr.serve("flow:summary", mflow.get_summary, 240)


@router.get("/flow/market/tape")
def flow_market_tape(limit: int = 40) -> dict:
    """The largest prints of the session across every watched ticker."""
    import market_flow_service as mflow

    return swr.serve(f"flow:tape:{limit}", lambda: mflow.get_tape(limit=limit), 120)


@router.get("/flow/market/unusual")
def flow_market_unusual(limit: int = 15) -> dict:
    """Contracts trading well above their own open interest."""
    import market_flow_service as mflow

    return swr.serve(f"flow:unusual:{limit}", lambda: mflow.get_unusual(limit=limit), 240)


@router.get("/flow/market/comparison")
def flow_market_comparison() -> dict:
    """Today's premium against the previous session on record."""
    import market_flow_service as mflow

    return swr.serve("flow:comparison", mflow.get_comparison, 240)


@router.get("/flow/market/expiries")
def flow_market_expiries(limit: int = 8) -> dict:
    """Premium by expiration across the watched tape."""
    import market_flow_service as mflow

    return swr.serve(f"flow:expiries:{limit}", lambda: mflow.get_expiry_flow(limit=limit), 600)


@router.get("/flow/market/intraday")
def flow_market_intraday() -> dict:
    """Cumulative premium through the session, for the tile sparklines."""
    import market_flow_service as mflow

    return swr.serve("flow:intraday", mflow.get_intraday, 240)


@router.get("/flow/market/sectors")
def flow_market_sectors() -> dict:
    """Premium grouped by the issuer's sector."""
    import market_flow_service as mflow

    return swr.serve("flow:sectors", mflow.get_sector_flow, 600)


@router.get("/options/levels/{symbol}")
def options_levels(symbol: str, expiry: Optional[str] = None) -> dict:
    """Open interest, gamma, key levels, expected move, Greeks and the score."""
    import options_levels_service as levels

    return levels.get_levels(symbol, expiry)


@router.post("/explain/parameter/{symbol}/{parameter}")
def explain_parameter(symbol: str, parameter: str,
                      horizon: str = "") -> dict:
    """
    What one parameter is actually saying about this company.

    A POST because it may spend a Claude request; the answer is cached for
    the day, so opening the same parameter again costs nothing. The horizon
    says which outlook the reader is looking at, so a session parameter is
    looked up in the outlook that actually scores it.
    """
    import parameter_explainer as explainer

    return explainer.explain(symbol, parameter, horizon=horizon)


@router.get("/screener")
def screener(limit: int = 100, min_marketcap: str = "", min_volume: str = "",
             sector: str = "", order: str = "") -> dict:
    """
    Rank the market: fundamentals with the option tape beside them.

    The app scores a fixed universe of thirty-eight names; this is how a
    thirty-ninth gets found.
    """
    import uw_screener_service as screen

    key = f"screen:{limit}:{min_marketcap}:{min_volume}:{sector}:{order}"
    return swr.serve(key, lambda: screen.screen(
        limit, min_marketcap=min_marketcap, min_volume=min_volume,
        sectors=sector or None, order=order or None), 300)


@router.get("/fundamentals/{symbol}")
def fundamentals(symbol: str) -> dict:
    """One company's reported figures, newest period first."""
    import uw_screener_service as screen

    return swr.serve(f"fundamentals:{symbol.upper()}",
                     lambda: screen.fundamentals(symbol), 6 * 3600)


@router.get("/movers")
def market_movers(limit: int = 25) -> dict:
    """The day's biggest movers."""
    import uw_screener_service as screen

    return swr.serve(f"movers:{limit}", lambda: screen.movers(limit), 300)


@router.get("/shorts/{symbol}")
def short_interest(symbol: str) -> dict:
    """Short interest and days to cover -- the other side of the ownership picture."""
    import uw_screener_service as screen

    return swr.serve(f"shorts:{symbol.upper()}",
                     lambda: screen.short_interest(symbol), 6 * 3600)


# ---------------------------------------------------------------------------
# dark pool and market-wide insider activity
# ---------------------------------------------------------------------------


@router.get("/darkpool/recent")
def darkpool_recent(limit: int = 50, min_premium: float = 0.0) -> dict:
    """The market's latest off-exchange prints, largest first."""
    import uw_darkpool_service as dark

    return swr.serve(f"dp:recent:{limit}:{int(min_premium)}",
                     lambda: dark.recent_prints(limit, min_premium), 30)


@router.get("/darkpool/{symbol}")
def darkpool_symbol(symbol: str, limit: int = 50) -> dict:
    """One symbol's off-exchange prints."""
    import uw_darkpool_service as dark

    return swr.serve(f"dp:{symbol.upper()}:{limit}",
                     lambda: dark.symbol_prints(symbol, limit), 30)


@router.get("/darkpool/{symbol}/levels")
def darkpool_levels(symbol: str, top: int = 12) -> dict:
    """Where the off-exchange volume traded, by price."""
    import uw_darkpool_service as dark

    return swr.serve(f"dplevels:{symbol.upper()}:{top}",
                     lambda: dark.price_levels(symbol, top), 120)


@router.get("/insiders/market")
def insiders_market(days: int = 30) -> dict:
    """Insider buying and selling across the whole market, day by day."""
    import uw_darkpool_service as dark

    return swr.serve(f"ins:market:{days}",
                     lambda: dark.market_insiders(days), 600)


@router.get("/insiders/transactions")
def insiders_transactions(limit: int = 100, buys: bool = False) -> dict:
    """The largest individual insider transactions across the market."""
    limit = validate.clamp_int(limit, low=10, high=300, default=100)
    import uw_ownership_service as own

    return swr.serve(f"ins:txns:{limit}:{int(buys)}",
                     lambda: own.market_insider_transactions(limit, buys_only=buys),
                     300)


@router.get("/insiders/sectors")
def insiders_sectors(limit: int = 10) -> dict:
    """Which sectors insiders are buying, and which they are selling."""
    import uw_darkpool_service as dark

    return swr.serve(f"ins:sectors:{limit}",
                     lambda: dark.insider_sector_board(limit), 900)


@router.get("/providers/usage")
def providers_usage() -> dict:
    """Which provider powers which screen, and what is lost without it."""
    import provider_usage

    return swr.serve("providers:usage", provider_usage.usage, 300)


@router.get("/disparity/{symbol}")
def options_disparity(symbol: str) -> dict:
    """How far the options market sits from its own balance, reading by reading."""
    import disparity_service as disparity

    return swr.serve(f"disparity:{symbol.upper()}",
                     lambda: disparity.get_disparity(symbol), 300)


@router.get("/volatility/{symbol}")
def volatility(symbol: str) -> dict:
    """
    The volatility screen for one stock: IV rank and the implied-vs-realized
    read, a year of both with the IV rank, and the term structure by expiry.
    """
    symbol = validate.clean_symbol(symbol)
    import uw_volatility_service as vol

    return swr.serve(f"vol:{symbol}", lambda: vol.get_volatility(symbol), 600)


@router.get("/flow/unusual/{symbol}")
def symbol_unusual_flow(symbol: str, limit: int = 25) -> dict:
    """Contracts trading far above their own normal volume, for one symbol."""
    import unusualwhales_service as uw

    return swr.serve(f"uwflow:{symbol.upper()}:{limit}",
                     lambda: uw.tape_rows(symbol, limit=limit), 60)


@router.get("/flow/market/regime")
def market_regime() -> dict:
    """Which way the whole market's option premium is leaning today."""
    import uw_flow_service as uwflow

    return swr.serve("uw:regime", uwflow.market_summary, 240)


@router.get("/flow/stock/{symbol}")
def stock_flow(symbol: str, limit: int = 100) -> dict:
    """
    One stock's option tape as it prints, with the provider's own side call.

    Separate from the unusual list above: this is every trade, that is the
    contracts having an abnormal day. The screen shows them side by side.
    """
    import uw_flow_service as uwflow

    return swr.serve(f"uwstock:{symbol.upper()}:{limit}",
                     lambda: uwflow.get_flow(symbol), 30)


@router.get("/flow/expiry/{symbol}")
def stock_flow_by_expiry(symbol: str) -> dict:
    """One stock's option flow split by expiry: near-dated, or further out."""
    import uw_flow_service as uwflow

    return swr.serve(f"uwexp:{symbol.upper()}",
                     lambda: uwflow.flow_by_expiry(symbol), 60)


@router.get("/flow/market/busiest")
def market_busiest(limit: int = 25) -> dict:
    """Where the option market's attention is right now, by transactions."""
    import uw_flow_service as uwflow

    return swr.serve(f"uwbusiest:{limit}",
                     lambda: uwflow.busiest_names(limit), 60)


@router.get("/funds/monthly/{symbol}")
def funds_monthly(symbol: str, months: int = 6) -> dict:
    """Fund ownership month by month, from Form N-PORT."""
    import nport_service as nport

    return swr.serve(f"nport:{symbol.upper()}:{months}",
                     lambda: nport.monthly_flows(symbol, months=months),
                     6 * 3600)


@router.get("/funds/daily/{symbol}")
def funds_daily(symbol: str, days: int = 10) -> dict:
    """Day-by-day change in the shares the sector ETFs hold."""
    import etf_daily_service as etfs

    return swr.serve(f"etfdaily:{symbol.upper()}:{days}",
                     lambda: etfs.daily_flows(symbol, days=days), 3600)


@router.get("/filings/funds-live")
def funds_live(limit: int = 30, symbol: Optional[str] = None) -> dict:
    """13F filings read on the day they are filed, against last quarter."""
    import funds_live_service as funds

    return funds.recent(limit=limit, symbol=symbol)


@router.get("/dividends/{symbol}")
def dividends(symbol: str, limit: int = 8) -> dict:
    """Declared dividends, the yield, and whether the payout is growing."""
    import dividends_service as div

    return swr.serve(f"dividends:{symbol.upper()}:{limit}",
                     lambda: div.get_dividends(symbol, limit=limit), 6 * 3600)


@router.get("/filings/live")
def events_live(limit: int = 40, symbol: Optional[str] = None) -> dict:
    """Filings from watched companies as EDGAR publishes them."""
    import edgar_live_service as live

    return live.recent(limit=limit, symbol=symbol)


@router.get("/filings/corporate/{symbol}")
def corporate_events(symbol: str, days: int = 365, limit: int = 40) -> dict:
    """
    Management changes, mergers, funding and restructuring, as the company
    itself tagged them on its SEC filings.
    """
    import corporate_events_service as events

    return swr.serve(f"corpevents:{symbol.upper()}:{days}:{limit}",
                     lambda: events.get_events(symbol, days=days, limit=limit),
                     3600)


@router.get("/flow/baseline/{symbol}")
def flow_baseline(symbol: str, sessions: int = 20) -> dict:
    """One symbol's option volume today against its own recent normal."""
    import market_flow_service as mflow

    return mflow.get_baseline(symbol, sessions=sessions)


@router.get("/options/flow/{symbol}")
def options_flow(symbol: str, limit: int = 40) -> dict:
    return options.get_flow(symbol, limit=limit)


# ---------------------------------------------------------------------------
# news & sentiment
# ---------------------------------------------------------------------------


def _news_backend():
    """
    The news source.

    One feed. The IBKR wire sat behind it for installs running TWS with a
    news entitlement, and Marketaux between them; both are gone. What went
    with Marketaux was a per-article sentiment model, which is why the tone
    shown now is labelled as this app's own keyword estimate everywhere it
    appears.
    """
    import uw_news_adapter as uwnews

    return uwnews


@router.get("/news/desk")
def news_desk() -> dict:
    """Merged headline feed plus every tally drawn from it."""
    import news_desk_service as desk

    return swr.serve("news:desk", desk.get_desk, 900)


@router.get("/news/providers")
def news_providers() -> dict:
    return _news_backend().provider_status()


@router.get("/news/{symbol}")
def symbol_news(symbol: str, limit: int = 20) -> dict:
    backend = _news_backend()
    getter = getattr(backend, "get_symbol_news", None) or backend.get_news
    return getter(symbol, limit)


@router.get("/sentiment/{symbol}")
def sentiment(symbol: str, limit: int = 20) -> dict:
    return _news_backend().get_sentiment(symbol, limit)


# ---------------------------------------------------------------------------
# insights
# ---------------------------------------------------------------------------


@router.get("/insights/{symbol}")
def ai_insights(symbol: str) -> dict:
    return insights.build_insights(symbol)


# ---------------------------------------------------------------------------
# scanner
# ---------------------------------------------------------------------------


@router.get("/scanner/presets")
def scanner_presets() -> dict:
    return scanner.list_presets()


@router.get("/scanner/run")
def scanner_run(
    preset: str = Query("MOST_ACTIVE"),
    limit: int = 25,
    location: str = "STK.US.MAJOR",
    above_price: Optional[float] = None,
    below_price: Optional[float] = None,
    above_volume: Optional[int] = None,
    technicals: bool = False,
) -> dict:
    return scanner.run_scan(
        preset=preset, limit=limit, location=location,
        above_price=above_price, below_price=below_price,
        above_volume=above_volume, technicals=technicals,
    )


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------


def _who(request: Request) -> Optional[str]:
    """The signed-in username, so a watchlist is scoped to its owner."""
    import auth_service as auth
    user = auth.current_user(request)
    return user.get("username") if user else None


@router.get("/watchlist/view")
def watchlist_view(request: Request) -> dict:
    """Watchlist rows with quotes, sector, market cap and next earnings."""
    import watchlist_view_service as wv

    return wv.get_view(owner=_who(request))


@router.get("/watchlist")
def watchlist_list(request: Request) -> dict:
    return workspace.list_watchlist(owner=_who(request))


@router.post("/watchlist")
def watchlist_add(request: Request, payload: dict = Body(...)) -> dict:
    return workspace.add_watchlist(payload.get("symbol", ""), payload.get("note"),
                                   owner=_who(request))


@router.delete("/watchlist/{symbol}")
def watchlist_remove(request: Request, symbol: str) -> dict:
    return workspace.remove_watchlist(symbol, owner=_who(request))


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------


@router.get("/alerts")
def alerts_list() -> dict:
    return workspace.list_alerts()


@router.post("/alerts")
def alerts_create(payload: dict = Body(...)) -> dict:
    return workspace.create_alert(
        symbol=payload.get("symbol", ""),
        kind=payload.get("kind", ""),
        comparator=payload.get("comparator", ">="),
        threshold=float(payload.get("threshold", 0)),
        note=payload.get("note"),
    )


@router.delete("/alerts/{alert_id}")
def alerts_delete(alert_id: int) -> dict:
    return workspace.delete_alert(alert_id)


@router.patch("/alerts/{alert_id}")
def alerts_toggle(alert_id: int, payload: dict = Body(...)) -> dict:
    return workspace.toggle_alert(alert_id, bool(payload.get("active", True)))


@router.get("/alerts/evaluate")
def alerts_evaluate() -> dict:
    return workspace.evaluate_alerts()


# ---------------------------------------------------------------------------
# trade journal
# ---------------------------------------------------------------------------


@router.get("/journal")
def journal_list(limit: int = 200) -> dict:
    return workspace.list_journal(limit)


@router.post("/journal")
def journal_create(payload: dict = Body(...)) -> dict:
    return workspace.create_journal(payload)


@router.delete("/journal/{entry_id}")
def journal_delete(entry_id: int) -> dict:
    return workspace.delete_journal(entry_id)


# ---------------------------------------------------------------------------
# strategy
# ---------------------------------------------------------------------------


@router.get("/strategy")
def strategy_get() -> dict:
    return workspace.get_strategy()


@router.put("/strategy")
def strategy_update(payload: dict = Body(...)) -> dict:
    return workspace.update_strategy(payload)


@router.post("/strategy/reset")
def strategy_reset() -> dict:
    return workspace.reset_strategy()


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------


@router.post("/backtest")
def backtest(payload: dict = Body(...)) -> dict:
    raw = payload.get("symbols") or []
    if isinstance(raw, str):
        raw = [s for s in raw.split(",")]
    return backtests.run_backtest(
        symbols=raw,
        start=payload.get("start"),
        end=payload.get("end"),
        min_score=float(payload.get("min_score", 70)),
        min_confidence=float(payload.get("min_confidence", 0)),
        direction=payload.get("direction", "LONG"),
        hold_days=int(payload.get("hold_days", 10)),
        stop_pct=float(payload.get("stop_pct", 5)),
        target_pct=float(payload.get("target_pct", 10)),
    )


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


def _dashboard_build() -> dict:
    """
    One call for the landing screen.

    Each block degrades on its own: a provider being down blanks that card
    rather than failing the whole page.
    """
    def safe(fn, fallback):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            return {**fallback, "status": "ERROR", "detail": type(exc).__name__}

    def upcoming_earnings() -> dict:
        """
        This week if anything reports, otherwise the next scheduled events.

        A panel headed "Upcoming Earnings" that goes blank for the eleven weeks
        between reporting seasons is not telling the operator anything. Widen
        rather than show an empty box.
        """
        block = calendar.get_calendar("THIS_WEEK", "DATE", None, True, 8)
        if block.get("rows"):
            return block
        wider = calendar.get_calendar("ALL", "DATE", None, True, 8)
        if wider.get("rows"):
            wider["widened_from"] = "THIS_WEEK"
            wider["range_label"] = "Next Scheduled"
        return wider

    # Six independent blocks. Run one after another they add up to most of the
    # landing screen's load time, and none of them needs another's answer --
    # the time is spent waiting on providers and the database, not computing.
    # One worker per block: with fewer, the last blocks queue behind the
    # others and the page waits for two rounds instead of one. Six concurrent
    # jobs still sits inside the database pool (5 + 3 overflow).
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="dash") as pool:
        provider_job = pool.submit(safe, lambda: health.get_health(False),
                                   {"providers": {}})
        market_job = pool.submit(safe, overview.get_market_overview,
                                 {"indices": [], "sectors": []})
        strip_job = pool.submit(safe, lambda: ticker_strip(None), {"cards": []})
        calendar_job = pool.submit(safe, upcoming_earnings, {"rows": []})
        watch_job = pool.submit(safe, workspace.list_watchlist, {"rows": []})
        alerts_job = pool.submit(safe, workspace.evaluate_alerts,
                                 {"rows": [], "triggered": 0})

        provider = provider_job.result()
        market_block = market_job.result()
        strip = strip_job.result()
        calendar_block = calendar_job.result()
        watch = watch_job.result()
        alerts = alerts_job.result()

    cards = strip.get("cards", [])
    ranked = [c for c in cards if c.get("score") is not None]
    ranked.sort(key=lambda c: c["score"], reverse=True)

    # With a short watchlist the same name would otherwise head both lists.
    # Split the ranking instead of showing one symbol as both the best and the
    # worst setup.
    half = len(ranked) // 2
    bullish = ranked[:5] if len(ranked) >= 4 else ranked[:max(half, 1)]
    bearish = (
        [c for c in reversed(ranked[-5:]) if c not in bullish]
        if len(ranked) >= 4
        else [c for c in reversed(ranked[half:]) if c not in bullish]
    )

    return {
        "providers": provider.get("providers", {}),
        "live": provider.get("live", False),
        "market": market.market_clock(),
        "overview": market_block,
        "bullish": bullish,
        "bearish": bearish,
        "earnings": calendar_block,
        "watchlist": watch,
        "alerts": alerts,
        "score_basis": strip.get("score_basis"),
        # Distinguishes "composites are still warming" from "nothing scored",
        # so the panels can say which rather than both reading as unavailable.
        "scoring": strip.get("scoring"),
        "status": "OK",
    }


# ---------------------------------------------------------------------------
# cache control
# ---------------------------------------------------------------------------


@router.post("/cache/clear")
def clear_cache() -> dict:
    market.cache.clear()
    return {"cleared": True}


@router.get("/dashboard")
def dashboard() -> dict:
    """The landing screen, served from the last build and refreshed behind it."""
    return swr.serve("dashboard", _dashboard_build, 120)


# ---------------------------------------------------------------------------
# admin: user accounts (admin only)
# ---------------------------------------------------------------------------


@router.get("/admin/users")
def admin_list_users(request: Request) -> dict:
    """Every invited account, with its last login. Admin only."""
    import auth_service as auth
    auth.require_admin(request)
    import user_service
    return {"users": user_service.list_users(), "status": "OK"}


@router.post("/admin/users")
def admin_create_user(request: Request, payload: dict = Body(...)) -> dict:
    """Create an account with a username and, optionally, a chosen password."""
    import auth_service as auth
    auth.require_admin(request)
    import user_service
    return user_service.create_user(
        str(payload.get("username") or ""),
        password=(str(payload.get("password")) if payload.get("password") else None))


@router.post("/admin/users/{username}/reset")
def admin_reset_user(request: Request, username: str,
                     payload: dict = Body(default={})) -> dict:
    """Set a new password for a user -- a chosen one, or a generated one."""
    import auth_service as auth
    auth.require_admin(request)
    import user_service
    pw = payload.get("password") if isinstance(payload, dict) else None
    return user_service.reset_password(username, password=(str(pw) if pw else None))


@router.post("/admin/users/{username}/active")
def admin_set_active(request: Request, username: str,
                     payload: dict = Body(default={})) -> dict:
    """Enable or disable an account without deleting it."""
    import auth_service as auth
    auth.require_admin(request)
    import user_service
    return user_service.set_active(username, bool(payload.get("active", True)))


@router.post("/admin/users/{username}/access")
def admin_set_access(request: Request, username: str,
                     payload: dict = Body(default={})) -> dict:
    """Grant or revoke full access (running analysis and changing data)."""
    import auth_service as auth
    auth.require_admin(request)
    import user_service
    return user_service.set_full_access(username, bool(payload.get("full", True)))


@router.get("/admin/provider/key")
def admin_provider_key(request: Request) -> dict:
    """Masked status of the active provider key. Never returns the key itself."""
    import auth_service as auth
    auth.require_admin(request)
    import unusualwhales_service as uw
    b = uw.budget()
    return {
        "configured": uw.configured(),
        "fingerprint": uw.key_fingerprint(),   # e.g. "•••• c082b4", never the key
        "app_left": b.get("app_left"),
        "app_budget": b.get("app_budget"),
        "blocked": uw.provider_status().get("blocked", False),
    }


@router.post("/admin/provider/key")
def admin_set_provider_key(request: Request, payload: dict = Body(...)) -> dict:
    """
    Replace the Unusual Whales API key at runtime, then test it once.

    For when a key hits its limit: paste a different provider key and it takes
    effect immediately, no redeploy. The key is stored and used, never returned.
    """
    import auth_service as auth
    auth.require_admin(request)
    import unusualwhales_service as uw
    # set_api_key verifies the candidate live before persisting and rolls back
    # to the previous key if it does not work, so a bad paste is never kept.
    res = uw.set_api_key(str(payload.get("key") or ""))
    if res.get("status") == "OK" and not res.get("detail"):
        res["detail"] = "Key replaced and verified live."
    return res


@router.delete("/admin/users/{username}")
def admin_delete_user(request: Request, username: str) -> dict:
    import auth_service as auth
    auth.require_admin(request)
    import user_service
    return user_service.delete_user(username)


@router.get("/admin/logins")
def admin_logins(request: Request, limit: int = 50) -> dict:
    """Recent login attempts across all users -- who, when, from where."""
    import auth_service as auth
    auth.require_admin(request)
    import user_service
    return {"events": user_service.recent_logins(limit), "status": "OK"}
