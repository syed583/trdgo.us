import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Target, Search, ChevronRight, AlertTriangle } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import '../components/earnings-trade.css';
import './tab-scan.css';

// Claude Last -- Pre-Earnings Direction Model v3, styled like the Earnings Trade
// list: cards grouped by earnings date, coloured by signal (BUY green / SELL red
// / NEUTRAL yellow), two-sided and SELL-capable, with a confidence flag.

const PARAMS: { key: string; label: string }[] = [
  { key: 'price_action', label: 'Price' },
  { key: 'options_flow_unusual', label: 'Flow' },
  { key: 'skew_disparity', label: 'Skew' },
  { key: 'eps_rev_dir', label: 'EPS' },
  { key: 'hist_price_reaction', label: 'Hist' },
  { key: 'insider', label: 'Insider' },
  { key: 'peers', label: 'Peers' },
  { key: 'short_interest', label: 'Short' },
  { key: 'prior_guidance', label: 'Guid' },
  { key: 'dark_pool', label: 'Dark' },
];

const sigTier = (sig: string) =>
  sig === 'BUY' ? 'buy' : sig === 'SELL' ? 'sell' : sig === 'PENDING' ? 'none' : 'neutral';

const fmtCap = (v: number) => {
  if (!v) return '';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v}`;
};

const dayLabel = (iso: string) => {
  if (!iso || iso === '￿') return 'Date TBD';
  try {
    return new Date(iso + 'T12:00:00').toLocaleDateString(undefined,
      { weekday: 'short', month: 'short', day: '2-digit' });
  } catch { return iso; }
};

export default function ClaudeLastPage({ ctx, base = '/claude-last', pz = false }:
  { ctx: PageContext; base?: string; pz?: boolean }) {
  const navigate = useNavigate();
  const [days, setDays] = useState(15);
  const [query, setQuery] = useState('');

  const resp = useApi<any>((s) => api2.claudeLastScan(days, s), [days],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const rows: any[] = resp.data?.rows || [];

  const groups = useMemo(() => {
    const q = query.trim().toUpperCase();
    const xs = q
      ? rows.filter((r) => String(r.symbol).toUpperCase().includes(q)
        || String(r.company || '').toUpperCase().includes(q))
      : rows;
    const by: Record<string, any[]> = {};
    for (const r of xs) {
      const k = r.date || '￿';
      (by[k] = by[k] || []).push(r);
    }
    return Object.entries(by)
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([iso, items]) => [iso, items] as [string, any[]]);
  }, [rows, query]);

  const total = groups.reduce((n, [, g]) => n + g.length, 0);
  const leanOf = (d: any) => d.points > d.max / 2 + 0.3 ? 'pos'
    : d.points < d.max / 2 - 0.3 ? 'neg' : 'neu';

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Target size={20} /> {pz ? 'Claude New +0' : 'Claude New'} · Direction v3.1</h1>
        <p>Two-sided, SELL-capable pre-earnings direction. Each box is a stock reporting soon,
          coloured by its signal. BUY ≥ 55 · SELL ≤ 45. Click a card for
          {pz ? ' the +/0/− form (bullish, neutral and bearish parameters).'
              : ' its full breakdown and confidence meter.'}</p>
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
          <i className="buy" /> Buy ≥ 55 <i className="neu" /> Neutral <i className="sell" /> Sell ≤ 45
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
          <div key={day} className="ue-day">
            <div className="ue-day-h">{dayLabel(day)} <span className="at-chip">{items.length}</span></div>
            <div className="ue-grid">
              {items.map((r) => {
                const pending = r.score == null;
                const t = sigTier(r.signal);
                return (
                  <div key={r.symbol} className="ue-cardwrap">
                    <button className={`ue-row sig-${t}`}
                      onClick={() => !pending && navigate(`${base}/${r.symbol}`)}>
                      <div className="ue-row-l">
                        <span className="ue-sym">{r.symbol}</span>
                        <span className="ue-co">{r.company || ''}</span>
                      </div>
                      <div className="ue-row-r">
                        {(r.prior_close != null || r.market_cap != null) && (
                          <span className="ue-quote">
                            {r.prior_close != null && <b>${Number(r.prior_close).toFixed(2)}</b>}
                            {r.market_cap != null && <em>{fmtCap(Number(r.market_cap))}</em>}
                          </span>
                        )}
                        {r.sector && <span className="ue-sector">{r.sector}</span>}
                        {r.time_label && <span className="ue-timing">{r.time_label}</span>}
                        <ChevronRight size={15} className="ue-chev" />
                      </div>
                    </button>

                    <div className={`ue-risk cn-status ${t}`}>
                      {pending ? (
                        <span className="cn-status-h"><Loader2 size={12} className="spin" /> warming…</span>
                      ) : (
                        <>
                          <div className="cn-status-top">
                            <span className="cn-status-h">
                              {r.signal === 'BUY' ? 'BUY — long into earnings'
                                : r.signal === 'SELL' ? 'SELL — short into earnings'
                                  : 'NEUTRAL — check the Straddle Scanner'}
                            </span>
                            <span className="cn-status-score">Score {r.score}/100</span>
                          </div>
                          {r.low_confidence && (
                            <div className="cn-lowconf"><AlertTriangle size={11} /> Low confidence
                              {r.coverage != null ? ` · coverage ${r.coverage}% · agree ${r.agreement}%` : ''}
                              {' — do not trade'}</div>
                          )}
                          <div className="cn-params">
                            {PARAMS.map((p) => {
                              const d = (r.params || []).find((x: any) => x.name === p.key);
                              if (!d) return null;
                              return (
                                <span key={p.key} className={`cn-chip ${leanOf(d)}`}
                                  title={`${p.label}: ${d.points?.toFixed(1)} of ${d.max}`}>
                                  {p.label} <b>{d.points?.toFixed(1)}</b>
                                </span>
                              );
                            })}
                          </div>
                        </>
                      )}
                    </div>
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
