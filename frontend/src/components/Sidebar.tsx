import React from 'react';
import { NavLink } from 'react-router-dom';
import { useLocation } from 'react-router-dom';
import {
  Activity, Brain, Building2, CalendarDays, Compass, Crosshair, FlaskConical, Flame, Gauge, History, Layers, LineChart, Receipt, Users,
  LayoutDashboard, Link2, ListOrdered, Menu, Newspaper, PanelLeftClose, PanelLeftOpen,
  Radar, Scale, ScrollText, Settings, ShieldCheck, Sparkles, Star, TrendingUp, UserCog, Waves, Waypoints, X, Zap,
} from 'lucide-react';
import { BrandLockup, BRAND_QUOTE_EARNINGS } from './Brand';
import { S, SYMBOL_SECTIONS, ADMIN_GROUP, USER_GROUPS, type NavGroup } from './navModel';

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

// The menu lives in navModel so the redesigned shell reads the SAME list. Two copies of a menu drift,
// and the way they drift is that a page A.25 or A.26 removed reappears in one shell only.

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
  const groups: NavGroup[] = isAdmin ? ADMIN_GROUP(mt5Operator) : USER_GROUPS(mt5Operator);
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
