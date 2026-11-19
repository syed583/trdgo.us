import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Bell, Crown, Headphones, Menu, Search, Sun, Moon, ChevronDown } from 'lucide-react';

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

        <button type="button" className="nu-icon" aria-label="Alerts" onClick={() => navigate('/alerts')}>
          <Bell size={17} />
          {/* Only when there is genuinely something unread. */}
          {!!alerts && alerts > 0 && <span className="nu-dot" aria-hidden="true" />}
        </button>

        <button type="button" className="nu-icon" aria-label="Help" onClick={() => navigate('/settings')}>
          <Headphones size={17} />
        </button>

        <button type="button" className="nu-icon" onClick={onTheme}
                aria-label={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'}>
          {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
        </button>

        {name && (
          <div className="nu-user">
            <span className="nu-avatar" aria-hidden="true">{initials}</span>
            <span className="nu-user-txt">
              <b>{name}</b>
              <span>{isAdmin ? 'Administrator' : 'Member'}</span>
            </span>
            <ChevronDown size={16} style={{ color: 'var(--nu-rail-ink-dim)' }} aria-hidden="true" />
          </div>
        )}
      </div>
    </header>
  );
}
