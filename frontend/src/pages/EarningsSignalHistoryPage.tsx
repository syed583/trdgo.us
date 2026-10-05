import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, X } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './signal-history.css';

const COUNT_OPTS = [4, 6, 8];
const LABEL: Record<string, string> = { BUY: 'B', SELL: 'S', NEUTRAL: 'N' };

function dayLabel(iso?: string | null): string {
  if (!iso) return '';
  try {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit' });
  } catch { return iso.slice(5); }
}

// Column header for the i-th column (0 = oldest shown, last = most recent).
function colHead(i: number, n: number): string {
  const back = n - 1 - i;
  return back === 0 ? 'Last' : `${back + 1} ago`;
}

/**
 * Earnings signal history: each upcoming-earnings name's REAL past earnings
 * events. Every cell is the actual post-earnings move — the stock rose (Buy),
 * fell (Sell) or was flat (Neutral) — newest on the right, with a Now column for
 * the current call. Nothing invented: a stock with fewer reports has empty
 * leading cells.
 */
export default function EarningsSignalHistoryPage({ ctx }: { ctx: PageContext }) {
  const { demo } = ctx;
  const navigate = useNavigate();
  const [count, setCount] = useState(4);
  const [query, setQuery] = useState('');

  const res = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.earningsSignalHistory(count, s)),
    [demo, count],
    { refreshMs: demo ? undefined : 120_000 },
  );
  const d = res.data;
  const n: number = d?.n || count;
  const allRows: any[] = d?.rows || [];
  const rows = useMemo(() => {
    const needle = query.trim().toUpperCase();
    return needle ? allRows.filter((r) => (r.symbol || '').includes(needle)) : allRows;
  }, [allRows, query]);

  return (
    <div className="page sh esh">
      <PageHead
        title="Signal History"
        subtitle={<>How each upcoming-earnings name actually moved at its recent
          earnings — <b>Buy</b> (rose), <b>Sell</b> (fell) or <b>Neutral</b> (flat) —
          with the current call in <b>Now</b>. Real post-earnings moves, not advice.</>}
      />

      <div className="sh-controls">
        <div className="sh-filter sh-search">
          <Search size={13} />
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search ticker…" aria-label="Search ticker" />
          {query && (
            <button className="sh-clear" onClick={() => setQuery('')} title="Clear search">
              <X size={13} />
            </button>
          )}
        </div>
        <div className="sh-filter">
          <label>Earnings</label>
          <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {COUNT_OPTS.map((c) => <option key={c} value={c}>Last {c}</option>)}
          </select>
        </div>
        <div className="sh-legend">
          <span><i className="sh-dot buy" /> Up (Buy)</span>
          <span><i className="sh-dot sell" /> Down (Sell)</span>
          <span><i className="sh-dot neutral" /> Flat</span>
          <span><i className="sh-dot none" /> No report</span>
        </div>
      </div>

      {demo ? (
        <div className="sh-empty">Disabled in demo mode.</div>
      ) : res.initialLoading || d?.status === 'LOADING'
          || (res.loading && !allRows.length) ? (
        <div className="sh-empty">Loading earnings reactions…</div>
      ) : d?.status !== 'OK' || !allRows.length ? (
        <div className="sh-empty">
          {d?.detail || 'No earnings reactions available yet — warming up.'}
        </div>
      ) : !rows.length ? (
        <div className="sh-empty">No tickers match “{query}”.</div>
      ) : (
        <div className="sh-table-wrap">
          <table className="sh-table">
            <thead>
              <tr>
                <th className="sh-sym-h">Stock</th>
                {Array.from({ length: n }).map((_, i) => (
                  <th key={i} className="sh-day-h">{colHead(i, n)}</th>
                ))}
                <th className="sh-latest-h">Now</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <td className="sh-sym"
                    onClick={() => navigate(`/earnings-trade/${r.symbol}${ctx.search}`)}>
                    {r.symbol}
                  </td>
                  {r.cells.map((c: any, i: number) => (
                    <td key={i} className="sh-cell sh-rx">
                      {c.signal ? (
                        <>
                          <span className={`sh-chip ${c.signal.toLowerCase()}`}
                            title={`${dayLabel(c.date)} · ${c.move_pct > 0 ? '+' : ''}${c.move_pct}%`}>
                            {LABEL[c.signal]}
                          </span>
                          <span className="sh-rx-date">{dayLabel(c.date)}</span>
                        </>
                      ) : (
                        <span className="sh-chip none" title="no report">·</span>
                      )}
                    </td>
                  ))}
                  <td className="sh-cell">
                    <span className={`sh-chip strong ${(r.now || 'none').toLowerCase()}`}
                      title={r.now ? `Current call: ${r.now}` : 'no current call'}>
                      {r.now ? LABEL[r.now] : '·'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="sh-foot">
            <b>Source:</b> real post-earnings price moves (UW) — the stock's actual
            1-day reaction to each of its recent reports.
            <ul className="sh-source">
              <li><b>Columns</b> — the stock's recent earnings, oldest left, most
                recent (<b>Last</b>) on the right; the date is under each chip.</li>
              <li><b>Up / Down / Flat</b> — the real move: rose (Buy), fell (Sell)
                or ~flat (Neutral). Hover for the exact %.</li>
              <li><b>Now</b> — the current Earnings Trade call.</li>
              <li><b>Blank ·</b> — the stock has no earlier earnings report.</li>
            </ul>
            Click a ticker for its Earnings Trade.
          </div>
        </div>
      )}
    </div>
  );
}
