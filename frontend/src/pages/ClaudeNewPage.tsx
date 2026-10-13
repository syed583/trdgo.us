import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, TrendingUp, Search } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Tab A -- Pre-Earnings Stock Direction v2. Card list like the Earnings tab:
// one box per stock, grouped by earnings date, coloured by signal (BUY green /
// SELL red / NEUTRAL yellow). Each card shows Claude New's own 9 parameters.

const PARAMS: { key: string; label: string }[] = [
  { key: 'options', label: 'Options' },
  { key: 'price_action', label: 'Price' },
  { key: 'insider', label: 'Insider' },
  { key: 'peers', label: 'Peers' },
  { key: 'eps_rev', label: 'EPS' },
  { key: 'hist_reaction', label: 'Hist' },
  { key: 'rev_rev', label: 'Rev' },
  { key: 'prior_guidance', label: 'Guid' },
  { key: 'dark_pool', label: 'Dark' },
];

const tier = (sig: string) =>
  sig === 'BUY' ? 'buy' : sig === 'SELL' ? 'sell' : sig === 'PENDING' ? 'pend' : 'neu';

export default function ClaudeNewPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const [days, setDays] = useState(15);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<string | null>(null);

  const resp = useApi<any>((s) => api2.tabAScan(days, s), [days],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const rows: any[] = resp.data?.rows || [];

  // Group by earnings date, keeping the scan's score order within each day.
  const groups = useMemo(() => {
    const q = query.trim().toUpperCase();
    const xs = q
      ? rows.filter((r) => String(r.symbol).toUpperCase().includes(q)
        || String(r.company || '').toUpperCase().includes(q))
      : rows;
    // Group by the actual earnings date (ISO), ordered nearest first.
    const by: Record<string, any[]> = {};
    for (const r of xs) {
      const k = r.date || '￿';
      (by[k] = by[k] || []).push(r);
    }
    return Object.entries(by)
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([iso, items]) => [iso, items] as [string, any[]]);
  }, [rows, query]);

  const dayLabel = (iso: string) => {
    if (!iso || iso === '￿') return 'Date TBD';
    try {
      return new Date(iso + 'T12:00:00').toLocaleDateString(undefined,
        { weekday: 'short', month: 'short', day: '2-digit' });
    } catch { return iso; }
  };

  const total = groups.reduce((n, [, g]) => n + g.length, 0);

  return (
    <div className="page">
      <div className="ts-head">
        <h1><TrendingUp size={20} /> Claude New · 13 Oct</h1>
        <p>Pre-earnings direction (v2). Each box is a stock reporting soon, coloured by its
          signal — <b className="pos">BUY ≥ 65</b>, <b className="neu">NEUTRAL</b>,
          <b className="neg"> SELL ≤ 35</b>. The 9 parameters are Claude New's own.</p>
      </div>

      <div className="cn-toolbar">
        <label>Window
          <select value={days} onChange={(e) => setDays(+e.target.value)}>
            <option value={7}>7d</option><option value={15}>15d</option><option value={21}>21d</option>
          </select>
        </label>
        <span className="cn-search">
          <Search size={13} />
          <input value={query} placeholder="Filter ticker / company…" spellCheck={false}
            onChange={(e) => setQuery(e.target.value)} />
        </span>
        <span className="cn-legend">
          <i className="buy" /> Buy <i className="neu" /> Neutral <i className="sell" /> Sell
        </span>
        <span className="at-chip">{total}</span>
      </div>

      {resp.initialLoading && !rows.length ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Scoring upcoming reports…</div>
      ) : !total ? (
        <div className="es-empty">{query ? `No matches for “${query}”.`
          : 'No upcoming reports in the window.'}</div>
      ) : (
        groups.map(([day, items]) => (
          <div key={day} className="cn-day">
            <div className="cn-day-h">{dayLabel(day)} <span className="at-chip">{items.length}</span></div>
            <div className="cn-grid">
              {items.map((r) => {
                const pending = r.score == null;
                const t = tier(r.signal);
                const pm: Record<string, any> = {};
                for (const p of r.params || []) pm[p.key] = p;
                const isOpen = open === r.symbol;
                const leanOf = (d: any) => d.points > d.max / 2 + 0.3 ? 'pos'
                  : d.points < d.max / 2 - 0.3 ? 'neg' : 'neu';
                return (
                  <div key={r.symbol}
                    className={`cn-card sig-${t}${pending ? '' : ' cn-link'}${isOpen ? ' cn-exp' : ''}`}
                    onClick={() => !pending && setOpen(isOpen ? null : r.symbol)}
                    title={pending ? '' : 'Show the parameter breakdown'}>
                    <div className="cn-card-h">
                      <span className="cn-sym">{r.symbol}</span>
                      {r.time_label && <span className="cn-time">{r.time_label}</span>}
                      <span className="cn-co">{r.company || ''}</span>
                      <span className="cn-right">
                        {pending ? <span className="cn-badge pend">warming</span> : (
                          <>
                            <span className={`cn-score ${t}`}>{r.score}<em>/100</em></span>
                            <span className={`cn-badge ${t}`}>{r.signal}</span>
                          </>
                        )}
                      </span>
                    </div>
                    {!pending && !isOpen && (
                      <div className="cn-params">
                        {PARAMS.map((p) => {
                          const d = pm[p.key];
                          if (!d) return null;
                          return (
                            <span key={p.key} className={`cn-chip ${leanOf(d)}`}
                              title={`${p.label}: ${d.points?.toFixed(1)} of ${d.max}`}>
                              {p.label} <b>{d.points?.toFixed(1)}</b>
                            </span>
                          );
                        })}
                      </div>
                    )}
                    {!pending && isOpen && (
                      <div className="cn-breakdown">
                        <div className="cn-bd-h">
                          Parameter breakdown — points / max (raw lean −1…+1)
                          <button className="cn-open-link"
                            onClick={(e) => { e.stopPropagation(); navigate(`/ai-insights/${r.symbol}`); }}>
                            Open {r.symbol} →
                          </button>
                        </div>
                        {(r.params || []).map((d: any) => (
                          <div key={d.name} className={`cn-bd-row ${leanOf(d)}`}>
                            <span className="cn-bd-lbl">{d.label}</span>
                            <span className="cn-bd-pts"><b>{d.points?.toFixed(1)}</b> / {d.max}</span>
                            <span className="cn-bd-raw">{d.bias != null
                              ? `${d.bias > 0 ? '+' : ''}${d.bias}` : 'no data'}</span>
                            {d.sub && (
                              <span className="cn-bd-sub">
                                {d.sub.map((s: any) => `${s.name} ${s.points?.toFixed(1)}/${s.max}`).join(' · ')}
                              </span>
                            )}
                          </div>
                        ))}
                        <div className="cn-bd-total">Score = sum of rows =
                          <b> {r.score}</b> / 100 → <b className={t}>{r.signal}</b></div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
