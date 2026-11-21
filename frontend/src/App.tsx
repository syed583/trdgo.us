import React, { Suspense, lazy, useCallback } from 'react';
import { SmartMoveAlertStrip, useSmartMoveAlerts } from './components/SmartMoveAlert';
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
import NewSidebar from './components/NewSidebar';
import NewTopBar from './components/NewTopBar';
import NewStrategyTradesPage from './pages/NewStrategyTradesPage';
import NewPreEarningsPage from './pages/NewPreEarningsPage';
import NewUnusualActivityPage from './pages/NewUnusualActivityPage';
import NewIvCrushPage from './pages/NewIvCrushPage';
import NewSignalHistoryPage from './pages/NewSignalHistoryPage';
import NewForwardScoreboardPage from './pages/NewForwardScoreboardPage';
import NewHistoricalBacktestPage from './pages/NewHistoricalBacktestPage';
import NewStocksPage from './pages/NewStocksPage';
import NewNewsPage from './pages/NewNewsPage';
import DevFigmaPreview, { hasFigmaFrame } from './DevFigmaPreview';
import { resolveTheme, applyTheme, saveTheme } from './lib/theme';
import './roboto.css';   // Roboto 400/500/700, self-hosted, for the redesigned shell only
import './new-shell.css';
import ErrorBoundary from './components/ErrorBoundary';
import { Loading } from './pages/shared';
const NewWatchlistPage = lazy(() => import('./pages/NewWatchlistPage'));
const AssistantWidget = lazy(() => import('./components/AssistantWidget'));
const EarningsPage = lazy(() => import('./pages/EarningsPage'));
const OptionsFlowPage = lazy(() => import('./pages/OptionsFlowPage'));
const OiBuildupPage = lazy(() => import('./pages/OiBuildupPage'));
const TradePlanPage = lazy(() => import('./pages/TradePlanPage'));
const SignalHistoryPage = lazy(() => import('./pages/SignalHistoryPage'));
const EarningsSignalHistoryPage = lazy(() => import('./pages/EarningsSignalHistoryPage'));
const ForwardScoreboardPage = lazy(() => import('./pages/ForwardScoreboardPage'));
const HistoricalBacktestPage = lazy(() => import('./pages/HistoricalBacktestPage'));
const PostEarningsForwardPage = lazy(() => import('./pages/PostEarningsForwardPage'));
const EarningsEquityPage = lazy(() => import('./pages/EarningsEquityPage'));
const EarningsOptionsPage = lazy(() => import('./pages/EarningsOptionsPage'));
const EarningsTradePage = lazy(() => import('./pages/EarningsTradePage'));
const EarningsAgentPage = lazy(() => import('./pages/EarningsAgentPage'));
const EarningsModelPage = lazy(() => import('./pages/EarningsModelPage'));
const AgentModelDetail = lazy(() => import('./pages/AgentModelDetail'));
import { GROK_MODEL, META_MODEL, CHATGPT_MODEL, KIMI_MODEL, GEMINI_MODEL, CLAUDE_PRO_MODEL } from './pages/EarningsModelPage';
const CLAUDE_PRO_DETAIL = { base: '/claude-pro', title: 'Claude Pro', detail: (s: string, sig?: AbortSignal) => api2.claudeProDetail(s, sig) };
const AllModelsPage = lazy(() => import('./pages/AllModelsPage'));
const ModelAccuracyPage = lazy(() => import('./pages/ModelAccuracyPage'));
const IntradayBoardPage = lazy(() => import('./pages/IntradayBoardPage'));
const AllIntradayModelsPage = lazy(() => import('./pages/AllIntradayModelsPage'));
import { META_INTRADAY_CFG, KIMI_INTRADAY_CFG, GEMINI_INTRADAY_CFG } from './pages/intradayBoardConfigs';
const META_INTRADAY_DETAIL = { base: '/meta-intraday', title: 'Agent 2 Intraday', detail: (s: string, sig?: AbortSignal) => api2.metaIntradayDetail(s, sig) };
const GROK_INTRADAY_DETAIL = { base: '/grok-intraday', title: 'Agent 1 Intraday', detail: (s: string, sig?: AbortSignal) => api2.grokIntradayDetail(s, sig) };
const GEMINI_DETAIL = { base: '/gemini', title: 'Agent 4', detail: (s: string, sig?: AbortSignal) => api2.geminiDetail(s, sig) };
const KIMI_DETAIL = { base: '/kimi', title: 'Agent 3', detail: (s: string, sig?: AbortSignal) => api2.kimiDetail(s, sig) };
const GROK_DETAIL = { base: '/grok', title: 'Agent 1', detail: (s: string, sig?: AbortSignal) => api2.grokDetail(s, sig) };
const META_DETAIL = { base: '/meta', title: 'Agent 2', detail: (s: string, sig?: AbortSignal) => api2.metaDetail(s, sig) };
const CHATGPT_DETAIL = { base: '/chatgpt', title: 'Agent 6', detail: (s: string, sig?: AbortSignal) => api2.chatgptDetail(s, sig) };
const MyBrokerPage = lazy(() => import('./pages/MyBrokerPage'));
const CopyTradingPage = lazy(() => import('./pages/CopyTradingPage'));
const OptionScannerPage = lazy(() => import('./pages/OptionScannerPage'));
const UnusualActivityPage = lazy(() => import('./pages/UnusualActivityPage'));
const InstitutionalPage = lazy(() => import('./pages/InstitutionalPage'));
const StrategyTradesPage = lazy(() => import('./pages/StrategyTradesPage'));
const Mt5AccountView = lazy(() => import('./pages/Mt5AutoTradePage').then((m) => ({ default: m.Mt5AccountView })));
const Mt5SymbolsView = lazy(() => import('./pages/Mt5AutoTradePage').then((m) => ({ default: m.Mt5SymbolsView })));
const Mt5AuditView = lazy(() => import('./pages/Mt5AutoTradePage').then((m) => ({ default: m.Mt5AuditView })));
const PreEarningsDirectionPage = lazy(() => import('./pages/PreEarningsDirectionPage'));
const TradgoCallPage = lazy(() => import('./pages/TradgoCallPage'));
const VolatilityPage = lazy(() => import('./pages/VolatilityPage'));
const ModelPerformancePage = lazy(() => import('./pages/ModelPerformancePage'));
const PeersPage = lazy(() => import('./pages/PeersPage'));
const DarkPoolPage = lazy(() => import('./pages/DarkPoolPage'));
const MarketInsidersPage = lazy(() => import('./pages/MarketInsidersPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const AdminDashboardPage = lazy(() => import('./pages/AdminDashboardPage'));
const AdminLoginPage = lazy(() => import('./pages/AdminLoginPage'));
const AuditLogPage = lazy(() => import('./pages/AuditLogPage'));
const WorkflowPage = lazy(() => import('./pages/WorkflowPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const OverviewPage = lazy(() => import('./pages/OverviewPage'));
const StocksPage = lazy(() => import('./pages/StocksPage'));
const CalendarPage = lazy(() => import('./pages/CalendarPage'));
const ScannerPage = lazy(() => import('./pages/ScannerPage'));
const WatchlistBoardPage = lazy(() => import('./pages/WatchlistBoardPage'));
const MarketPulsePage = lazy(() => import('./pages/MarketPulsePage'));
const NewsPage = lazy(() => import('./pages/NewsPage'));
const NewsDeskPage = lazy(() => import('./pages/NewsDeskPage'));
const InsightsPage = lazy(() => import('./pages/InsightsPage'));
const BacktestPage = lazy(() => import('./pages/BacktestPage'));
const SmartMoveEngine20Page = lazy(() => import('./pages/SmartMoveEngine20Page'));
const NewSmartMovePage = lazy(() => import('./pages/NewSmartMovePage'));
const NewMarketOverviewPage = lazy(() => import('./pages/NewMarketOverviewPage'));
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
      () => import('./pages/InsightsPage'),
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
  /^\/(earnings|earnings-trade|earnings-claude|claude-last|claude-new-pz|grok|meta|chatgpt|kimi|gemini|claude-pro|grok-intraday|meta-intraday|kimi-intraday|gemini-intraday|tradgo-call|earnings-equity|earnings-options|options-flow|options-chain|oi-buildup|trade-plan|volatility|news|ai-insights|peers|dark-pool|model-performance|dashboard)(\/|$)/;

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
  /^\/(?:earnings|earnings-trade|earnings-claude|claude-last|claude-new-pz|grok|meta|chatgpt|kimi|gemini|claude-pro|grok-intraday|meta-intraday|kimi-intraday|gemini-intraday|tradgo-call|earnings-equity|earnings-options|options-flow|options-chain|oi-buildup|trade-plan|volatility|news|ai-insights|peers|dark-pool|model-performance|dashboard)\/([A-Za-z0-9.\-]+)/;

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
  // Whether this account is an ADMINISTRATOR, straight from the server. This is NOT the inverse of
  // readOnly: a non-admin with full_access has readOnly false, and so does an account whose `me` request
  // has not loaded yet. Gating admin-only content on !readOnly showed it to both (the Historical snapshot
  // tab, kit G). Anything admin-only reads THIS.
  isAdmin: boolean;
  /** The redesigned shell is on for this account. Read by the Analysis landing, which chooses its design. */
  newUi: boolean;
}

function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = useParams();

  const demo = isDemoMode();

  // Browser connectivity: a global banner so a dropped connection is obvious
  // and distinct from the backend being down (held/cached data stays on screen).
  const [online, setOnline] = React.useState(
    typeof navigator === 'undefined' ? true : navigator.onLine);
  React.useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener('online', up);
    window.addEventListener('offline', down);
    return () => {
      window.removeEventListener('online', up);
      window.removeEventListener('offline', down);
    };
  }, []);

  // Reset scroll to the top on navigation so a new page doesn't open halfway
  // down (scroll position was carrying over from the previous page).
  React.useEffect(() => {
    window.scrollTo(0, 0);
    (document.querySelector('.app-main') as HTMLElement | null)?.scrollTo?.(0, 0);
  }, [location.pathname]);

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
  // Stage 1 of the redesign. The server decides per account (ui_flags.py) and fails closed, so an
  // unreadable flag serves the existing interface rather than a half-finished one.
  // DEV ONLY: ?newui=1 lets the redesign be looked at on a developer's machine, where there is no
  // backend to ask. `import.meta.env.DEV` is replaced with `false` at build time, so this branch is
  // removed from the production bundle entirely - it cannot be reached on the server by any URL.
  const newUi = !!me.data?.new_ui
    || (import.meta.env.DEV && new URLSearchParams(location.search).get('newui') === '1');
  const [theme, setTheme] = React.useState(() => resolveTheme());
  const toggleTheme = React.useCallback(() => {
    setTheme((t) => {
      const next = t === 'light' ? 'dark' : 'light';
      applyTheme(next); saveTheme(next);
      return next;
    });
  }, []);
  const [railOpen, setRailOpen] = React.useState(false);
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
  // MT5 auto-trading pages: the operator only (admin, or the username named in the server's MT5_OPERATOR setting).
  const mt5Operator = !demo && (isAdmin || me.data?.mt5_operator === true);
  // Smart Money Move gate-refusal alerts: operator and admin only (the hook sends nothing for anyone else).
  const smAlerts = useSmartMoveAlerts(mt5Operator);
  const smAlertList: any[] = smAlerts.data?.alerts || [];
  const operatorGate = (el: React.ReactNode) =>
    (mt5Operator ? el
      : authResolving ? null
        : <Navigate to={`/strategy-trades${location.search}`} replace />);

  const ctx: PageContext = {
    symbol, demo, onSymbol, indices, strip, quote, search: location.search, readOnly, isAdmin, newUi,
  };

  // Signed out (and not demo): hand off to the SPA's own login page. The backend
  // serves this shell to anonymous visitors so the phone-OTP login renders;
  // without this they would see an empty authenticated frame.
  if (!demo && me.data && me.data.authenticated === false) {
    // Admin area has its own username+password sign-in; users use phone login.
    const to = location.pathname.startsWith('/admin') ? '/admin/login' : '/login';
    return <Navigate to={`${to}${location.search}`} replace />;
  }

  return (
    <div className={newUi ? 'app app-nu' : 'app'}>
      {newUi ? (
        <NewSidebar isAdmin={isAdmin} mt5Operator={mt5Operator} search={location.search}
                    open={railOpen} onClose={() => setRailOpen(false)} />
      ) : (
      <Sidebar optionsMode={optionsMode} search={location.search} isAdmin={isAdmin} mt5Operator={mt5Operator} symbol={symbol} smAlert={smAlertList.length ? (smAlertList[0].acknowledged ? 'ack' : 'critical') : undefined} />
      )}

      <div className="app-main">
        {newUi ? (
          <NewTopBar displayName={me.data?.display_name} username={me.data?.username}
                     role={me.data?.role} isAdmin={isAdmin}
                     alerts={smAlertList.filter((a: any) => !a.acknowledged).length}
                     theme={theme} onTheme={toggleTheme} onBurger={() => setRailOpen(true)} />
        ) : (
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
          displayName={me.data?.display_name}
        />
        )}

        {mt5Operator && <SmartMoveAlertStrip alerts={smAlertList} />}

        {!online && (
          <div className="offline-banner" role="status">
            You’re offline — showing the last loaded data. We’ll reconnect
            automatically when your connection returns.
          </div>
        )}

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

        {/* Each page is its own download, fetched the first time it opens.
            The ErrorBoundary (keyed by route so navigating clears a crash)
            keeps a render error on one page from blanking the whole app. */}
        <ErrorBoundary key={location.pathname}>
        <Suspense fallback={<div className="page"><Loading /></div>}>
        {/* DEV ONLY: with the redesign flag on, the nine designed pages render their Figma frame so the
            look can be judged inside the real shell. hasFigmaFrame() is false in any production build
            (import.meta.env.DEV compiles to false), so this branch cannot exist on the server. */}
        {newUi && hasFigmaFrame(location.pathname, location.search) ? <DevFigmaPreview /> : (
        <Routes>
          <Route path="/" element={<Navigate to={`${isAdmin ? '/admin' : '/dashboard'}${location.search}`} replace />} />
          {/* The redesigned Dashboard is served ONLY to an account the flag names. Everyone else gets
              the existing Overview page, unchanged - the route, its data and its behaviour are untouched. */}
          <Route path="/dashboard" element={<OverviewPage ctx={ctx} />} />
          <Route path="/dashboard/:symbol" element={<OverviewPage ctx={ctx} />} />
          <Route path="/stocks" element={newUi ? <NewStocksPage ctx={ctx} /> : <StocksPage ctx={ctx} />} />

          <Route path="/earnings" element={<Navigate to={`/earnings/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/earnings/:symbol" element={<EarningsPage ctx={ctx} />} />
          <Route path="/earnings-calendar" element={<CalendarPage ctx={ctx} />} />

          <Route path="/scanner" element={<ScannerPage ctx={ctx} />} />
          {/* New UI only: the classic board is untouched for everyone else. */}
          <Route path="/watchlist" element={newUi ? <NewWatchlistPage ctx={ctx} /> : <WatchlistBoardPage ctx={ctx} />} />
          <Route path="/market" element={newUi ? <NewMarketOverviewPage ctx={ctx} /> : <MarketPulsePage ctx={ctx} />} />

          <Route path="/options-flow" element={<Navigate to={`/options-flow/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/options-flow/:symbol" element={<OptionsFlowPage ctx={ctx} />} />

          <Route path="/oi-buildup" element={<OiBuildupPage ctx={ctx} />} />
          <Route path="/oi-buildup/:symbol" element={<OiBuildupPage ctx={ctx} />} />

          <Route path="/trade-plan" element={<Navigate to={`/trade-plan/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/trade-plan/:symbol" element={<TradePlanPage ctx={ctx} />} />

          {/* Signals now live inside the Trade Plan screen. */}
          <Route path="/signals" element={<Navigate to={`/trade-plan/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/signal-history" element={<SignalHistoryPage ctx={ctx} />} />
          <Route path="/smart-move/engine20" element={newUi ? <NewSmartMovePage ctx={ctx} /> : <SmartMoveEngine20Page ctx={ctx} />} />
          <Route path="/earnings-signals" element={newUi ? <NewSignalHistoryPage ctx={ctx} /> : <EarningsSignalHistoryPage ctx={ctx} />} />
          <Route path="/forward-scoreboard" element={newUi ? <NewForwardScoreboardPage ctx={ctx} /> : <ForwardScoreboardPage />} />
          <Route path="/historical-backtest" element={newUi ? <NewHistoricalBacktestPage ctx={ctx} /> : <HistoricalBacktestPage />} />
          <Route path="/post-earnings-forward" element={<PostEarningsForwardPage />} />
          {/* Old split routes now point at the combined grid. */}
          <Route path="/earnings-stock-history"
            element={<Navigate to={`/earnings-signals${location.search}`} replace />} />
          <Route path="/earnings-options-history"
            element={<Navigate to={`/earnings-signals${location.search}`} replace />} />

          <Route path="/tradgo-call" element={<TradgoCallPage ctx={ctx} />} />
          <Route path="/tradgo-call/:symbol" element={<TradgoCallPage ctx={ctx} />} />
          <Route path="/earnings-trade" element={<EarningsTradePage ctx={ctx} />} />
          <Route path="/earnings-trade/:symbol" element={<EarningsTradePage ctx={ctx} />} />
          <Route path="/earnings-claude" element={<EarningsAgentPage ctx={ctx} base="/earnings-claude" profile="plusminus" />} />
          <Route path="/earnings-claude/:symbol" element={<EarningsAgentPage ctx={ctx} base="/earnings-claude" profile="plusminus" />} />
          <Route path="/claude-last" element={<EarningsModelPage ctx={ctx} />} />
          <Route path="/claude-last/:symbol" element={<AgentModelDetail ctx={ctx} />} />
          <Route path="/claude-new-pz" element={<EarningsModelPage ctx={ctx} base="/claude-new-pz" pz />} />
          <Route path="/claude-new-pz/:symbol" element={<AgentModelDetail ctx={ctx} base="/claude-new-pz" pz />} />
          <Route path="/grok" element={<EarningsModelPage ctx={ctx} model={GROK_MODEL} />} />
          <Route path="/grok/:symbol" element={<AgentModelDetail ctx={ctx} model={GROK_DETAIL} />} />
          <Route path="/meta" element={<EarningsModelPage ctx={ctx} model={META_MODEL} />} />
          <Route path="/meta/:symbol" element={<AgentModelDetail ctx={ctx} model={META_DETAIL} />} />
          <Route path="/chatgpt" element={<EarningsModelPage ctx={ctx} model={CHATGPT_MODEL} />} />
          <Route path="/chatgpt/:symbol" element={<AgentModelDetail ctx={ctx} model={CHATGPT_DETAIL} />} />
          <Route path="/kimi" element={<EarningsModelPage ctx={ctx} model={KIMI_MODEL} />} />
          <Route path="/kimi/:symbol" element={<AgentModelDetail ctx={ctx} model={KIMI_DETAIL} />} />
          <Route path="/gemini" element={<EarningsModelPage ctx={ctx} model={GEMINI_MODEL} />} />
          <Route path="/gemini/:symbol" element={<AgentModelDetail ctx={ctx} model={GEMINI_DETAIL} />} />
          <Route path="/claude-pro" element={<EarningsModelPage ctx={ctx} model={CLAUDE_PRO_MODEL} />} />
          <Route path="/claude-pro/:symbol" element={<AgentModelDetail ctx={ctx} model={CLAUDE_PRO_DETAIL} />} />
          <Route path="/all-models" element={<AllModelsPage ctx={ctx} />} />
          <Route path="/model-accuracy" element={<ModelAccuracyPage />} />
          <Route path="/institutional-intel" element={<InstitutionalPage />} />
          {/* The redesigned blotter for an account the flag names; everyone else keeps the existing
              page, untouched. Same endpoint, same data, same A.26 agent mapping. */}
          <Route path="/strategy-trades" element={newUi ? <NewStrategyTradesPage ctx={ctx} /> : <StrategyTradesPage />} />
          <Route path="/mt5" element={operatorGate(<Mt5AccountView />)} />
          <Route path="/mt5/symbols" element={operatorGate(<Mt5SymbolsView />)} />
          <Route path="/mt5/audit" element={operatorGate(<Mt5AuditView />)} />
          <Route path="/mt5/copy-trading" element={operatorGate(<CopyTradingPage />)} />
          <Route path="/grok-intraday" element={<IntradayBoardPage ctx={ctx} />} />
          <Route path="/grok-intraday/:symbol" element={<AgentModelDetail ctx={ctx} model={GROK_INTRADAY_DETAIL} />} />
          <Route path="/meta-intraday" element={<IntradayBoardPage ctx={ctx} cfg={META_INTRADAY_CFG} />} />
          <Route path="/meta-intraday/:symbol" element={<AgentModelDetail ctx={ctx} model={META_INTRADAY_DETAIL} />} />
          <Route path="/kimi-intraday" element={<IntradayBoardPage ctx={ctx} cfg={KIMI_INTRADAY_CFG} />} />
          <Route path="/kimi-intraday/:symbol" element={<AgentModelDetail ctx={ctx} model={{ base: '/kimi-intraday', title: 'Agent 3 Intraday', detail: (s: string, sig?: AbortSignal) => api2.kimiIntradayDetail(s, sig) }} />} />
          <Route path="/gemini-intraday" element={<IntradayBoardPage ctx={ctx} cfg={GEMINI_INTRADAY_CFG} />} />
          <Route path="/gemini-intraday/:symbol" element={<AgentModelDetail ctx={ctx} model={{ base: '/gemini-intraday', title: 'Agent 4 Intraday', detail: (s: string, sig?: AbortSignal) => api2.geminiIntradayDetail(s, sig) }} />} />
          <Route path="/all-intraday-models" element={<AllIntradayModelsPage ctx={ctx} />} />
          <Route path="/option-scanner" element={newUi ? <NewIvCrushPage ctx={ctx} /> : <OptionScannerPage ctx={ctx} />} />
          <Route path="/unusual-activity" element={newUi ? <NewUnusualActivityPage ctx={ctx} /> : <UnusualActivityPage ctx={ctx} />} />
          <Route path="/pre-earnings-direction" element={newUi ? <NewPreEarningsPage ctx={ctx} /> : <PreEarningsDirectionPage ctx={ctx} />} />
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

          <Route path="/news" element={newUi ? <NewNewsPage ctx={ctx} /> : <NewsDeskPage ctx={ctx} />} />
          <Route path="/news/:symbol" element={<NewsPage ctx={ctx} />} />

          <Route path="/ai-insights" element={<Navigate to={`/ai-insights/${DEFAULT_SYMBOL}${location.search}`} replace />} />
          <Route path="/ai-insights/:symbol" element={<InsightsPage ctx={ctx} />} />

          <Route path="/backtests" element={<BacktestPage ctx={ctx} />} />
          <Route path="/alerts" element={<AlertsPage ctx={ctx} />} />
          <Route path="/trade-journal" element={<JournalPage ctx={ctx} />} />
          <Route path="/strategy" element={<StrategyPage ctx={ctx} />} />
          <Route path="/settings" element={adminGate(<SettingsPage ctx={ctx} />)} />
          {/* C.1: a user connects their OWN broker account. Not admin-gated on purpose - and /settings is. */}
          <Route path="/my-broker" element={<MyBrokerPage />} />
          <Route path="/admin" element={adminGate(<AdminDashboardPage ctx={ctx} />)} />
          <Route path="/admin/users" element={adminGate(<UsersPage ctx={ctx} />)} />
          <Route path="/admin/audit" element={adminGate(<AuditLogPage ctx={ctx} />)} />
          <Route path="/admin/workflow" element={adminGate(<WorkflowPage ctx={ctx} />)} />
          <Route path="/community" element={<CommunityPage ctx={ctx} />} />

          <Route path="*" element={<NotFound />} />
        </Routes>
        )}
        </Suspense>
        </ErrorBoundary>
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
      <Routes>
        {/* The sign-in pages render standalone — no sidebar, no data calls. */}
        <Route path="/login" element={
          <Suspense fallback={<div className="page"><Loading /></div>}>
            <LoginPage />
          </Suspense>
        } />
        <Route path="/admin/login" element={
          <Suspense fallback={<div className="page"><Loading /></div>}>
            <AdminLoginPage />
          </Suspense>
        } />
        <Route path="/*" element={<Shell />} />
      </Routes>
    </BrowserRouter>
  );
}
