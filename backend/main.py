from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import engine, get_db

from models import (
    Company,
    EstimateSnapshot,
    FundamentalSnapshot,
    EarningsEvent,
    PriceBar
)

# ---------------------------------------------------------
# SERVICES
# ---------------------------------------------------------

from market_data_service import get_historical_bars
from technical_service import calculate_technicals
from technical_score_service import score_technicals

from earnings_history_service import get_earnings_history
from earnings_history_score_service import score_earnings_history

from sec_fundamental_service import get_clean_fundamentals
from fundamental_score_service import score_fundamentals

from market_environment_service import score_market_environment
from options_score_service import score_options
from options_analytics_service import calculate_options_analytics
from trdgo_score_service import score_trdgo
from analysis_service import build_analysis
from live_score_service import get_trdgo_score
from risk_service import assess_risk
from data_validation_service import validate_symbol_data
from confidence_service import calculate_confidence
from estimate_score_service import score_estimates

from api_routes import router as api_router


# ---------------------------------------------------------
# FASTAPI APP
# ---------------------------------------------------------

app = FastAPI(
    title="US-Stock Reader Market Intelligence API",
    description=(
        "Backend API for US-Stock Reader market analysis "
        "& Earnings Intelligence"
    ),
    version="1.0.0"
)

# The dashboard is served by Vite on a different origin during development,
# so the browser needs an explicit CORS grant to reach these routes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# The app bundle is a megabyte of JavaScript and the JSON payloads are
# repetitive; over the tunnel that is most of a slow first load.
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402


class _GzipExceptStreams(GZipMiddleware):
    """
    Compress everything except the analysis stream.

    Gzip holds bytes back until it has a block worth compressing, which is
    right for a page and wrong for server-sent events: the analysis screen
    stopped at "6 of 12 sources" and sat there, because the remaining events
    were sitting in a compression buffer waiting for more data that only
    arrives when the run finishes. The same request answered in ten seconds
    from a client that did not ask for gzip.
    """

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").endswith("/stream"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(_GzipExceptStreams, minimum_size=1024)


@app.on_event("startup")
def _start_edgar_watcher() -> None:
    """Watch EDGAR's live feed for filings by the companies on the board."""
    import edgar_live_service as live

    live.start()


@app.on_event("startup")
def _start_etf_daily_holdings() -> None:
    """
    Read the sector SPDRs' daily books.

    The only ownership data in the app that is a day old rather than a
    quarter: these funds publish their whole holdings list every evening.
    """
    import etf_daily_service as etfs

    etfs.start()


@app.on_event("startup")
def _start_fund_filing_watcher() -> None:
    """Read 13F filings as funds file them, rather than weeks later."""
    import funds_live_service as funds

    funds.start()


@app.on_event("startup")
def _start_slow_screen_warmer() -> None:
    """
    Build the slowest screens before anyone opens them, then keep them fresh.

    Each is served from its last build (see swr.py); this makes sure there is
    a last build, so no visitor is the one who waits the full minute.
    """
    import os
    import threading
    import time

    import swr

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def warm() -> None:
        import api_routes as r

        time.sleep(20)  # let IBKR connect and the first page requests land
        for fn in (r.dashboard, r.market_pulse, r.flow_market_summary,
                   r.flow_market_tape, r.flow_market_unusual,
                   r.flow_market_intraday, r.flow_market_sectors,
                   r.flow_market_comparison, r.flow_market_expiries,
                   r.news_desk, r.earnings_calendar_context):
            try:
                fn()
            except Exception:  # noqa: BLE001 - warming is best effort
                pass
        swr.start_warmer()

    threading.Thread(target=warm, daemon=True, name="slow-screen-warmer").start()


@app.on_event("startup")
def _warm_default_strip() -> None:
    """
    Start scoring the default ticker basket as soon as the server is up.

    A cold composite needs one SEC fetch per symbol, so without this the first
    visitor watches the cards fill in over several refreshes. Warming happens
    on the existing background pool and never blocks startup or a request.
    """
    try:
        from api_routes import DEFAULT_STRIP
        from live_score_service import warm_scores

        warm_scores(list(DEFAULT_STRIP))
    except Exception:  # noqa: BLE001 - warming must never stop the server
        pass


@app.on_event("startup")
def _start_market_warmer() -> None:
    """
    Hold the TWS connection open across the session.

    The connection is the thing worth pre-warming. At the opening bell TWS is
    servicing every subscription at once and its handshake times out, so a
    connection established then fails -- and the app falls back to a provider
    snapshot dated to the last completed session. One established beforehand
    rides through, because the expensive part is connecting rather than
    staying connected.
    """
    try:
        import market_warmer_service as warmer

        warmer.start()
    except Exception:  # noqa: BLE001 - warming must never stop the server
        pass


@app.on_event("startup")
def _start_snapshot_loop() -> None:
    """
    Capture the day's scores once per session, in the background.

    The record only accumulates if it happens without anyone remembering to
    trigger it, and a validation set with gaps on the days nobody opened the
    app is a biased one -- exactly the quiet days would be missing.

    A day already captured is overwritten rather than duplicated, so restarts
    are harmless.
    """
    import threading

    def loop() -> None:
        import time

        while True:
            try:
                from api_routes import DEFAULT_STRIP
                import snapshot_service as snap
                import workspace_service as workspace

                saved = [r["symbol"] for r in
                         workspace.list_watchlist(with_quotes=False)["rows"]]
                symbols = saved + [s for s in DEFAULT_STRIP if s not in saved]
                snap.capture(symbols[:25])
                # Outcomes for anything now old enough to have one.
                snap.fill_forward_returns()
            except Exception:  # noqa: BLE001 - never take down the server
                pass
            # Once a day is the cadence the record needs; the score itself is
            # cached far more tightly than this.
            time.sleep(6 * 3600)

    # Daemon so it never holds the process open on shutdown. Started after a
    # delay so it does not compete with warming for providers.
    threading.Timer(180.0, lambda: threading.Thread(
        target=loop, daemon=True, name="snapshots").start()).start()


@app.on_event("startup")
def _start_calendar_sync() -> None:
    """
    Refresh the earnings calendar once a day, in the background.

    Companies announce their reporting dates a few weeks ahead, and the
    provider flips ``date_confirmed`` when they do. Nothing in the app was
    asking again after the first fetch, so a date that firmed up last week
    stayed marked as an estimate until somebody triggered a sync by hand --
    which meant the calendar's least certain rows were the ones least likely
    to be corrected.

    The window reaches back a fortnight as well as forward: results land after
    a report, and the same pass that confirms an upcoming date fills in the
    actual for one that has just happened.
    """
    import threading

    def loop() -> None:
        import time
        from datetime import date, timedelta

        while True:
            try:
                import benzinga_earnings_service as benzinga
                import earnings_calendar_service as calendar
                import live_market_service as market

                today = date.today()
                benzinga.sync_calendar(
                    today - timedelta(days=14),
                    today + timedelta(days=200),
                    force=True,
                )
                # The calendar caches aggressively; a fresh sync behind a warm
                # cache would not reach the screen until the TTL expired.
                market.cache.clear()

                # Measure the reaction to anything that has just reported, so
                # the impact panel keeps up with the results the sync brought.
                try:
                    import earnings_impact_service as impact

                    impact.backfill()
                except Exception:  # noqa: BLE001 - optional enrichment
                    pass

                del calendar
            except Exception:  # noqa: BLE001 - never take down the server
                pass
            time.sleep(24 * 3600)

    # Daemon, and started after a delay so the first sync does not compete
    # with warming and the snapshot pass for the same providers.
    threading.Timer(300.0, lambda: threading.Thread(
        target=loop, daemon=True, name="calendar-sync").start()).start()


@app.on_event("startup")
def _start_call_scorecard() -> None:
    """
    Judge stored calls once their session has closed.

    Cheap -- one query for unjudged calls and a daily-bar lookup per symbol --
    so it runs every half hour and does nothing until a close is final.
    """
    import threading

    def loop() -> None:
        import time

        while True:
            try:
                import calls_service

                calls_service.evaluate_pending()
            except Exception:  # noqa: BLE001 - never take down the server
                pass
            time.sleep(1800)

    threading.Timer(60.0, lambda: threading.Thread(
        target=loop, daemon=True, name="call-scorecard").start()).start()


@app.on_event("startup")
def _start_market_pulse_warmer() -> None:
    """
    Keep the market overview built.

    Seventy provider round trips is not something to do on a request thread
    while someone waits. Built on a timer instead, so opening the page is a
    cache read; a visitor who arrives mid-build gets the last one and the
    page fills in.
    """
    import threading

    def loop() -> None:
        import time

        while True:
            try:
                import live_market_service as market
                import market_pulse_service as pulse

                pulse.warm()
                pause = market.session_ttl(
                    pulse.PULSE_TTL_OPEN, pulse.PULSE_TTL_CLOSED)
            except Exception:  # noqa: BLE001 - never take down the server
                pause = 300.0
            time.sleep(max(30.0, pause))

    threading.Timer(10.0, lambda: threading.Thread(
        target=loop, daemon=True, name="market-pulse-warm").start()).start()


@app.on_event("startup")
def _start_ai_trade_board() -> None:
    """
    Keep the AI Trade board scoring, continuously.

    The page polls once a minute, which is only worth doing if something has
    changed in between -- so the scan runs back to back rather than on a long
    timer. Each pass picks up where the last stopped, so every name in the
    universe is rescored on a rolling basis and the ranking moves as the scan
    reaches each one.

    A pass is bounded, and the pause between passes is short but real: it
    keeps a failing provider from being hammered in a tight loop.
    """
    import threading

    def loop() -> None:
        import time

        try:
            import ai_trade_service as board

            # Pick up where the last run of the server left off, so a restart
            # does not show an empty board for the ten minutes it takes the
            # rolling scan to fill one.
            board._load_rows()
        except Exception:  # noqa: BLE001
            pass

        while True:
            try:
                import ai_trade_service as board

                board.get_board(refresh=True)
                # The scanner asks the service how long to wait: the rule
                # depends on the market session and belongs with the scan,
                # not here.
                pause = board.next_pause()
            except Exception:  # noqa: BLE001 - never take down the server
                # Back off on failure rather than spinning on a provider that
                # is refusing every call.
                pause = 120.0
            time.sleep(pause)

    # Started early on purpose: the first pass is the slow one, and doing it
    # before anyone opens the tab is the whole point of having a warmer.
    threading.Timer(20.0, lambda: threading.Thread(
        target=loop, daemon=True, name="ai-trade-board").start()).start()


# ---------------------------------------------------------
# ROOT
# ---------------------------------------------------------

@app.get("/")
def root():
    """
    The dashboard when the frontend is built, otherwise a liveness payload.

    This route predates static hosting and matched ahead of the SPA catch-all,
    so opening the site returned {"status": "running"} instead of the app. The
    JSON is still available at /api/root for scripts that relied on it.
    """
    index = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"
    if index.is_file():
        from fastapi.responses import FileResponse as _FileResponse

        # Same rule as the SPA catch-all: this document names the hashed
        # bundles, so caching it is what makes a browser keep asking for an
        # old build by name.
        return _FileResponse(index, headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
        })
    return {
        "service": "US-Stock Reader",
        "status": "running"
    }


@app.get("/api/root")
def api_root():
    return {
        "service": "US-Stock Reader",
        "status": "running"
    }


# ---------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------

@app.get("/health")
def health_check():
    try:

        with engine.connect() as connection:
            connection.execute(
                text("SELECT 1")
            )

        return {
            "status": "ok",
            "service": "US-Stock Reader",
            "database": "connected"
        }

    except Exception as exc:

        return {
            "status": "error",
            "service": "US-Stock Reader",
            "database": "disconnected",
            "error": str(exc)
        }


# ---------------------------------------------------------
# COMPANY
# ---------------------------------------------------------

@app.get("/companies/{symbol}")
def get_company(
    symbol: str,
    db: Session = Depends(get_db)
):
    company = (
        db.query(Company)
        .filter(
            Company.symbol == symbol.upper()
        )
        .first()
    )

    if not company:
        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )

    return {
        "id": company.id,
        "symbol": company.symbol,
        "company_name": company.company_name,
        "exchange": company.exchange,
        "sector": company.sector,
        "industry": company.industry,
        "currency": company.currency
    }


# ---------------------------------------------------------
# ESTIMATES
# ---------------------------------------------------------

@app.get("/companies/{symbol}/estimates")
def get_estimates(
    symbol: str,
    db: Session = Depends(get_db)
):
    company = (
        db.query(Company)
        .filter(
            Company.symbol == symbol.upper()
        )
        .first()
    )

    if not company:
        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )

    estimates = (
        db.query(EstimateSnapshot)
        .filter(
            EstimateSnapshot.company_id == company.id
        )
        .order_by(
            EstimateSnapshot.snapshot_time.desc()
        )
        .all()
    )

    return {
        "symbol": company.symbol,
        "count": len(estimates),
        "estimates": estimates
    }


# ---------------------------------------------------------
# FUNDAMENTALS FROM DATABASE
# ---------------------------------------------------------

@app.get("/companies/{symbol}/fundamentals")
def get_fundamentals(
    symbol: str,
    db: Session = Depends(get_db)
):
    company = (
        db.query(Company)
        .filter(
            Company.symbol == symbol.upper()
        )
        .first()
    )

    if not company:
        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )

    fundamentals = (
        db.query(FundamentalSnapshot)
        .filter(
            FundamentalSnapshot.company_id == company.id
        )
        .order_by(
            FundamentalSnapshot.snapshot_time.desc()
        )
        .all()
    )

    return {
        "symbol": company.symbol,
        "count": len(fundamentals),
        "fundamentals": fundamentals
    }


# ---------------------------------------------------------
# EARNINGS EVENTS
# ---------------------------------------------------------

@app.get("/companies/{symbol}/earnings")
def get_company_earnings(
    symbol: str,
    db: Session = Depends(get_db)
):
    company = (
        db.query(Company)
        .filter(
            Company.symbol == symbol.upper()
        )
        .first()
    )

    if not company:
        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )

    events = (
        db.query(EarningsEvent)
        .filter(
            EarningsEvent.company_id == company.id
        )
        .order_by(
            EarningsEvent.earnings_date.desc()
        )
        .all()
    )

    return {
        "symbol": company.symbol,
        "count": len(events),
        "earnings": events
    }


# ---------------------------------------------------------
# PRICE BARS FROM DATABASE
# ---------------------------------------------------------

@app.get("/companies/{symbol}/prices")
def get_company_prices(
    symbol: str,
    timeframe: str = "1D",
    db: Session = Depends(get_db)
):
    company = (
        db.query(Company)
        .filter(
            Company.symbol == symbol.upper()
        )
        .first()
    )

    if not company:
        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )

    bars = (
        db.query(PriceBar)
        .filter(
            PriceBar.company_id == company.id,
            PriceBar.timeframe == timeframe
        )
        .order_by(
            PriceBar.timestamp.desc()
        )
        .all()
    )

    return {
        "symbol": company.symbol,
        "timeframe": timeframe,
        "count": len(bars),
        "prices": bars
    }


# ---------------------------------------------------------
# IBKR MARKET HISTORY
# ---------------------------------------------------------

@app.get("/market/history/{symbol}")
def market_history(symbol: str):
    try:

        bars = get_historical_bars(
            symbol.upper()
        )

        return {
            "symbol": symbol.upper(),
            "bars": len(bars),
            "data": bars
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Market data error: {str(exc)}"
        )


# ---------------------------------------------------------
# TECHNICAL ANALYSIS
# ---------------------------------------------------------

@app.get("/market/technicals/{symbol}")
def market_technicals(symbol: str):
    try:

        bars = get_historical_bars(
            symbol.upper()
        )

        technicals = calculate_technicals(
            bars
        )

        return {
            "symbol": symbol.upper(),
            "bars_used": len(bars),
            "technicals": technicals
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Technical analysis error: "
                f"{str(exc)}"
            )
        )


# ---------------------------------------------------------
# TECHNICAL SCORE
# ---------------------------------------------------------

@app.get("/market/technical-score/{symbol}")
def market_technical_score(symbol: str):
    try:

        bars = get_historical_bars(
            symbol.upper()
        )

        technicals = calculate_technicals(
            bars
        )

        score = score_technicals(
            technicals
        )

        return {
            "symbol": symbol.upper(),
            **score
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Technical score error: "
                f"{str(exc)}"
            )
        )


# ---------------------------------------------------------
# SEC FUNDAMENTALS
# ---------------------------------------------------------

@app.get("/market/fundamentals/{symbol}")
def market_fundamentals(symbol: str):
    try:

        fundamentals = get_clean_fundamentals(
            symbol.upper()
        )

        return fundamentals

    except ValueError as exc:

        raise HTTPException(
            status_code=404,
            detail=str(exc)
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"SEC fundamental data error: "
                f"{str(exc)}"
            )
        )


# ---------------------------------------------------------
# FUNDAMENTAL SCORE
# ---------------------------------------------------------

@app.get("/market/fundamental-score/{symbol}")
def market_fundamental_score(symbol: str):
    try:

        fundamentals = get_clean_fundamentals(
            symbol.upper()
        )

        score = score_fundamentals(
            fundamentals
        )

        return {
            "symbol": symbol.upper(),
            **score
        }

    except ValueError as exc:

        raise HTTPException(
            status_code=404,
            detail=str(exc)
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Fundamental score error: "
                f"{str(exc)}"
            )
        )


# ---------------------------------------------------------
# EARNINGS HISTORY
# ---------------------------------------------------------

@app.get("/market/earnings-history/{symbol}")
def market_earnings_history(
    symbol: str,
    db: Session = Depends(get_db)
):
    try:

        history = get_earnings_history(
            db,
            symbol.upper()
        )

        return history

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Earnings history error: "
                f"{str(exc)}"
            )
        )


# ---------------------------------------------------------
# EARNINGS HISTORY SCORE
# ---------------------------------------------------------

@app.get("/market/earnings-history-score/{symbol}")
def market_earnings_history_score(
    symbol: str,
    db: Session = Depends(get_db)
):
    try:

        history = get_earnings_history(
            db,
            symbol.upper()
        )

        score = score_earnings_history(
            history
        )

        return {
            "symbol": symbol.upper(),
            **score
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Earnings history score error: "
                f"{str(exc)}"
            )
        )


# ---------------------------------------------------------
# OPTIONS ANALYTICS
# ---------------------------------------------------------

@app.get("/market/options/{symbol}")
def market_options(symbol: str):
    try:
        return calculate_options_analytics(symbol.upper())
    except ConnectionRefusedError:
        return {
            "symbol": symbol.upper(),
            "status": "IBKR_UNAVAILABLE",
            "provider_status": "IBKR_UNAVAILABLE",
            "warnings": ["IBKR/TWS connection refused; options analytics unavailable"],
            "data_quality": "NO_DATA",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Options analytics error: {str(exc)}"
            )
        )


@app.get("/market/options-score/{symbol}")
def market_options_score(symbol: str):
    try:
        analytics = calculate_options_analytics(symbol.upper())
        score = score_options(analytics)
        return {
            "symbol": symbol.upper(),
            **score,
            "provider_status": str(analytics.get("status") or "UNKNOWN").upper(),
        }
    except ConnectionRefusedError:
        return {
            "symbol": symbol.upper(),
            "score": 0,
            "max_score": 15,
            "bias": "INSUFFICIENT_DATA",
            "confidence": 0,
            "reasons": ["Options provider unavailable"],
            "warnings": ["IBKR/TWS connection refused; options score unavailable"],
            "data_quality": "NO_DATA",
            "provider_status": "IBKR_UNAVAILABLE",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Options score error: {str(exc)}"
            )
        )


@app.get("/market/expected-move/{symbol}")
def market_expected_move(symbol: str):
    try:
        analytics = calculate_options_analytics(symbol.upper())
        return {
            "symbol": symbol.upper(),
            "expected_move": {
                "dollars": analytics.get("expected_move_dollars"),
                "percent": analytics.get("expected_move_percent"),
                "range": analytics.get("expected_range"),
            },
            "status": analytics.get("status", "OK"),
            "provider_status": str(analytics.get("status") or "UNKNOWN").upper(),
        }
    except ConnectionRefusedError:
        return {
            "symbol": symbol.upper(),
            "expected_move": {
                "dollars": None,
                "percent": None,
                "range": {"lower": None, "upper": None},
            },
            "status": "IBKR_UNAVAILABLE",
            "provider_status": "IBKR_UNAVAILABLE",
            "warnings": ["IBKR/TWS connection refused; expected move unavailable"],
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Expected move error: {str(exc)}"
            )
        )


@app.get("/market/score/{symbol}")
def market_final_score(symbol: str):
    """
    Final score for one symbol.

    Delegates to live_score_service.get_trdgo_score, which is the single
    scoring path. This route used to assemble its own components, and the two
    implementations disagreed on the same symbol at the same instant: a
    component with no data (calculate_technicals returns None on empty bars,
    and score_technicals turns that into 0/20 "NO_DATA") was counted here as a
    real zero instead of being excluded, which pushed direction_score down and
    reported a confidence the gated path did not agree with. A closed TWS could
    therefore turn a BUY into a WAIT. One path means one answer.
    """
    try:
        scored = get_trdgo_score(symbol.upper())
        result = dict(scored["final"])
        result["risk"] = scored["risk"]
        result["provider_status"] = scored["providers"]
        return result

    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Final score error: {str(exc)}",
        )


@app.get("/market/analysis/{symbol}")
def market_analysis(symbol: str):
    """
    Full analysis payload, built from the same gated score as /market/score
    and /api/score so all three agree.
    """
    try:
        scored = get_trdgo_score(symbol.upper())
        final = scored["final"]
        components = scored["components"]

        options = components.get("options") or {}
        expected_move = {
            "dollars": options.get("expected_move_dollars"),
            "percent": options.get("expected_move_percent"),
            "range": options.get("expected_range")
                     or {"lower": None, "upper": None},
        }

        return build_analysis({
            "symbol": symbol.upper(),
            "direction_score": final.get("direction_score", 0),
            "decision": final.get("decision", "WAIT"),
            "confidence": final.get("confidence_score", 0),
            "confidence_score": final.get("confidence_score", 0),
            "risk": scored["risk"],
            "expected_move": expected_move,
            "components": components,
            "bullish_reasons": [],
            "bearish_reasons": [],
            "warnings": final.get("warnings", []),
            "data_quality": {
                "status": "PARTIAL" if final.get("missing_components") else "GOOD",
                "confidence": final.get("confidence_score", 0),
            },
            "provider_status": scored["providers"],
        })

    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Analysis error: {str(exc)}",
        )


# ---------------------------------------------------------
# MARKET ENVIRONMENT SCORE
# ---------------------------------------------------------

@app.get("/market/environment-score")
def market_environment_score():
    try:

        result = score_market_environment()

        return result

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Market environment error: "
                f"{str(exc)}"
            )
        )

# ---------------------------------------------------------------------------
# access control + single-origin hosting
# ---------------------------------------------------------------------------

import auth_service as auth
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


@app.middleware("http")
async def _mark_foreground(request: Request, call_next):
    """
    Record that a person is waiting, for as long as this request runs.

    Background scans stand aside while this is non-zero. Registered as
    middleware for the same reason the gate is: a new endpoint is covered
    without anyone remembering to cover it.
    """
    import foreground

    path = request.url.path
    if (not path.startswith("/api")
            or path.startswith(foreground.IGNORE_PREFIXES)):
        return await call_next(request)

    with foreground.request():
        return await call_next(request)


@app.middleware("http")
async def _gate(request: Request, call_next):
    """
    Require a session for everything once ACCESS_PASSWORD is set.

    Applied as middleware rather than per-route so a newly added endpoint is
    protected by default -- forgetting a dependency is exactly how these things
    leak.
    """
    if auth.enabled() and not auth.is_public_path(request.url.path):
        if request.method != "OPTIONS":
            try:
                auth.require_session(request)
            except HTTPException:
                if request.url.path.startswith("/api") or request.url.path.startswith("/market"):
                    return JSONResponse({"detail": "Authentication required"},
                                        status_code=401)
                return FileResponse(_LOGIN_PAGE, status_code=401)
    return await call_next(request)


@app.get("/auth/status")
def auth_status() -> dict:
    return {"required": auth.enabled()}


@app.post("/auth/login")
async def auth_login(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - form posts and empty bodies land here
        form = await request.form()
        body = dict(form)

    if not auth.check_password(str(body.get("password") or "")):
        return JSONResponse({"detail": "Incorrect password"}, status_code=401)

    # The tunnel terminates TLS and forwards plain HTTP to us, so the scheme on
    # this hop is always http -- but the browser sees https and several block a
    # cookie that lacks Secure on a secure page. Trust the forwarded proto so
    # the flag matches what the browser actually experienced.
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    over_https = forwarded == "https" or request.url.scheme == "https"

    response = JSONResponse({"status": "OK"})
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.issue_token(),
        max_age=auth.SESSION_TTL,
        httponly=True,      # not readable by page scripts
        samesite="lax",
        secure=over_https,
        path="/",
    )
    return response


@app.post("/auth/logout")
def auth_logout():
    response = JSONResponse({"status": "OK"})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


# Serve the built frontend from the same origin, so one shared URL is enough
# and the browser's /api calls come straight back here.
_DIST = Path(__file__).parent.parent / "frontend" / "dist"
_LOGIN_PAGE = Path(__file__).parent / "login.html"

class _HashedAssets(StaticFiles):
    """
    Static files with the caching a hashed build actually wants.

    Vite stamps a content hash into every asset filename, so a given URL can
    never change contents -- it is safe to cache for a year. Serving them with
    no Cache-Control at all was the cause of a recurring and genuinely
    confusing failure: a rebuilt frontend would be on the server while the
    browser kept replaying an older bundle, so fixes looked like they had not
    been applied.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if _DIST.is_dir():
    app.mount("/assets", _HashedAssets(directory=_DIST / "assets"),
              name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """Hand every unmatched path to the SPA, which owns its own routing."""
        index = _DIST / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Frontend not built")
        # The entry document names the hashed bundles, so it is the one file
        # that must never be cached -- cache it and the browser keeps asking
        # for last week's assets by name.
        return FileResponse(index, headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
        })

