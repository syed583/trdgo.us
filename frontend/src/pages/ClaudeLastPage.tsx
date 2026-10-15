import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Target, Search, ChevronRight, AlertTriangle } from 'lucide-react';
import type { PageContext } from '../App';
import { RiskAlertsCard } from '../components/EarningsShared';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import '../components/earnings-trade.css';
import './tab-scan.css';

// Claude Last -- Pre-Earnings Direction Model v3, styled like the Earnings Trade
// list: cards grouped by earnings date, coloured by signal (BUY green / SELL red
// / NEUTRAL yellow), two-sided and SELL-capable, with a confidence flag.

// v2 spec: 14 parameters (Pre_Earnings_Model_v2.pdf §2), in table order.
const PARAMS: { key: string; label: string }[] = [
  { key: 'eps_rev_dir', label: 'EPS' },
  { key: 'options_flow', label: 'Flow' },
  { key: 'unusual_skew', label: 'Unusual' },
  { key: 'prior_guidance', label: 'Guid' },
  { key: 'hist_price_reaction', label: 'Hist' },
  { key: 'price_action', label: 'Price' },
  { key: 'ema_trend', label: 'EMA' },
  { key: 'peers', label: 'Peers' },
  { key: 'disparity', label: 'Disp' },
  { key: 'realized_vs_implied', label: 'R/I' },
  { key: 'insider', label: 'Insider' },
  { key: 'short_interest', label: 'Short' },
  { key: 'dark_pool', label: 'Dark' },
  { key: 'implied_move', label: 'IM' },
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
        <h1><Target size={20} /> {pz ? 'Claude New +0' : 'Claude New'} · Direction v2 · 14-param</h1>
        <p>Two-sided, SELL-capable pre-earnings direction (14 parameters, 100 pts, +
          Implied-Move gate). Each box is a stock reporting soon, coloured by its signal.
          BUY &gt; 70 · SELL &lt; 50. Click a card for
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
          <i className="buy" /> Buy &gt; 70 <i className="neu" /> Neutral <i className="sell" /> Sell &lt; 50
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
                        {(r.time_label || r.report_timing) && (
                          <span className="ue-timing" title="earnings result date and time">
                            {r.date_label ? `${r.date_label} · ` : ''}
                            {r.report_clock ? `${r.report_clock} ET · ` : ''}
                            {r.report_timing || r.time_label}
                            {r.report_timing && r.time_label ? ` (${r.time_label})` : ''}
                          </span>
                        )}
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
                    {!pending && <NewsDropdown symbol={r.symbol} demo={ctx.demo} />}
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

// Per-card News & Alerts dropdown. Lazy: the alerts (one request per symbol) are
// only fetched when the card is expanded, so the list stays light.
function NewsDropdown({ symbol, demo }: { symbol: string; demo?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="cn-news">
      <button className="cn-news-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <AlertTriangle size={12} /> Risk Alerts
        <ChevronRight size={12} className="cn-news-chev"
          style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }} />
      </button>
      {open && (
        <div className="cn-news-body">
          <RiskAlertsCard symbol={symbol} demo={demo} />
        </div>
      )}
    </div>
  );
}
