import React from 'react';
import { NavLink } from 'react-router-dom';
import { useLocation } from 'react-router-dom';
import {
  Activity, BarChart3, Brain, Building2, CalendarDays, Compass, Crosshair, FlaskConical, Flame, Gauge, History, Layers, LineChart, Receipt, Users,
  LayoutDashboard, ListOrdered, Menu, Newspaper, PanelLeftClose, PanelLeftOpen,
  Radar, Scale, ScrollText, Settings, ShieldCheck, Sparkles, Star, TrendingUp, UserCog, Waves, Waypoints, X, Zap,
} from 'lucide-react';
import { BrandLockup, BRAND_QUOTE_EARNINGS } from './Brand';

const S = 15;

interface NavEntry {
  to: string;
  label: string;
  icon: React.ReactNode;
}

interface NavGroup {
  heading: string;
  items: NavEntry[];
}

/**
 * The sidebar: every section, grouped by what you came to do.
 *
 * Still one entry per route -- nothing reachable only by URL, nothing twice --
 * but the fifteen links were a flat wall that was hard to scan, so they are
 * gathered under a few headings (Analysis, Markets, Earnings & news, Lists).
 * The groups are labels only, not collapsible: a label you cannot get wrong is
 * worth more here than another thing to toggle.
 */
// MT5 auto-trading (operator only): shown next to Strategy Trades for the operator, in the admin panel for the admin.
const MT5_ITEMS: NavEntry[] = [
  { to: '/mt5', label: 'MT5 Account', icon: <Settings size={S} /> },
  { to: '/mt5/symbols', label: 'MT5 Symbol Mapping', icon: <ListOrdered size={S} /> },
  { to: '/mt5/audit', label: 'MT5 Audit Report', icon: <ScrollText size={S} /> },
];

const NAV_GROUPS: NavGroup[] = [
  { heading: 'Analysis', items: [
    { to: '/ai-insights', label: 'Analysis Intraday', icon: <Brain size={S} /> },
    { to: '/grok-intraday', label: 'Grok Intraday', icon: <Zap size={S} /> },
    { to: '/meta-intraday', label: 'Meta Intraday', icon: <Layers size={S} /> },
    { to: '/kimi-intraday', label: 'Kimi Intraday', icon: <Compass size={S} /> },
    { to: '/gemini-intraday', label: 'Gemini Intraday', icon: <Waypoints size={S} /> },
    { to: '/all-intraday-models', label: 'All Intraday', icon: <ListOrdered size={S} /> },
    { to: '/strategy-trades', label: 'Strategy Trades', icon: <Receipt size={S} /> },
    // A.25 (owner decision 2026-11-13, after the intraday audit): Tradgo Call, Trade Plan, Signal History and Sector
    // Comparison left the menu. Their ROUTES still work (/tradgo-call, /trade-plan, /signal-history, /peers) and every
    // module stays on disk, because the earnings pages, the signal-colouring route and the snapshot warmer import them.
    // Nothing was deleted. Trade Plan's background scanner and its Claude read are off (main.py, trade_plan_service).
    { to: '/smart-move/engine20', label: 'Smart Money Move (LIVE)', icon: <Gauge size={S} /> },
  ] },
  { heading: 'Earnings & news', items: [
    { to: '/earnings-calendar', label: 'Earnings Calendar', icon: <CalendarDays size={S} /> },
    // A.25 (owner decision 2026-11-13): the seven per-model boards left the menu. Earnings calls are made by Smart Money
    // Move now, and these seven were one component over the same cached data. The ROUTES still work (/claude-last, /grok,
    // /meta, /chatgpt, /kimi, /gemini, /claude-pro) and every module stays on disk, because All Models, the backtests, the
    // monitor registry and the four intraday boards import them. Nothing was deleted.
    { to: '/all-models', label: 'All Models', icon: <ListOrdered size={S} /> },
    { to: '/model-accuracy', label: 'Model Accuracy', icon: <BarChart3 size={S} /> },
    { to: '/option-scanner', label: 'IV Crush Check', icon: <Flame size={S} /> },
    { to: '/unusual-activity', label: 'Unusual Activity', icon: <Zap size={S} /> },
    { to: '/pre-earnings-direction', label: 'Pre-Earnings Direction', icon: <Compass size={S} /> },
    { to: '/earnings-signals', label: 'Signal History', icon: <History size={S} /> },
    { to: '/forward-scoreboard', label: 'Forward Scoreboard', icon: <ShieldCheck size={S} /> },
    { to: '/historical-backtest', label: 'Historical Backtest', icon: <FlaskConical size={S} /> },
    { to: '/post-earnings-forward', label: 'Post-Earnings AI', icon: <Radar size={S} /> },
    { to: '/news', label: 'News & Sentiment', icon: <Newspaper size={S} /> },
  ] },
  { heading: 'Markets', items: [
    { to: '/market', label: 'Market Overview', icon: <TrendingUp size={S} /> },
    { to: '/dashboard', label: 'Overview', icon: <LayoutDashboard size={S} /> },
    { to: '/stocks', label: 'Stocks', icon: <ListOrdered size={S} /> },
    { to: '/options-flow', label: 'Options', icon: <Activity size={S} /> },
    { to: '/oi-buildup', label: 'OI Build-Up', icon: <Layers size={S} /> },
    { to: '/volatility', label: 'Volatility', icon: <LineChart size={S} /> },
    { to: '/dark-pool', label: 'Dark Pool', icon: <Waves size={S} /> },
    { to: '/market-insiders', label: 'Market Insiders', icon: <UserCog size={S} /> },
    { to: '/institutional-intel', label: 'Institutional Intelligence', icon: <Building2 size={S} /> },
  ] },
  { heading: 'Lists', items: [
    { to: '/watchlist', label: 'Watchlist', icon: <Star size={S} /> },
  ] },
];

const COLLAPSE_KEY = 'usr.sidebar.collapsed';

function readCollapsed(): boolean {
  // Private windows and blocked site data make this throw rather than return
  // null, so a failure has to mean "expanded" instead of breaking the shell.
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}

// Sections whose URL carries the active ticker. Linking to these with the
// current symbol keeps the stock you're viewing when you switch sections,
// instead of snapping back to the default.
const SYMBOL_SECTIONS = new Set([
  '/earnings', '/options-flow', '/volatility', '/news',
  '/ai-insights', '/peers', '/dark-pool', '/dashboard', '/model-performance',
]);

export default function Sidebar({
  optionsMode,
  search,
  isAdmin,
  symbol,
  mt5Operator,
  smAlert,
}: {
  optionsMode: boolean;
  search: string;
  isAdmin?: boolean;
  symbol?: string;
  mt5Operator?: boolean;
  /** Gate-refusal badge on the Smart Money Move (LIVE) item: 'critical' (red) until acknowledged, 'ack' (grey) until a LIVE pass succeeds. */
  smAlert?: 'critical' | 'ack';
}) {
  // The admin is the operator, not a viewer: signed in as admin the sidebar is
  // just the admin panel -- Users and Settings -- and none of the data pages.
  // Regular users get every data page and neither admin link.
  const groups: NavGroup[] = isAdmin
    ? [{ heading: '', items: [
        { to: '/admin', label: 'Dashboard', icon: <LayoutDashboard size={S} /> },
        { to: '/admin/users', label: 'Users', icon: <Users size={S} /> },
        { to: '/admin/audit', label: 'Audit Log', icon: <ScrollText size={S} /> },
        { to: '/admin/workflow', label: 'Workflow', icon: <Waypoints size={S} /> },
        { to: '/settings', label: 'Settings', icon: <Settings size={S} /> },
        ...(mt5Operator ? MT5_ITEMS : [])] }]
    : (mt5Operator ? NAV_GROUPS.map((g) => (g.heading === 'Analysis' ? { ...g, items: [...g.items, ...MT5_ITEMS] } : g)) : NAV_GROUPS);
  const [collapsed, setCollapsed] = React.useState(readCollapsed);

  // On a phone the sidebar is a drawer rather than a column. Without it there
  // was no navigation at all below 820px -- the sidebar was simply hidden and
  // the header's pill row with it, so every screen except the one you landed
  // on was unreachable.
  const [open, setOpen] = React.useState(false);
  const location = useLocation();

  // Close on navigation: a drawer that stays open over the page it just
  // opened is a drawer you have to dismiss twice.
  React.useEffect(() => { setOpen(false); }, [location.pathname]);

  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0');
      } catch {
        // Preference simply does not persist; the toggle still works.
      }
      return next;
    });
  };

  return (
    <>
      {/* Only rendered at phone widths; CSS hides it everywhere else. */}
      <button
        className="nav-fab"
        onClick={() => setOpen(true)}
        aria-label="Open navigation"
        aria-expanded={open}
      >
        <Menu size={18} />
      </button>

      {open && (
        <button
          className="nav-scrim"
          onClick={() => setOpen(false)}
          aria-label="Close navigation"
        />
      )}

      <aside className={`sidebar ${collapsed ? 'collapsed' : ''} ${open ? 'open' : ''}`}>
      <button
        className="nav-close"
        onClick={() => setOpen(false)}
        aria-label="Close navigation"
      >
        <X size={16} />
      </button>
      <button
        className="sidebar-toggle"
        onClick={toggle}
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        aria-expanded={!collapsed}
      >
        {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
      </button>

      {!collapsed && <BrandLockup />}

      <nav className="nav">
        {groups.map((group) => (
          <div className="nav-group" key={group.heading || 'main'}>
            {group.heading && !collapsed && (
              <div className={`nav-group-label${group.heading === 'Earnings & news' ? ' blink' : ''}`}>{group.heading}</div>
            )}
            {group.items.map((item) => (
              <NavLink
                key={item.to + item.label}
                to={(SYMBOL_SECTIONS.has(item.to) && symbol
                  ? `${item.to}/${symbol}` : item.to) + search}
                className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                end={item.to === '/dashboard'}
                // Collapsed to icons only, the label has to survive as a tooltip.
                title={collapsed ? item.label : undefined}
              >
                {item.icon}
                {!collapsed && <span>{item.label}</span>}
                {smAlert && item.to === '/smart-move/engine20' && (
                  <span className={`nav-badge ${smAlert}`} data-testid="sm-alert-badge" title={smAlert === 'ack' ? 'Gate refusal acknowledged; waiting for a successful LIVE pass' : 'The scheduled LIVE pass was refused'} aria-label="Smart Money Move alert">!</span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        {!collapsed && (
          <div className="sidebar-quote">
            {BRAND_QUOTE_EARNINGS}
            <b>— Trdgo.us</b>
          </div>
        )}
      </div>
      </aside>
    </>
  );
}
