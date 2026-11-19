import React from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowDown, ArrowUp, ListOrdered, Search } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * Stocks in the redesigned shell (Figma frame 30:251).
 *
 * The first frame where EVERY column exists in the payload: /api/stocks/all returns symbol, name,
 * price, change_percent, market_cap, volume and iv_rank in one request. Nothing had to be dropped and
 * nothing had to be invented - so this page is the design, with the design's own copy.
 *
 * One request for the whole list is the point of the page, and the frame says so in its footer. The
 * page repeats it, because a reader who assumes a per-stock call would expect the prices to be fresher
 * than they are.
 */

type Any = any;

const money = (v: Any): string =>
  (v === null || v === undefined || Number.isNaN(Number(v))
    ? '—' : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

const cap = (v: Any): string => {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toFixed(0)}`;
};

const vol = (v: Any): string => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return String(Math.round(n));
};

const pct = (v: Any): string =>
  (v === null || v === undefined ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`);

type SortKey = 'market_cap' | 'price' | 'change_percent' | 'volume' | 'iv_rank' | 'symbol';

export default function NewStocksPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const navigate = useNavigate();
  const [find, setFind] = React.useState('');
  const [sort, setSort] = React.useState<SortKey>('market_cap');
  const [desc, setDesc] = React.useState(true);

  const q = useApi<Any>((s) => api2.stocksAll(s), [], { refreshMs: 60_000 });
  const d = q.data || {};
  const rows: Any[] = d.rows || [];

  const shown = React.useMemo(() => {
    const f = find.trim().toUpperCase();
    let r = f
      ? rows.filter((x) => String(x.symbol || '').includes(f)
        || String(x.name || '').toUpperCase().includes(f))
      : rows;
    r = [...r].sort((a, b) => {
      if (sort === 'symbol') {
        return String(a.symbol || '').localeCompare(String(b.symbol || '')) * (desc ? -1 : 1);
      }
      const A = Number(a[sort]); const B = Number(b[sort]);
      // a name missing this figure sorts last either way, never as a zero
      if (!Number.isFinite(A) && !Number.isFinite(B)) return 0;
      if (!Number.isFinite(A)) return 1;
      if (!Number.isFinite(B)) return -1;
      return desc ? B - A : A - B;
    });
    return r;
  }, [rows, find, sort, desc]);

  const head = (key: SortKey, label: string, right = true) => (
    <th className={right ? 'r' : ''}>
      <button type="button" className="nsk-sort"
              onClick={() => { if (sort === key) setDesc(!desc); else { setSort(key); setDesc(true); } }}>
        {label}
        {sort === key && (desc ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
      </button>
    </th>
  );

  const open = (sym: string) => navigate(`/ai-insights?symbol=${encodeURIComponent(sym)}`);

  return (
    <div className="nd nst nua nsk">
      <section className="nua-head">
        <h1><ListOrdered size={22} /> Stocks</h1>
        <p>
          The most-active optionable names, with live prices — one request, no per-stock calls.
          Search any other ticker to open it directly.
        </p>
      </section>

      <div className="nst-filters nsk-filters">
        <label className="nst-field nst-find">
          <span>Filter this list</span>
          <span className="nsh-lookup">
            <input value={find} onChange={(e) => setFind(e.target.value)}
                   placeholder="symbol or company"
                   onKeyDown={(e) => {
                     // Enter on a symbol that is not in the list opens it anyway - the frame's promise.
                     if (e.key === 'Enter') {
                       const v = find.trim().toUpperCase();
                       if (v && !shown.some((x) => String(x.symbol) === v)) open(v);
                     }
                   }} />
            <Search size={15} />
          </span>
        </label>
        <span className="nst-count">{shown.length} shown of {rows.length}</span>
      </div>

      <section className="nd-card nst-card">
        <header className="nd-card-head nsk-head"><h3>Covered stocks</h3></header>

        {q.loading && !rows.length && <div className="nd-empty">Loading…</div>}
        {!q.loading && !rows.length && <div className="nd-empty">The screener did not return any names.</div>}
        {!!rows.length && !shown.length && (
          <div className="nd-empty">
            Nothing here matches “{find}”. Press Enter to open it as a ticker anyway.
          </div>
        )}

        {!!shown.length && (
          <div className="nst-scroll">
            <table className="nd-table nst-table nsk-table">
              <thead>
                <tr>
                  {head('symbol', 'Ticker', false)}
                  {head('price', 'Price')}
                  {head('change_percent', 'Change')}
                  {head('market_cap', 'Market cap')}
                  {head('volume', 'Volume')}
                  {head('iv_rank', 'IV rank')}
                </tr>
              </thead>
              <tbody>
                {shown.slice(0, 500).map((r) => (
                  <tr key={r.symbol} onClick={() => open(r.symbol)} className="nsk-row">
                    <td>
                      <b className="nsk-sym">{r.symbol}</b>
                      {r.name && r.name !== r.symbol && <div className="nst-sub">{r.name}</div>}
                    </td>
                    <td className="r nsk-num">{money(r.price)}</td>
                    <td className={`r nsk-num ${r.change_percent == null ? '' : Number(r.change_percent) >= 0 ? 'up' : 'down'}`}>
                      {pct(r.change_percent)}
                    </td>
                    <td className="r nsk-num">{cap(r.market_cap)}</td>
                    <td className="r nsk-num">{vol(r.volume)}</td>
                    <td className="r nsk-num">{r.iv_rank == null ? '—' : Math.round(Number(r.iv_rank))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!!rows.length && (
          <p className="nd-note">
            {rows.length} optionable names from the provider&apos;s screener, each with its last price —
            fetched in one request, so a price here is as fresh as that request, not a live tick.
          </p>
        )}
      </section>

      {isAdmin && (
        <details className="nd-ops">
          <summary>Screener detail (administrator)</summary>
          <div>
            status {d.status || '—'} · rows {rows.length} · count {d.count ?? '—'}
            {!!d.detail && <> · {d.detail}</>}
          </div>
        </details>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The stock list could not be loaded.</div>
      )}
    </div>
  );
}
