import React from 'react';
import { Plus, Search, Star, Trash2, X } from 'lucide-react';
import { api, api2 } from '../api/client';
import type { PageContext } from '../App';
import '../new-dashboard.css';

/**
 * Watchlist - option C (owner, 2026-11-19): TradingView's official embed widgets for the DELAYED
 * price and chart, and ONE call to our own /api/quote when a reader picks a symbol, for live
 * bid/ask.
 *
 * WHY THAT SPLIT. TradingView streams US equities delayed - "as per their requirements we are only
 * allowed to stream delayed data" - and no widget exposes bid/ask at all. So the widgets carry the
 * list and the chart, where delay does not matter much, and the one number that has to be live
 * comes from the provider we already pay for, once, when a reader actually asks for a symbol.
 * Hovering a list costs nothing.
 *
 * NOTHING IS SCRAPED. The widgets are TradingView's own published embeds, loaded from their CDN,
 * and their attribution block is rendered with each one - it is a condition of using them and it
 * is not ours to remove or hide.
 *
 * NO EXCHANGE PREFIXES in our own list: the reader sees AAPL, not NASDAQ:AAPL. The widgets are
 * given the bare ticker and resolve it themselves.
 *
 * The list lives in this browser (localStorage). It is a personal list of symbols, not a trading
 * record, so it does not need a table, a migration or a server round trip.
 */

const STORE = 'trdgo.watchlist.v1';
const DEFAULTS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL'];

function readStore(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || 'null');
    if (Array.isArray(raw) && raw.every((x) => typeof x === 'string')) return raw;
  } catch { /* a corrupted entry is replaced, not reported */ }
  return DEFAULTS;
}

/**
 * Mount one of TradingView's published embeds.
 *
 * Their embeds are a <script> whose JSON body is the configuration, appended inside a container -
 * so React has to place the script itself rather than render it. The whole container is rebuilt
 * when the configuration changes: the widgets have no update API, and leaving a stale one behind
 * is how you end up with two charts stacked in the same box.
 */
function TvWidget({ kind, config, height }: { kind: string; config: object; height: number }) {
  const host = React.useRef<HTMLDivElement | null>(null);
  const json = JSON.stringify(config);
  React.useEffect(() => {
    const el = host.current;
    if (!el) return undefined;
    el.innerHTML = '';
    const mount = document.createElement('div');
    mount.className = 'tradingview-widget-container__widget';
    el.appendChild(mount);
    const s = document.createElement('script');
    s.src = `https://s3.tradingview.com/external-embedding/embed-widget-${kind}.js`;
    s.async = true;
    s.type = 'text/javascript';
    s.innerHTML = json;
    el.appendChild(s);
    return () => { el.innerHTML = ''; };
  }, [kind, json]);
  return (
    <div className="tradingview-widget-container nwl-tv" style={{ height }}>
      <div ref={host} style={{ height: height - 22 }} />
      {/* TradingView's attribution. Required by their terms; do not remove or hide it. */}
      <div className="tradingview-widget-copyright">
        <a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">
          <span className="blue-text">Track all markets on TradingView</span>
        </a>
      </div>
    </div>
  );
}

/** The shell's current theme.
 *
 * PageContext does not carry it - checked, not assumed - and the TradingView embeds take their
 * colour scheme as part of a configuration that cannot be changed after the widget is mounted. So
 * the theme is read from the element the shell actually sets, and watched, which rebuilds the
 * widgets when the reader flips the switch instead of leaving a dark chart on a light page.
 */
function useShellTheme(): 'light' | 'dark' {
  const read = () => (document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
  const [theme, setTheme] = React.useState<'light' | 'dark'>(read);
  React.useEffect(() => {
    const obs = new MutationObserver(() => setTheme(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

export default function NewWatchlistPage(_props: { ctx: PageContext }) {
  const [list, setList] = React.useState<string[]>(readStore);
  const [picked, setPicked] = React.useState<string>(() => readStore()[0] || 'AAPL');
  const [find, setFind] = React.useState('');
  const [results, setResults] = React.useState<any[]>([]);
  const [searchState, setSearchState] = React.useState<{ status?: string; detail?: string }>({});
  const [quote, setQuote] = React.useState<any>(null);
  const [quoteState, setQuoteState] = React.useState<'idle' | 'loading' | 'error'>('idle');
  const dark = useShellTheme() === 'dark';

  React.useEffect(() => { localStorage.setItem(STORE, JSON.stringify(list)); }, [list]);

  // The search is debounced because it is a box people type into; each pass is one call to our own
  // server, which filters a set it built once today - no provider request per keystroke.
  React.useEffect(() => {
    const q = find.trim();
    if (q.length < 1) { setResults([]); setSearchState({}); return undefined; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      api2.watchlistSearch(q, 10, ctrl.signal)
        .then((r: any) => { setResults(r?.matches || []); setSearchState({ status: r?.status, detail: r?.detail }); })
        .catch(() => { /* an aborted keystroke is not an error */ });
    }, 220);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [find]);

  // ONE live call, and only when a reader picks a symbol. This is the only thing on the page that
  // costs a provider request.
  React.useEffect(() => {
    if (!picked) return undefined;
    const ctrl = new AbortController();
    setQuoteState('loading');
    setQuote(null);
    api.quote(picked, ctrl.signal)
      .then((q: any) => { setQuote(q); setQuoteState('idle'); })
      .catch(() => setQuoteState('error'));
    return () => ctrl.abort();
  }, [picked]);

  const add = (sym: string) => {
    const s = sym.toUpperCase();
    setList((prev) => (prev.includes(s) ? prev : [...prev, s]));
    setPicked(s);
    setFind('');
    setResults([]);
  };
  const drop = (sym: string) => setList((prev) => prev.filter((x) => x !== sym));

  // A bid of ZERO is not a bid. The provider returns a literal 0 for bid and ask out of
  // hours, and printing it as $0.00 states a price of nothing as though it were real.
  const money = (v: any) => (v == null || Number(v) === 0 ? '—' : `$${Number(v).toFixed(2)}`);
  /** 32973 seconds is nine hours; say so. */
  const ago = (s: any) => {
    const n = Number(s);
    if (!Number.isFinite(n) || n < 0) return null;
    if (n < 90) return `${Math.round(n)} seconds ago`;
    if (n < 5400) return `${Math.round(n / 60)} minutes ago`;
    if (n < 172800) return `${Math.round(n / 3600)} hours ago`;
    return `${Math.round(n / 86400)} days ago`;
  };
  const age = quote?.quote_age_s ?? quote?.age_s;

  return (
    <div className="nd nwl">
      <header className="page-head nwl-head">
        <div>
          <h1><Star size={20} /> Watchlist</h1>
          <p>
            Delayed prices and charts from TradingView&apos;s own widgets. Live bid and ask are read
            from our market data provider once, when you pick a symbol.
          </p>
        </div>
      </header>

      {/* ---------------------------------------------------------------- search */}
      <section className="nd-card nwl-search">
        <label className="nwl-find">
          <Search size={16} aria-hidden="true" />
          <input
            value={find}
            onChange={(e) => setFind(e.target.value)}
            placeholder="Add a symbol — US common stock and ADRs"
            aria-label="Search for a symbol to add"
          />
          {!!find && (
            <button type="button" className="icon-btn" onClick={() => setFind('')} aria-label="Clear">
              <X size={14} />
            </button>
          )}
        </label>
        {searchState.status === 'UNIVERSE_UNAVAILABLE' && (
          <div className="nd-empty">{searchState.detail}</div>
        )}
        {!!results.length && (
          <ul className="nwl-results">
            {results.map((m) => (
              <li key={m.symbol}>
                <button type="button" onClick={() => add(m.symbol)}>
                  <b>{m.symbol}</b>
                  <span>{m.name || m.description || ''}</span>
                  <i>{m.issue_type}</i>
                  <Plus size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {!results.length && find.trim() && searchState.status === 'OK' && (
          <div className="nd-empty">
            No US common stock or ADR matches “{find.trim()}”. Funds, trusts and index products are
            not offered here.
          </div>
        )}
      </section>

      <div className="nwl-grid">
        {/* -------------------------------------------------------------- the list */}
        <section className="nd-card nwl-list">
          <header className="nd-card-head"><h3>Symbols</h3></header>
          <ul className="nwl-syms">
            {list.map((s) => (
              <li key={s} className={s === picked ? 'is-on' : ''}>
                <button type="button" className="nwl-sym" onClick={() => setPicked(s)}>{s}</button>
                <button type="button" className="icon-btn" onClick={() => drop(s)}
                  aria-label={`Remove ${s}`}><Trash2 size={13} /></button>
              </li>
            ))}
            {!list.length && <li className="nd-empty">Nothing on the list yet.</li>}
          </ul>
          {/* Delayed, and the widget says so itself. */}
          {!!list.length && (
            <TvWidget
              kind="market-quotes"
              height={320}
              config={{
                width: '100%',
                height: 298,
                symbolsGroups: [{ name: 'Watchlist', symbols: list.map((s) => ({ name: s })) }],
                colorTheme: dark ? 'dark' : 'light',
                isTransparent: true,
                locale: 'en',
              }}
            />
          )}
        </section>

        {/* -------------------------------------------------------------- the pick */}
        <section className="nd-card nwl-detail">
          <header className="nd-card-head">
            <h3>{picked || '—'}</h3>
            <span className="nst-mute">{quote?.name || ''}</span>
          </header>

          {/* The one live reading on the page. */}
          <div className="nwl-quote">
            {quoteState === 'loading' && <div className="nd-empty">Reading the live quote…</div>}
            {quoteState === 'error' && <div className="nd-empty">The live quote is unavailable right now.</div>}
            {quoteState === 'idle' && quote && (
              <>
                <div><span>Bid</span><b>{money(quote.bid)}</b></div>
                <div><span>Ask</span><b>{money(quote.ask)}</b></div>
                <div><span>Spread</span>
                  {/* A spread between two prices that are not there is not zero, it is unknown:
                      out of hours the provider returns a literal 0 for both sides. */}
                  <b>{Number(quote.bid) > 0 && Number(quote.ask) > 0
                    ? `$${(Number(quote.ask) - Number(quote.bid)).toFixed(2)}` : '—'}</b></div>
                <div><span>Last</span><b>{money(quote.price)}</b></div>
              </>
            )}
          </div>
          <p className="nwl-note">
            Bid and ask are live from our market data provider, read once when you picked
            {picked ? ` ${picked}` : ' a symbol'}
            {ago(age) ? `, ${ago(age)}` : ''}. The chart and the list below are
            TradingView&apos;s delayed data.
          </p>

          {!!picked && (
            <TvWidget
              kind="advanced-chart"
              height={420}
              config={{
                autosize: true,
                symbol: picked,
                interval: 'D',
                timezone: 'America/New_York',
                theme: dark ? 'dark' : 'light',
                style: '1',
                locale: 'en',
                hide_side_toolbar: true,
                allow_symbol_change: false,
                save_image: false,
              }}
            />
          )}
        </section>
      </div>
    </div>
  );
}
