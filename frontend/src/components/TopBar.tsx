import React, { useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { Bell, ChevronDown, Loader2, Moon, RefreshCw, Search, Sun } from 'lucide-react';
import '../pages/signals.css';
import { api2 } from '../api/client';
import type { HealthPayload, Quote, SymbolMatch } from '../api/client';
import { money, signed, signedPct, tone as tone2 } from '../lib/format';
import { applyTheme, resolveTheme, saveTheme, type Theme } from '../lib/theme';

/**
 * Primary sections, as a pill row in the header.
 *
 * The sidebar carries every destination; this is the short list of places the
 * screen actually moves between, which is what the design calls for. Both stay
 * in sync because both read real routes -- there are no dead links here.
 */
const HEADER_NAV = [
  { to: '/ai-insights', label: 'Analysis' },
  { to: '/ai-trade', label: 'AI Trade' },
  { to: '/options-flow', label: 'Options' },
  { to: '/market', label: 'Market' },
  { to: '/watchlist', label: 'Watchlist' },
];

function HeaderNav({ search }: { search: string }) {
  return (
    <nav className="header-nav" aria-label="Primary">
      {HEADER_NAV.map((item) => (
        <NavLink
          key={item.to}
          to={`${item.to}${search}`}
          className={({ isActive }) => `hn-pill ${isActive ? 'active' : ''}`}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

export default function TopBar({
  variant, symbol, onSymbol, quote, health, demo, onRefresh, refreshing, search,
  isAdmin, username,
}: {
  variant: 'earnings' | 'options';
  symbol: string;
  onSymbol: (s: string) => void;
  quote?: Quote | null;
  health?: HealthPayload | null;
  demo?: boolean;
  onRefresh?: () => void;
  refreshing?: boolean;
  search: string;
  isAdmin?: boolean;
  username?: string;
}) {
  const chgTone = tone2(quote?.change);
  // The Analysis page carries its own prominent ticker search (the centred hero
  // when idle, a back-to-search control on a result), so the header's search
  // there was a second box doing the same job. Hide it on that route only.
  const hideHeaderSearch = useLocation().pathname.startsWith('/ai-insights');

  return (
    <header className="topbar">
      {/* Admin runs the operator panel, not the data pages -- no ticker search
          or quote strip belongs in its header. */}
      {isAdmin ? (
        <div className="topbar-spacer" />
      ) : variant === 'earnings' ? (
        <>
          {/* Route navigation lives in the sidebar only. The header used to
              repeat it as pills, which duplicated the sidebar and was the
              single widest thing in the bar. */}
          <HeaderNav search={search} />
          {!hideHeaderSearch && <SymbolSearch symbol={symbol} onSymbol={onSymbol} demo={demo} />}
          <div className="topbar-spacer" />
        </>
      ) : (
        <>
          {!hideHeaderSearch && <SymbolSearch symbol={symbol} onSymbol={onSymbol} demo={demo} />}
          <div className="topbar-quote">
            <span className="tq-sym">{quote?.symbol || symbol}</span>
            <span className="tq-px">{money(quote?.price)}</span>
            <span className="tq-name">{quote?.name}</span>
            <span className={`tq-chg ${chgTone}`}>
              {signed(quote?.change)} ({signedPct(quote?.change_percent)})
            </span>
            {/* Shown beside the close rather than replacing it: the session
                figure and the extended print are both true and mean
                different things. */}
            {quote?.extended && <ExtendedTag ext={quote.extended} />}
          </div>
          <div className="topbar-spacer" />
        </>
      )}

      {demo || isAdmin ? null : <HealthBadge health={health} search={search} />}
      {demo && <DemoBadge />}

      {onRefresh && !isAdmin && (
        <button className="icon-btn" onClick={onRefresh}
          title="Refresh live data" aria-label="Refresh live data">
          <RefreshCw size={15} className={refreshing ? 'spin' : undefined} />
        </button>
      )}

      {!demo && <SignalBell search={search} />}

      <ThemeToggle />

      <AccountMenu isAdmin={isAdmin} username={username} search={search} demo={demo} />
    </header>
  );
}

/**
 * The profile control: a dropdown, not a link. Click it to see who you are
 * signed in as and to sign out. Admins also get the provider Settings link
 * here; regular users never see it (Settings is the admin panel).
 */
function AccountMenu({
  isAdmin, username, search, demo,
}: { isAdmin?: boolean; username?: string; search: string; demo?: boolean }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const name = username || (isAdmin ? 'admin' : 'account');
  const role = isAdmin ? 'Administrator' : 'User';
  const initial = (name[0] || 'U').toUpperCase();

  const signOut = async () => {
    setBusy(true);
    try { await api2.logout(); } catch { /* clear client-side regardless */ }
    // The cookie is HttpOnly, so the server clears it; a hard reload drops any
    // in-memory state and lands on the login screen the gate now redirects to.
    window.location.href = '/';
  };

  return (
    <div className={`acct ${open ? 'open' : ''}`} ref={ref}>
      <button className="plan" title="Account"
        onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open}>
        <div className="avatar">{initial}</div>
        <div className="plan-text">
          <b>{name}</b>
          <span>{role}</span>
        </div>
        <ChevronDown size={13} color="var(--text-mute)" />
      </button>
      {open && (
        <div className="acct-menu" role="menu">
          <div className="acct-head">
            <div className="avatar lg">{initial}</div>
            <div>
              <b>{name}</b>
              <span>{role}</span>
            </div>
          </div>
          {isAdmin && !demo && (
            <>
              <button className="acct-item" role="menuitem"
                onClick={() => { setOpen(false); navigate(`/admin/users${search}`); }}>
                Manage users
              </button>
              <button className="acct-item" role="menuitem"
                onClick={() => { setOpen(false); navigate(`/settings${search}`); }}>
                Provider settings
              </button>
              <div className="acct-sep" />
            </>
          )}
          <button className="acct-item danger" role="menuitem"
            onClick={signOut} disabled={busy}>
            {busy ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Ticker search, suggesting from the SEC ticker registry.
 *
 * Suggestions come from reqMatchingSymbols, and committing a symbol validates
 * it against a real contract before navigating, so an unknown ticker says so
 * here instead of loading an empty page.
 */
function SymbolSearch({
  symbol, onSymbol, demo,
}: { symbol: string; onSymbol: (s: string) => void; demo?: boolean }) {
  const [draft, setDraft] = useState(symbol);
  const [matches, setMatches] = useState<SymbolMatch[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setDraft(symbol); setError(null); }, [symbol]);

  // Debounced lookup; aborted whenever the query moves on.
  useEffect(() => {
    if (demo || !open) return;
    const q = draft.trim();
    if (q.length < 1 || q === symbol) { setMatches([]); return; }

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api2.searchSymbols(q, controller.signal)
        .then((r) => { setMatches(r.matches || []); setHighlight(0); })
        .catch(() => undefined);
    }, 220);

    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [draft, open, demo, symbol]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const commit = async (value: string) => {
    const next = value.trim().toUpperCase();
    if (!next) return;
    setOpen(false);
    if (next === symbol) return;

    if (demo) { onSymbol(next); return; }

    setBusy(true);
    setError(null);
    try {
      const check = await api2.validateSymbol(next);
      if (check.valid) {
        onSymbol(next);
      } else {
        setError(check.status === 'SYMBOL_NOT_FOUND'
          ? `SYMBOL NOT FOUND: ${next}`
          : check.detail || 'Could not validate symbol');
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      commit(matches[highlight]?.symbol || draft);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="search-wrap" ref={boxRef}>
      <div className={`search ${error ? 'invalid' : ''}`}>
        {busy ? <Loader2 size={13} className="spin" /> : <Search size={13} />}
        <input
          value={draft}
          onChange={(e) => { setDraft(e.target.value.toUpperCase()); setOpen(true); setError(null); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search ticker (e.g., NVDA)"
          aria-label="Search ticker"
          spellCheck={false}
        />
      </div>

      {error && <div className="search-error">{error}</div>}

      {open && matches.length > 0 && (
        <ul className="search-menu" role="listbox">
          {matches.map((m, i) => (
            <li
              key={m.con_id}
              role="option"
              aria-selected={i === highlight}
              className={i === highlight ? 'on' : ''}
              onMouseEnter={() => setHighlight(i)}
              onMouseDown={(e) => { e.preventDefault(); commit(m.symbol); }}
            >
              <b>{m.symbol}</b>
              <span>{m.name}</span>
              <i>{m.exchange}</i>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Pre- or post-market print, labelled with which session it belongs to. */
function ExtendedTag({ ext }: { ext: NonNullable<Quote['extended']> }) {
  const tone = tone2(ext.change);
  return (
    <span className="ext-tag" title={
      `${ext.session_label}: ${ext.price} against a ${ext.previous_close} close`
      + (ext.as_of ? ` · as of ${new Date(ext.as_of).toLocaleTimeString()}` : '')
      + ` · source ${ext.source}`
    }>
      <i>{ext.session === 'PRE_MARKET' ? 'PRE' : 'POST'}</i>
      <b>{money(ext.price)}</b>
      <span className={tone}>
        {signed(ext.change)} ({signedPct(ext.change_percent)})
      </span>
    </span>
  );
}

function DemoBadge() {
  return (
    <span className="conn demo" title="Showing isolated demo fixtures, not live data">
      <i className="dot" />
      DEMO DATA
    </span>
  );
}

function HealthBadge({
  health, search,
}: { health?: HealthPayload | null; search: string }) {
  if (!health) {
    return <span className="conn"><i className="dot" />Connecting</span>;
  }

  // The feed answering is not the same as the market trading. Saying "LIVE"
  // while the Market Overview says "Closed" read as a contradiction, so the
  // badge now reflects the session too: "LIVE" only when the market is open,
  // otherwise the session label (e.g. "Closed") in a calm, non-green state --
  // the same state the page's own header shows.
  const feedOk = health.feed === 'OK';
  const marketOpen = !!health.market?.is_open;
  const live = feedOk && marketOpen;
  const closed = feedOk && !marketOpen;
  // A rate-limit pause is temporary and the screens still serve held data, so
  // it is "busy", not "offline". Only a genuine outage (no data at all) is off.
  const dataFlowing = health.market_data === 'OK';
  const busy = !feedOk && (dataFlowing
    || health.feed === 'RATE_LIMITED' || health.feed === 'UNKNOWN');
  const degraded = live && (
    health.market_data !== 'OK' || health.providers?.news?.status !== 'OK'
  );
  const sessionLabel = health.market?.label || 'Market closed';

  const title = Object.entries(health.providers || {})
    .map(([k, v]) => `${k}: ${v.status}`)
    .join(' · ');

  return (
    <NavLink
      to={`/settings${search}`}
      className={`conn ${live ? (degraded ? 'warn' : 'live') : closed ? '' : busy ? 'warn' : 'down'}`}
      title={title || 'Provider status'}
    >
      <i className="dot" />
      {live ? (degraded ? 'LIVE · DEGRADED' : 'LIVE')
        : closed ? sessionLabel
          : busy ? 'FEED BUSY' : 'FEED OFFLINE'}
    </NavLink>
  );
}


/**
 * Light / dark switch.
 *
 * This slot used to be a second link to Settings -- the account control beside
 * it already goes there, so the moon icon promised a theme toggle and did
 * nothing of the sort.
 */
function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => resolveTheme());

  useEffect(() => { applyTheme(theme); }, [theme]);

  // Shown as a pair rather than one swapping icon: the design puts both modes
  // on screen so the current one reads as a state, not as an action.
  const pick = (next: Theme) => {
    if (next === theme) return;
    saveTheme(next);
    setTheme(next);
  };

  return (
    <div className="theme-switch" role="group" aria-label="Colour theme">
      <button
        className={`ts-btn ${theme === 'light' ? 'on' : ''}`}
        onClick={() => pick('light')}
        title="Light mode"
        aria-label="Light mode"
        aria-pressed={theme === 'light'}
      >
        <Sun size={14} />
      </button>
      <button
        className={`ts-btn ${theme === 'dark' ? 'on' : ''}`}
        onClick={() => pick('dark')}
        title="Dark mode"
        aria-label="Dark mode"
        aria-pressed={theme === 'dark'}
      >
        <Moon size={14} />
      </button>
    </div>
  );
}

const SG_LABEL: Record<string, string> = {
  BIAS_FLIP: 'View changed', NEW_SETUP: 'New setup', TP1_HIT: 'Target 1 hit',
  TP2_HIT: 'Target 2 hit', SL_HIT: 'Stopped out', EXPIRED: 'Expired',
  INVALIDATED: 'Invalidated',
};

/**
 * Header notification bell. Polls the signal feed, shows an unread badge, opens
 * a dropdown of recent events, and pops a toast when a new one lands.
 */
function SignalBell({ search }: { search: string }) {
  const navigate = useNavigate();
  const [count, setCount] = useState(0);
  const [events, setEvents] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [toasts, setToasts] = useState<any[]>([]);
  const ref = useRef<HTMLDivElement | null>(null);
  const lastId = useRef(0);
  const primed = useRef(false);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await api2.signals(8);
        if (!alive) return;
        const evs: any[] = r?.events || [];
        setEvents(evs);
        setCount(r?.unread || 0);
        const maxId = evs.length ? evs[0].id : 0;
        if (primed.current && maxId > lastId.current) {
          const fresh = evs.filter((e) => e.id > lastId.current).slice(0, 3);
          fresh.forEach((e) => {
            setToasts((t) => [{ ...e, _k: e.id }, ...t].slice(0, 3));
            window.setTimeout(
              () => setToasts((t) => t.filter((x) => x._k !== e.id)), 9000);
          });
        }
        if (maxId) lastId.current = Math.max(lastId.current, maxId);
        primed.current = true;
      } catch { /* ignore */ }
    };
    poll();
    const id = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      poll();
    }, 30_000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const openFeed = () => {
    setOpen((v) => !v);
    if (!open && count) { api2.signalsSeen().catch(() => {}); setCount(0); }
  };
  const goPlan = (sym: string) => {
    setOpen(false);
    navigate(sym ? `/trade-plan/${sym}${search}` : `/trade-plan${search}`);
  };

  return (
    <div className="sg-bell-wrap" ref={ref}>
      <button className="icon-btn sg-bell" title="Signals"
        aria-label="Signals" onClick={openFeed}>
        <Bell size={15} />
        {count > 0 && <span className="sg-bell-badge">{count > 99 ? '99+' : count}</span>}
      </button>

      {open && (
        <div className="sg-dropdown">
          <div className="sg-dd-head">Signals</div>
          {events.length === 0 ? (
            <div className="sg-dd-empty">No signals yet.</div>
          ) : (
            events.map((e) => (
              <button key={e.id} className="sg-dd-item" onClick={() => goPlan(e.symbol)}>
                <span className="sg-dd-sym">{e.symbol}</span>
                <span className="sg-dd-type">{SG_LABEL[e.type] || e.type}</span>
                <span className="sg-dd-note">{e.note}</span>
              </button>
            ))
          )}
          <button className="sg-dd-all" onClick={() => goPlan('')}>
            Open Trade Plan →
          </button>
        </div>
      )}

      {/* Toasts: pop when a fresh signal lands. */}
      <div className="sg-toasts">
        {toasts.map((t) => (
          <button key={t._k} className="sg-toast" onClick={() => goPlan(t.symbol)}>
            <Bell size={14} />
            <div>
              <div className="sg-toast-t">
                <b>{t.symbol}</b> · {SG_LABEL[t.type] || t.type}
              </div>
              <div className="sg-toast-n">{t.note}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
