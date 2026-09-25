import React from 'react';
import { NavLink } from 'react-router-dom';
import { useLocation } from 'react-router-dom';
import {
  Activity, BarChart3, Brain, CalendarDays, LineChart, Users,
  LayoutDashboard, Menu, Newspaper, PanelLeftClose, PanelLeftOpen,
  Settings, Sparkles, Star, TrendingUp, X,
} from 'lucide-react';
import { BrandLockup, BRAND_QUOTE_EARNINGS } from './Brand';

const S = 15;

interface NavEntry {
  to: string;
  label: string;
  icon: React.ReactNode;
}

/**
 * The sidebar: every section, one list.
 *
 * Still a single list rather than the old pair that swapped by workspace --
 * entries that move under you depending on where you came from cannot be
 * learned. Every route the app serves has exactly one entry here, so nothing
 * is reachable only by typing a URL and nothing appears twice.
 */
const NAV: NavEntry[] = [
  { to: '/ai-insights', label: 'Analysis', icon: <Brain size={S} /> },
  { to: '/ai-trade', label: 'AI Trade', icon: <Sparkles size={S} /> },
  { to: '/dashboard', label: 'Overview', icon: <LayoutDashboard size={S} /> },
  { to: '/earnings-calendar', label: 'Earnings Calendar', icon: <CalendarDays size={S} /> },
  { to: '/earnings', label: 'Earnings Analysis', icon: <BarChart3 size={S} /> },
  { to: '/options-flow', label: 'Options', icon: <Activity size={S} /> },
  { to: '/volatility', label: 'Volatility', icon: <LineChart size={S} /> },
  { to: '/market', label: 'Market Overview', icon: <TrendingUp size={S} /> },
  { to: '/watchlist', label: 'Watchlist', icon: <Star size={S} /> },
  { to: '/news', label: 'News & Sentiment', icon: <Newspaper size={S} /> },
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

export default function Sidebar({
  optionsMode,
  search,
  isAdmin,
}: {
  optionsMode: boolean;
  search: string;
  isAdmin?: boolean;
}) {
  // The admin is the operator, not a viewer: signed in as admin the sidebar is
  // just the admin panel -- Users and Settings -- and none of the data pages.
  // Regular users get every data page and neither admin link.
  const nav = isAdmin
    ? [{ to: '/admin/users', label: 'Users', icon: <Users size={S} /> },
       { to: '/settings', label: 'Settings', icon: <Settings size={S} /> }]
    : NAV;
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
        {nav.map((item) => (
          <NavLink
            key={item.to + item.label}
            to={`${item.to}${search}`}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            end={item.to === '/dashboard'}
            // Collapsed to icons only, the label has to survive as a tooltip.
            title={collapsed ? item.label : undefined}
          >
            {item.icon}
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-foot">
        {!collapsed && (
          <div className="sidebar-quote">
            {BRAND_QUOTE_EARNINGS}
            <b>— Tradgo.US</b>
          </div>
        )}
      </div>
      </aside>
    </>
  );
}
