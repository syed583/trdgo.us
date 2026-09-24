import React, { useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { ChevronDown, Loader2, Moon, RefreshCw, Search, Sun } from 'lucide-react';
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
}) {
  const chgTone = tone2(quote?.change);

  return (
    <header className="topbar">
      {variant === 'earnings' ? (
        <>
          {/* Route navigation lives in the sidebar only. The header used to
              repeat it as pills, which duplicated the sidebar and was the
              single widest thing in the bar. */}
          <HeaderNav search={search} />
          <SymbolSearch symbol={symbol} onSymbol={onSymbol} demo={demo} />
          <div className="topbar-spacer" />
        </>
      ) : (
        <>
          <SymbolSearch symbol={symbol} onSymbol={onSymbol} demo={demo} />
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

      {demo ? <DemoBadge /> : <HealthBadge health={health} search={search} />}

      {onRefresh && (
        <button className="icon-btn" onClick={onRefresh}
          title="Refresh live data" aria-label="Refresh live data">
          <RefreshCw size={15} className={refreshing ? 'spin' : undefined} />
        </button>
      )}

      <ThemeToggle />

      <NavLink to={`/settings${search}`} className="plan" title="Account & providers">
        <div className="avatar">U</div>
        <div className="plan-text">
          <b>Tradgo.US</b>
          <span>Personal</span>
        </div>
        <ChevronDown size={13} color="var(--text-mute)" />
      </NavLink>
    </header>
  );
}

/**
 * Ticker search with live IBKR suggestions.
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

  const live = health.ibkr === 'OK';
  const degraded = live && (
    health.market_data !== 'OK' || health.providers?.news?.status !== 'OK'
  );

  const title = Object.entries(health.providers || {})
    .map(([k, v]) => `${k}: ${v.status}`)
    .join(' · ');

  return (
    <NavLink
      to={`/settings${search}`}
      className={`conn ${live ? (degraded ? 'warn' : 'live') : 'down'}`}
      title={title || 'Provider status'}
    >
      <i className="dot" />
      {live ? (degraded ? 'LIVE · DEGRADED' : 'LIVE · IBKR') : 'IBKR OFFLINE'}
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
