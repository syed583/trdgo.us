import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Bell, Bot, Crown, HelpCircle, LogOut, Menu, Search, Settings, Sun, Moon, ChevronDown, Users,
} from 'lucide-react';
import TradeAlerts, { useUnseenAlerts } from './TradeAlerts';
import { api2 } from '../api/client';

/**
 * Stage 1 of the redesign: the top bar, drawn to the Figma frame.
 *
 * EVERY VALUE HERE IS REAL OR ABSENT. The frame shows "Pro Plan", "Rizwan Khan / Pro Member" and a red
 * notification dot. The name comes from the signed-in account (/auth/me display_name); the plan comes from
 * that account's role; the dot appears only when there is something to see. Where we have no real value
 * the element is not drawn at all - a decorative "Pro Plan" chip on an account that has no plan is a false
 * statement about the product, and the owner's rule is that no number or claim is a placeholder.
 */
export default function NewTopBar({
  displayName,
  username,
  role,
  isAdmin,
  alerts,
  theme,
  onTheme,
  onBurger,
}: {
  displayName?: string | null;
  username?: string;
  role?: string;
  isAdmin?: boolean;
  /** How many unread things there are. Undefined or 0 means no dot - never a decorative one. */
  alerts?: number;
  theme?: 'dark' | 'light';
  onTheme?: () => void;
  onBurger?: () => void;
}) {
  const navigate = useNavigate();
  const [q, setQ] = React.useState('');
  const [bellOpen, setBellOpen] = React.useState(false);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [signingOut, setSigningOut] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement | null>(null);
  const { unseen, markSeen } = useUnseenAlerts();

  // A menu that will not close is worse than no menu: outside click and Escape both dismiss it.
  React.useEffect(() => {
    if (!menuOpen) return undefined;
    const away = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [menuOpen]);

  const name = (displayName || username || '').trim();
  // Initials for the avatar: first letters of the first two words, else the first two characters.
  const initials = React.useMemo(() => {
    if (!name) return '';
    const parts = name.split(/\s+/).filter(Boolean);
    const s = parts.length >= 2 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
    return s.toUpperCase();
  }, [name]);

  // The plan chip states the account's real tier. An ordinary account shows nothing rather than "Pro".
  const plan = isAdmin ? 'Administrator' : (role && role !== 'user' ? role.replace(/_/g, ' ') : null);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const sym = q.trim().toUpperCase();
    if (!sym) return;
    navigate(`/ai-insights?symbol=${encodeURIComponent(sym)}`);
    setQ('');
  };

  return (
    <header className="nu-bar">
      <button type="button" className="nu-burger" onClick={onBurger} aria-label="Open menu">
        <Menu size={22} />
      </button>

      <form className="nu-search" onSubmit={submit} role="search">
        <Search size={18} />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search symbol, company or keyword…   (e.g. NVDA, AAPL, earnings)"
          aria-label="Search symbol, company or keyword"
        />
      </form>

      <div className="nu-bar-right">
        {plan && <span className="nu-plan"><Crown size={15} /> {plan}</span>}

        {/* The bell OPENS A PANEL of the trades the models just opened. It used to jump to
            /alerts, which is the price-rule builder - a different thing, still linked from the
            panel's footer. The dot appears only when something has arrived that you have not
            looked at. */}
        <div className="nu-bell-wrap">
          <button type="button" className="nu-icon" aria-label="Trade alerts"
            aria-expanded={bellOpen} onClick={() => setBellOpen((v) => !v)}>
            <Bell size={17} />
            {unseen > 0 && <span className="nu-dot" aria-hidden="true" />}
          </button>
          {bellOpen && <TradeAlerts onClose={() => setBellOpen(false)} onSeen={markSeen} />}
        </div>

        {/* The assistant. It used to be a bubble pinned to the bottom-right corner, which at the
            design's 1536x1024 canvas sat on top of the last row of every page. */}
        <button type="button" className="nu-icon" aria-label="Ask the Trdgo assistant"
          title="Ask the Trdgo assistant"
          onClick={() => window.dispatchEvent(new Event('trdgo:assistant-open'))}>
          <Bot size={17} />
        </button>

        <button type="button" className="nu-icon" onClick={onTheme}
                aria-label={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'}>
          {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
        </button>

        {name && (
          <div className={`nu-user-wrap${menuOpen ? ' is-open' : ''}`} ref={menuRef}>
            {/* This was a <div> with no handler - clicking it did nothing at all. */}
            <button type="button" className="nu-user" aria-haspopup="menu" aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}>
              <span className="nu-avatar" aria-hidden="true">{initials}</span>
              <span className="nu-user-txt">
                <b>{name}</b>
                <span>{isAdmin ? 'Administrator' : 'Member'}</span>
              </span>
              <ChevronDown size={16} aria-hidden="true" />
            </button>
            {menuOpen && (
              <div className="nu-menu" role="menu">
                <div className="nu-menu-head">
                  <span className="nu-avatar lg" aria-hidden="true">{initials}</span>
                  <div><b>{name}</b><span>{isAdmin ? 'Administrator' : 'Member'}</span></div>
                </div>
                {/* Admin-only destinations, exactly as the classic menu gates them: /settings
                    redirects a non-admin to the dashboard, so offering it to everyone would be a
                    menu item that silently does the wrong thing. */}
                {isAdmin && (
                  <>
                    <button type="button" className="nu-menu-item" role="menuitem"
                      onClick={() => { setMenuOpen(false); navigate('/admin/users'); }}>
                      <Users size={15} /> Manage users
                    </button>
                    <button type="button" className="nu-menu-item" role="menuitem"
                      onClick={() => { setMenuOpen(false); navigate('/settings'); }}>
                      <Settings size={15} /> Provider settings
                    </button>
                    <div className="nu-menu-sep" />
                  </>
                )}
                {/* Help is the headphones' job, moved in here. It opens the assistant, which is a
                    thing that exists and answers - there is no help PAGE to link to. */}
                <button type="button" className="nu-menu-item" role="menuitem"
                  onClick={() => { setMenuOpen(false); window.dispatchEvent(new Event('trdgo:assistant-open')); }}>
                  <HelpCircle size={15} /> Help &amp; assistant
                </button>
                <div className="nu-menu-sep" />
                <button type="button" className="nu-menu-item nu-menu-out" role="menuitem"
                  disabled={signingOut}
                  onClick={async () => {
                    setSigningOut(true);
                    try { await api2.logout(); } catch { /* the cookie is HttpOnly; the server clears it */ }
                    // A hard navigation drops any in-memory state, as the classic menu does.
                    window.location.href = '/login';
                  }}>
                  <LogOut size={15} /> {signingOut ? 'Signing out…' : 'Sign out'}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
