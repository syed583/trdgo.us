import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, X } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './signal-history.css';

const SESSION_OPTS = [5, 10, 15, 20, 30];
const LABEL: Record<string, string> = { BUY: 'B', SELL: 'S', NEUTRAL: 'N' };

function dayLabel(iso: string): string {
  try {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit' });
  } catch { return iso.slice(5); }
}

/**
 * Past Earnings Trade signal for the upcoming-earnings names -- one row per
 * stock, a cell per session, Buy / Sell / Neutral. `kind` selects which call:
 * the equity "stock" decision, or the "options" positioning lean. Today's
 * column is live; the earlier sessions accumulate as the daily capture runs.
 */
export default function EarningsSignalHistoryPage({
  ctx, kind, title,
}: { ctx: PageContext; kind: 'stock' | 'options'; title: string }) {
  const { demo } = ctx;
  const navigate = useNavigate();
  const [sessions, setSessions] = useState(10);
  const [end, setEnd] = useState('');
  const [query, setQuery] = useState('');

  const res = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.earningsSignalHistory(kind, sessions, end, s)),
    [demo, kind, sessions, end],
    { refreshMs: demo ? undefined : 120_000 },
  );
  const d = res.data;
  const dates: string[] = d?.dates || [];
  const allRows: any[] = d?.rows || [];
  const rows = useMemo(() => {
    const needle = query.trim().toUpperCase();
    return needle ? allRows.filter((r) => (r.symbol || '').includes(needle)) : allRows;
  }, [allRows, query]);
  const what = kind === 'options' ? 'options positioning' : 'equity';

  return (
    <div className="page sh esh">
      <PageHead
        title={title}
        subtitle={<>The Earnings Trade {what} call — <b>Buy</b>, <b>Sell</b> or{' '}
          <b>Neutral</b> — per upcoming-earnings name, over recent sessions.
          Analysis, not advice.</>}
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
          <label>Sessions</label>
          <select value={sessions} onChange={(e) => setSessions(Number(e.target.value))}>
            {SESSION_OPTS.map((n) => <option key={n} value={n}>Last {n}</option>)}
          </select>
        </div>
        <div className="sh-filter">
          <label>Up to date</label>
          <input type="date" value={end} max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setEnd(e.target.value)} />
          {end && (
            <button className="sh-clear" onClick={() => setEnd('')} title="Clear date">
              <X size={13} />
            </button>
          )}
        </div>
        <div className="sh-legend">
          <span><i className="sh-dot buy" /> Buy</span>
          <span><i className="sh-dot sell" /> Sell</span>
          <span><i className="sh-dot neutral" /> Neutral</span>
          <span><i className="sh-dot none" /> No data</span>
        </div>
      </div>

      {demo ? (
        <div className="sh-empty">Disabled in demo mode.</div>
      ) : res.initialLoading ? (
        <div className="sh-empty">Loading earnings signal history…</div>
      ) : d?.status !== 'OK' || !allRows.length ? (
        <div className="sh-empty">
          {d?.detail || 'No earnings signals captured yet — today fills in from the '
            + 'live decision and the record builds over the next sessions.'}
        </div>
      ) : !rows.length ? (
        <div className="sh-empty">No tickers match “{query}”.</div>
      ) : (
        <div className="sh-table-wrap">
          <table className="sh-table">
            <thead>
              <tr>
                <th className="sh-sym-h">Stock</th>
                {dates.map((dt) => <th key={dt} className="sh-day-h">{dayLabel(dt)}</th>)}
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
                    <td key={i} className="sh-cell">
                      <span className={`sh-chip ${(c.signal || 'none').toLowerCase()} ${c.carried ? 'carried' : ''}`}
                        title={c.signal ? `${c.signal}${c.carried ? ' (carried forward)' : ''}${c.live ? ' (live)' : ''}` : 'no data'}>
                        {c.signal ? LABEL[c.signal] : '·'}
                      </span>
                    </td>
                  ))}
                  <td className="sh-cell">
                    <span className={`sh-chip strong ${(r.latest || 'none').toLowerCase()}`}>
                      {r.latest ? LABEL[r.latest] : '·'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="sh-foot">
            <b>Source:</b> the Earnings Trade engine — the same call the colored
            cards and the detail page show.
            <ul className="sh-source">
              <li><b>Daily columns</b> — the {what} decision recorded that session.</li>
              <li><b>Now column</b> — the live current decision.</li>
              <li><b>Dimmed cells</b> — a prior decision carried forward across a gap day.</li>
              <li><b>Blank ·</b> — no decision captured that day.</li>
            </ul>
            Click a ticker for its Earnings Trade.
          </div>
        </div>
      )}
    </div>
  );
}
