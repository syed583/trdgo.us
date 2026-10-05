import React, { Suspense, lazy, useCallback } from 'react';
import {
  BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate,
  useParams,
} from 'react-router-dom';
import { api, api2 } from './api/client';
import type { HealthPayload, IndicesPayload, WatchlistPayload } from './api/client';
import { useApi } from './hooks/useApi';
import {
  DEMO_EARNINGS, DEMO_INDICES, DEMO_STATUS, DEMO_WATCHLIST, isDemoMode,
} from './demo';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
const AssistantWidget = lazy(() => import('./components/AssistantWidget'));
const EarningsPage = lazy(() => import('./pages/EarningsPage'));
const OptionsFlowPage = lazy(() => import('./pages/OptionsFlowPage'));
const OiBuildupPage = lazy(() => import('./pages/OiBuildupPage'));
const TradePlanPage = lazy(() => import('./pages/TradePlanPage'));
const SignalHistoryPage = lazy(() => import('./pages/SignalHistoryPage'));
const EarningsSignalHistoryPage = lazy(() => import('./pages/EarningsSignalHistoryPage'));
const EarningsEquityPage = lazy(() => import('./pages/EarningsEquityPage'));
const EarningsOptionsPage = lazy(() => import('./pages/EarningsOptionsPage'));
const EarningsTradePage = lazy(() => import('./pages/EarningsTradePage'));
const VolatilityPage = lazy(() => import('./pages/VolatilityPage'));
const ModelPerformancePage = lazy(() => import('./pages/ModelPerformancePage'));
const PeersPage = lazy(() => import('./pages/PeersPage'));
const DarkPoolPage = lazy(() => import('./pages/DarkPoolPage'));
const MarketInsidersPage = lazy(() => import('./pages/MarketInsidersPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const WorkflowPage = lazy(() => import('./pages/WorkflowPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const OverviewPage = lazy(() => import('./pages/OverviewPage'));
const StocksPage = lazy(() => import('./pages/StocksPage'));
const CalendarPage = lazy(() => import('./pages/CalendarPage'));
const ScannerPage = lazy(() => import('./pages/ScannerPage'));
const WatchlistBoardPage = lazy(() => import('./pages/WatchlistBoardPage'));
const MarketPulsePage = lazy(() => import('./pages/MarketPulsePage'));
const NewsPage = lazy(() => import('./pages/NewsPage'));
const NewsDeskPage = lazy(() => import('./pages/NewsDeskPage'));
const AiTradePage = lazy(() => import('./pages/AiTradePage'));
const InsightsPage = lazy(() => import('./pages/InsightsPage'));
const BacktestPage = lazy(() => import('./pages/BacktestPage'));
const workspace = () => import('./pages/WorkspacePages');
const AlertsPage = lazy(() => workspace().then((m) => ({ default: m.AlertsPage })));
const CommunityPage = lazy(() => workspace().then((m) => ({ default: m.CommunityPage })));
const JournalPage = lazy(() => workspace().then((m) => ({ default: m.JournalPage })));
const SettingsPage = lazy(() => workspace().then((m) => ({ default: m.SettingsPage })));
const StrategyPage = lazy(() => workspace().then((m) => ({ default: m.StrategyPage })));
import './styles.css';
import './app-pages.css';
import './earnings-calendar.css';
import './market-flow.css';
import './market-overview.css';
import './watchlist.css';
import './news-desk.css';
import './ai-trade.css';

const DEFAULT_SYMBOL = 'NVDA';

// The pages are split so the first open downloads only one of them. Fetch the
// rest once the browser is idle, so opening any other page later is instant
// instead of a download.
if (typeof window !== 'undefined') {
  window.setTimeout(() => {
    const pages = [
      () => import('./pages/AiTradePage'), () => import('./pages/InsightsPage'),
      () => import('./pages/DashboardPage'), () => import('./pages/OptionsFlowPage'),
      () => import('./pages/MarketPulsePage'), () => import('./pages/WatchlistBoardPage'),
      () => import('./pages/NewsDeskPage'), () => import('./pages/NewsPage'),
      () => import('./pages/CalendarPage'), () => import('./pages/EarningsPage'),
      () => import('./pages/ScannerPage'), () => import('./pages/BacktestPage'),
      () => import('./pages/WorkspacePages'),
    ];
    pages.forEach((load) => { load().catch(() => undefined); });
  }, 1500);
}

/** Sections that swap the sidebar to the options workspace. */
const OPTIONS_ROUTES = [
  '/options-flow', '/options-chain', '/strategy', '/community',
];

/** Sections that carry a symbol in the URL. */
const SYMBOL_SECTIONS =
  /^\/(earnings|earnings-trade|earnings-equity|earnings-options|options-flow|options-chain|oi-buildup|trade-plan|volatility|news|ai-insights|peers|dark-pool|model-performance|dashboard)(\/|$)/;

/**
 * The same sections, capturing the ticker itself.
 *
 * Shell renders <Routes>, so it sits *above* every route match and
 * useParams() there returns {} - the :symbol param belongs to the child
 * route. Reading it from the pathname is what actually tracks the URL;
 * without this the active symbol was pinned to DEFAULT_SYMBOL and clicking a
 * ticker card changed the address bar while every panel kept showing NVDA.
 */
const SYMBOL_IN_PATH =
  /^\/(?:earnings|earnings-trade|earnings-equity|earnings-options|options-flow|options-chain|oi-buildup|trade-plan|volatility|news|ai-insights|peers|dark-pool|model-performance|dashboard)\/([A-Za-z0-9.\-]+)/;

export interface PageContext {
  symbol: string;
  demo: boolean;
  onSymbol: (s: string) => void;
  indices: ReturnType<typeof useApi<IndicesPayload>>;
  strip: ReturnType<typeof useApi<WatchlistPayload>>;
  quote: ReturnType<typeof useApi<any>>;
  search: string;
  // A regular (non-admin) account is view-only: it can read every page but
  // cannot run analysis or change anything. Pages use this to disable actions.
  readOnly: boolean;
}

function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = useParams();

  const demo = isDemoMode();
  // The URL is the single source of truth for the active ticker. Read it from
  // the pathname: useParams() cannot see a child route's params from here.
  const symbol = (
    location.pathname.match(SYMBOL_IN_PATH)?.[1]
    || params.symbol
    || DEFAULT_SYMBOL
  ).toUpperCase();
  const optionsMode = OPTIONS_ROUTES.some((r) => location.pathname.startsWith(r));

  const health = useApi<HealthPayload>(
    (s) => (demo
      ? Promise.resolve({
        providers: {}, live: true, market: DEMO_STATUS.market,
        feed: 'OK', market_data: 'OK', options: 'OK',
      } as HealthPayload)
      : api2.health(false, s)),
    [demo],
    { refreshMs: demo ? undefined : 45_000 },
  );

  const indices = useApi<IndicesPayload>(
    (s) => (demo ? Promise.resolve(DEMO_INDICES) : api.indices(s)),
    [demo],
    { refreshMs: demo ? undefined : 60_000 },
  );

  const strip = useApi<WatchlistPayload>(
    (s) => (demo ? Promise.resolve(DEMO_WATCHLIST) : api.tickerStrip(undefined, s)),
    [demo],
  );

  const quote = useApi(
    (s) => (demo ? Promise.resolve(DEMO_EARNINGS.quote) : api.quote(symbol, s)),
    [symbol, demo],
    { refreshMs: demo ? undefined : 25_000 },
  );

  const refreshAll = useCallback(() => {
    health.refresh();
    indices.refresh();
    strip.refresh();
    quote.refresh();
  }, [health, indices, strip, quote]);

  /** Switching ticker keeps you in the section you are already reading. */
  const onSymbol = useCallback(
    (next: string) => {
      const clean = next.trim().toUpperCase();
      if (!clean) return;
      const match = location.pathname.match(SYMBOL_SECTIONS);
      // Searching from a section that carries a ticker keeps you in it; from
      // anywhere else (Stocks list, Market Overview, Watchlist, admin…) open
      // the stock's Overview rather than defaulting to Earnings Analysis.
      const section = match ? match[1] : 'dashboard';
      navigate(`/${section}/${clean}${location.search}`);
    },
    [navigate, location],
  );

  const me = useApi<any>((s) => (demo ? Promise.resolve({ is_admin: false, authenticated: false }) : api2.me(s)), [demo]);
  const isAdmin = !!me.data?.is_admin;
  // Until the account loads, isAdmin is false -- redirecting on that would bounce
  // an admin off an /admin/* page they opened directly or refreshed. Hold the
  // guard (render nothing) until the check resolves, then decide.
  const authResolving = !demo && me.initialLoading;
  const adminGate = (el: React.ReactNode) =>
    (isAdmin ? el
      : authResolving ? null
        : <Navigate to={`/dashboard${location.search}`} replace />);
  // A signed-in, non-admin account without full access is view-only. While `me`
  // is still loading we do not know yet, so we do not lock the UI prematurely.
  const readOnly = !demo && me.data?.authenticated === true
    && !isAdmin && me.data?.full_access !== true;

  const ctx: PageContext = {
    symbol, demo, onSymbol, indices, strip, quote, search: location.search, readOnly,
  };

  return (
    <div className="app">
      <Sidebar optionsMode={optionsMode} search={location.search} isAdmin={isAdmin} symbol={symbol} />

      <div className="app-main">
        <TopBar
          variant={optionsMode ? 'options' : 'earnings'}
          symbol={symbol}
          onSymbol={onSymbol}
          quote={quote.data}
          health={health.data}
          demo={demo}
          onRefresh={refreshAll}
          refreshing={quote.loading || strip.loading}
          search={location.search}
          isAdmin={isAdmin}
          username={me.data?.username}
        />

        {readOnly && (
          <div className="readonly-banner">
            View-only account — you can browse everything, but running analysis
            and making changes are disabled. Ask the administrator for full access.
          </div>
        )}

        {(() => {
          // Provider-busy alert: only for a stall the user would actually feel.
          // A plan with a per-minute cap (and the unlimited daily plan still has
          // one) brushes that cap in normal bursts and the backend backs off for
          // a second or two -- held data keeps serving and it clears before a
          // read finishes. Flashing a full-width red banner for a 1s pause reads
          // as an outage when nothing is wrong; the small "LIVE · BUSY" chip in
          // the header already carries that. So the banner waits for a sustained
          // block or the daily budget actually running out.
          const feed: any = (health.data as any)?.providers?.feed;
          if (!feed) return null;
          const outOfBudget = feed.status === 'BUDGET_EXHAUSTED'
            || (typeof feed.app_left === 'number' && feed.app_left <= 0);
          const stalled = (feed.blocked === true || feed.status === 'RATE_LIMITED')
            && (feed.blocked_for_seconds ?? 0) >= 4;
          if (!outOfBudget && !stalled) return null;
          const wait = feed.blocked_for_seconds
            ? ` Retrying in about ${Math.ceil(feed.blocked_for_seconds)}s.` : '';
          const why = outOfBudget
            ? "today's data limit is reached"
            : 'the data provider is busy (rate limit)';
          return (
            <div className="feed-banner" role="alert">
              ⚠ Live data is paused — {why}. Prices and new searches may be
              delayed or show “--”; please wait a moment and try again.{wait}
            </div>
          );
        })()}

        {/* Each page is its own download, fetched the first time it opens. */}
        <Suspense fallback={<div className="page" />}>
        <Routes>
          <Route path="/" element={<Navigate to={`${isAdmin ? '/admin/users' : '/dashboard'}${location.search}`} replace />} />
          <Route path="/dashboard" element={<OverviewPage ctx={ctx} />} />
          <Route path="/dashboard/:symbol" element={<OverviewPage ctx={ctx} />} />
          <Route path="/stocks" element={<StocksPage ctx={ctx} />} />

          <Route path="/earnings" element={<Navigate to={`/earnings/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/earnings/:symbol" element={<EarningsPage ctx={ctx} />} />
          <Route path="/earnings-calendar" element={<CalendarPage ctx={ctx} />} />

          <Route path="/scanner" element={<ScannerPage ctx={ctx} />} />
          <Route path="/watchlist" element={<WatchlistBoardPage ctx={ctx} />} />
          <Route path="/market" element={<MarketPulsePage ctx={ctx} />} />

          <Route path="/options-flow" element={<Navigate to={`/options-flow/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/options-flow/:symbol" element={<OptionsFlowPage ctx={ctx} />} />

          <Route path="/oi-buildup" element={<OiBuildupPage ctx={ctx} />} />
          <Route path="/oi-buildup/:symbol" element={<OiBuildupPage ctx={ctx} />} />

          <Route path="/trade-plan" element={<Navigate to={`/trade-plan/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/trade-plan/:symbol" element={<TradePlanPage ctx={ctx} />} />

          {/* Signals now live inside the Trade Plan screen. */}
          <Route path="/signals" element={<Navigate to={`/trade-plan/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/signal-history" element={<SignalHistoryPage ctx={ctx} />} />
          <Route path="/earnings-signals" element={<EarningsSignalHistoryPage ctx={ctx} />} />
          {/* Old split routes now point at the combined grid. */}
          <Route path="/earnings-stock-history"
            element={<Navigate to={`/earnings-signals${location.search}`} replace />} />
          <Route path="/earnings-options-history"
            element={<Navigate to={`/earnings-signals${location.search}`} replace />} />

          <Route path="/earnings-trade" element={<EarningsTradePage ctx={ctx} />} />
          <Route path="/earnings-trade/:symbol" element={<EarningsTradePage ctx={ctx} />} />
          <Route path="/earnings-equity" element={<EarningsEquityPage ctx={ctx} />} />
          <Route path="/earnings-equity/:symbol" element={<EarningsEquityPage ctx={ctx} />} />
          <Route path="/earnings-options" element={<EarningsOptionsPage ctx={ctx} />} />
          <Route path="/earnings-options/:symbol" element={<EarningsOptionsPage ctx={ctx} />} />

          <Route path="/volatility" element={<Navigate to={`/volatility/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/volatility/:symbol" element={<VolatilityPage ctx={ctx} />} />
          <Route path="/model-performance" element={<ModelPerformancePage ctx={ctx} />} />
          <Route path="/model-performance/:symbol" element={<ModelPerformancePage ctx={ctx} />} />
          <Route path="/peers" element={<Navigate to={`/peers/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/peers/:symbol" element={<PeersPage ctx={ctx} />} />
          <Route path="/dark-pool" element={<Navigate to={`/dark-pool/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/dark-pool/:symbol" element={<DarkPoolPage ctx={ctx} />} />
          <Route path="/market-insiders" element={<MarketInsidersPage ctx={ctx} />} />
          {/* The chain lives inside the options page now. Old links still
              work: they land on the same symbol's options screen. */}
          <Route path="/options-chain" element={<Navigate to={`/options-flow/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/options-chain/:symbol" element={<ChainRedirect />} />

          <Route path="/news" element={<NewsDeskPage ctx={ctx} />} />
          <Route path="/news/:symbol" element={<NewsPage ctx={ctx} />} />

          <Route path="/ai-trade" element={<AiTradePage ctx={ctx} />} />
          <Route path="/ai-insights" element={<Navigate to={`/ai-insights/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/ai-insights/:symbol" element={<InsightsPage ctx={ctx} />} />

          <Route path="/backtests" element={<BacktestPage ctx={ctx} />} />
          <Route path="/alerts" element={<AlertsPage ctx={ctx} />} />
          <Route path="/trade-journal" element={<JournalPage ctx={ctx} />} />
          <Route path="/strategy" element={<StrategyPage ctx={ctx} />} />
          <Route path="/settings" element={adminGate(<SettingsPage ctx={ctx} />)} />
          <Route path="/admin/users" element={adminGate(<UsersPage ctx={ctx} />)} />
          <Route path="/admin/workflow" element={adminGate(<WorkflowPage ctx={ctx} />)} />
          <Route path="/community" element={<CommunityPage ctx={ctx} />} />

          <Route path="*" element={<NotFound />} />
        </Routes>
        </Suspense>
      </div>

      {/* The assistant spends the Claude key, so only for signed-in accounts. */}
      {!demo && me.data?.authenticated === true && (
        <Suspense fallback={null}>
          <AssistantWidget symbol={symbol} />
        </Suspense>
      )}
    </div>
  );
}

/** Sends a bookmarked chain URL to the same symbol's options page. */
function ChainRedirect() {
  const location = useLocation();
  const symbol = location.pathname.split('/')[2] || DEFAULT_SYMBOL;
  return <Navigate to={`/options-flow/${symbol}${location.search}`} replace />;
}

function NotFound() {
  const navigate = useNavigate();
  return (
    <div className="soon">
      <h2>Page not found</h2>
      <p>That route does not exist in Trdgo.us.</p>
      <button className="ghost-btn" onClick={() => navigate('/dashboard')}>
        Back to dashboard
      </button>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  );
}
