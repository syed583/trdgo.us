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
const EarningsPage = lazy(() => import('./pages/EarningsPage'));
const OptionsFlowPage = lazy(() => import('./pages/OptionsFlowPage'));
const VolatilityPage = lazy(() => import('./pages/VolatilityPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
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
  /^\/(earnings|options-flow|options-chain|volatility|news|ai-insights)(\/|$)/;

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
  /^\/(?:earnings|options-flow|options-chain|volatility|news|ai-insights)\/([A-Za-z0-9.\-]+)/;

export interface PageContext {
  symbol: string;
  demo: boolean;
  onSymbol: (s: string) => void;
  indices: ReturnType<typeof useApi<IndicesPayload>>;
  strip: ReturnType<typeof useApi<WatchlistPayload>>;
  quote: ReturnType<typeof useApi<any>>;
  search: string;
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
      const section = match ? match[1] : 'earnings';
      navigate(`/${section}/${clean}${location.search}`);
    },
    [navigate, location],
  );

  const me = useApi<any>((s) => (demo ? Promise.resolve({ is_admin: false }) : api2.me(s)), [demo]);
  const isAdmin = !!me.data?.is_admin;

  const ctx: PageContext = {
    symbol, demo, onSymbol, indices, strip, quote, search: location.search,
  };

  return (
    <div className="app">
      <Sidebar optionsMode={optionsMode} search={location.search} isAdmin={isAdmin} />

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
        />

        {/* Each page is its own download, fetched the first time it opens. */}
        <Suspense fallback={<div className="page" />}>
        <Routes>
          <Route path="/" element={<Navigate to={`/dashboard${location.search}`} replace />} />
          <Route path="/dashboard" element={<DashboardPage ctx={ctx} />} />

          <Route path="/earnings" element={<Navigate to={`/earnings/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/earnings/:symbol" element={<EarningsPage ctx={ctx} />} />
          <Route path="/earnings-calendar" element={<CalendarPage ctx={ctx} />} />

          <Route path="/scanner" element={<ScannerPage ctx={ctx} />} />
          <Route path="/watchlist" element={<WatchlistBoardPage ctx={ctx} />} />
          <Route path="/market" element={<MarketPulsePage ctx={ctx} />} />

          <Route path="/options-flow" element={<Navigate to={`/options-flow/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/options-flow/:symbol" element={<OptionsFlowPage ctx={ctx} />} />

          <Route path="/volatility" element={<Navigate to={`/volatility/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/volatility/:symbol" element={<VolatilityPage ctx={ctx} />} />
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
          <Route path="/settings" element={<SettingsPage ctx={ctx} />} />
          <Route path="/admin/users" element={<UsersPage ctx={ctx} />} />
          <Route path="/community" element={<CommunityPage ctx={ctx} />} />

          <Route path="*" element={<NotFound />} />
        </Routes>
        </Suspense>
      </div>
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
      <p>That route does not exist in Tradgo.US.</p>
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
