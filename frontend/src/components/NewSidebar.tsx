import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronsLeft, ChevronsRight, Cpu, ArrowRight } from 'lucide-react';
// The file names describe the INK, not the theme: logo.png is the WHITE wordmark, which belongs on
// the dark rail, and logo-dark.png is the dark one, which belongs on the light rail. They were
// wired the other way round - invisible while both rendered 11 pixels tall, obvious at full size.
import wordmarkOnDark from '../assets/logo.png';
import { BrandMark } from './Brand';
import wordmarkOnLight from '../assets/logo-dark.png';
import { USER_GROUPS, ADMIN_GROUP, SYMBOL_SECTIONS, type NavGroup } from './navModel';

/**
 * Stage 1 of the redesign: the sidebar, drawn to the Figma frame.
 *
 * WHAT IS DELIBERATELY NOT COPIED. The frame shows a flat list of twelve links. The live menu is
 * twenty-three links in five groups, and the owner's instruction is that the sidebar shows exactly the
 * pages users see today - nothing A.25 or A.26 removed comes back. So the STYLE is the frame's (navy rail,
 * blue active pill, the promo card at the foot) and the CONTENT is the live menu, headings included,
 * because twenty-three links with no headings is the flat wall the groups were introduced to fix.
 *
 * The menu itself is imported from navModel, which is the SAME source the existing sidebar uses. That is
 * the point: two independent copies of a menu is how a page quietly comes back after being removed. If a
 * link is gated, it is gated in one place and both shells obey it.
 */
export default function NewSidebar({
  isAdmin,
  mt5Operator,
  search,
  open,
  onClose,
}: {
  isAdmin?: boolean;
  mt5Operator?: boolean;
  search?: string;
  open?: boolean;
  onClose?: () => void;
}) {
  const COLLAPSE_KEY = 'usr.sidebar.collapsed';
  const [collapsed, setCollapsed] = React.useState(() => {
    // Blocked site data throws rather than returning null, so a failure means expanded.
    try { return window.localStorage.getItem(COLLAPSE_KEY) === '1'; } catch { return false; }
  });
  const location = useLocation();

  // A drawer that stays open over the page it just opened is one you dismiss twice.
  React.useEffect(() => { if (open) onClose?.(); /* eslint-disable-next-line */ }, [location.pathname]);

  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try { window.localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0'); } catch { /* not persisted */ }
      return next;
    });
  };

  const groups: NavGroup[] = isAdmin ? ADMIN_GROUP(mt5Operator) : USER_GROUPS(mt5Operator);

  return (
    <>
      {open && <div className="nu-scrim" onClick={onClose} aria-hidden="true" />}
      <aside
        className={`nu-rail${collapsed ? ' is-collapsed' : ''}${open ? ' is-open' : ''}`}
        aria-label="Sections"
      >
        <div className="nu-rail-head">
          {/* THE WORDMARK IS THE BRAND. Two files, one shown: the rail is navy in dark mode and
              white in light, and a single image cannot read on both. CSS picks; neither is ever
              drawn twice. These are the same two files the classic UI uses, which sizes them
              160px wide - they are 1386x480 wordmarks, and the 32x32 box they used to sit in
              squashed them to an 11px sliver.
              The name is NOT printed beside them: the wordmark already says it. */}
          <img className="nu-logo-dark" src={wordmarkOnDark} alt="Tradgo.us" />
          <img className="nu-logo-light" src={wordmarkOnLight} alt="Tradgo.us" />
          {/* 72px of collapsed rail cannot hold a wordmark, so the brand's own mark stands in. */}
          <span className="nu-logo-mark" aria-hidden="true"><BrandMark size={30} /></span>
          <div className="nu-rail-brand">
            <span>Global Market Intelligence</span>
          </div>
        </div>

        <nav className="nu-nav">
          {groups.map((g) => (
            <React.Fragment key={g.heading || 'admin'}>
              {g.heading && <div className="nu-nav-head">{g.heading}</div>}
              {g.items.map((it) => (
                <NavLink
                  key={it.to}
                  to={SYMBOL_SECTIONS.has(it.to) && search ? `${it.to}${search}` : it.to}
                  className={({ isActive }) => `nu-link${isActive ? ' is-active' : ''}`}
                  title={collapsed ? it.label : undefined}
                >
                  {it.icon}
                  <span>{it.label}</span>
                </NavLink>
              ))}
            </React.Fragment>
          ))}

          {/* INSIDE the nav, as its last child, so it SCROLLS WITH THE MENU instead of
              sitting in the rail's fixed area above it. Every menu item is reached first
              (owner, 2026-11-21). It links to a page that exists and claims nothing. */}
            {!isAdmin && (
            <div className="nu-promo">
              <span className="nu-promo-badge"><Cpu size={16} /> Tradgo.AI</span>
              <h4>Market Intelligence</h4>
              <p>Earnings, options flow and trade signals, from live market data.</p>
              <NavLink to="/ai-insights">Explore Now <ArrowRight size={15} /></NavLink>
            </div>
          )}
        </nav>

        <div className="nu-rail-foot">
          <button type="button" className="nu-rail-toggle" onClick={toggle}
                  aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
            {!collapsed && <span>Collapse</span>}
          </button>
        </div>
      </aside>
    </>
  );
}
