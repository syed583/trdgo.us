import React from 'react';
import { NavLink } from 'react-router-dom';
import { useLocation } from 'react-router-dom';
import {
  Activity, BarChart3, Brain, CalendarDays, LineChart, Users,
  LayoutDashboard, ListOrdered, Menu, Newspaper, PanelLeftClose, PanelLeftOpen,
  Scale, Settings, Sparkles, Star, Target, TrendingUp, UserCog, Waves, Waypoints, X,
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
const NAV_GROUPS: NavGroup[] = [
  { heading: 'Analysis', items: [
    { to: '/ai-insights', label: 'Analysis', icon: <Brain size={S} /> },
    { to: '/ai-trade', label: 'AI Trade', icon: <Sparkles size={S} /> },
    { to: '/model-performance', label: 'Model Performance', icon: <Target size={S} /> },
    { to: '/peers', label: 'Peer Comparison', icon: <Scale size={S} /> },
  ] },
  { heading: 'Markets', items: [
    { to: '/market', label: 'Market Overview', icon: <TrendingUp size={S} /> },
    { to: '/dashboard', label: 'Overview', icon: <LayoutDashboard size={S} /> },
    { to: '/stocks', label: 'Stocks', icon: <ListOrdered size={S} /> },
    { to: '/options-flow', label: 'Options', icon: <Activity size={S} /> },
    { to: '/volatility', label: 'Volatility', icon: <LineChart size={S} /> },
    { to: '/dark-pool', label: 'Dark Pool', icon: <Waves size={S} /> },
    { to: '/market-insiders', label: 'Market Insiders', icon: <UserCog size={S} /> },
  ] },
  { heading: 'Earnings & news', items: [
    { to: '/earnings-calendar', label: 'Earnings Calendar', icon: <CalendarDays size={S} /> },
    { to: '/earnings', label: 'Earnings Analysis', icon: <BarChart3 size={S} /> },
    { to: '/news', label: 'News & Sentiment', icon: <Newspaper size={S} /> },
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
}: {
  optionsMode: boolean;
  search: string;
  isAdmin?: boolean;
  symbol?: string;
}) {
  // The admin is the operator, not a viewer: signed in as admin the sidebar is
  // just the admin panel -- Users and Settings -- and none of the data pages.
  // Regular users get every data page and neither admin link.
  const groups: NavGroup[] = isAdmin
    ? [{ heading: '', items: [
        { to: '/admin/users', label: 'Users', icon: <Users size={S} /> },
        { to: '/admin/workflow', label: 'Workflow', icon: <Waypoints size={S} /> },
        { to: '/settings', label: 'Settings', icon: <Settings size={S} /> }] }]
    : NAV_GROUPS;
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
              <div className="nav-group-label">{group.heading}</div>
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
