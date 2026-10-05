import { Fragment, useMemo, useState } from 'react';
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
 * Earnings signal history: per upcoming-earnings name, the Earnings Trade call
 * over recent sessions -- a Stock row (the equity decision) and an Options row
 * (the options positioning lean) stacked per ticker, like Signal History.
 * Today's column is live; earlier sessions accumulate as the daily capture runs.
 */
export default function EarningsSignalHistoryPage({ ctx }: { ctx: PageContext }) {
  const { demo } = ctx;
  const navigate = useNavigate();
  const [sessions, setSessions] = useState(10);
  const [end, setEnd] = useState('');
  const [query, setQuery] = useState('');

  const res = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.earningsSignalHistory(sessions, end, s)),
    [demo, sessions, end],
    { refreshMs: demo ? undefined : 120_000 },
  );
  const d = res.data;
  const dates: string[] = d?.dates || [];
  const allRows: any[] = d?.rows || [];
  const rows = useMemo(() => {
    const needle = query.trim().toUpperCase();
    return needle ? allRows.filter((r) => (r.symbol || '').includes(needle)) : allRows;
  }, [allRows, query]);

  return (
    <div className="page sh esh">
      <PageHead
        title="Signal History"
        subtitle={<>The Earnings Trade call per upcoming-earnings name over recent
          sessions — a <b>Stock</b> row (equity) and an <b>Options</b> row
          (positioning), each <b>Buy</b> / <b>Sell</b> / <b>Neutral</b>.
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
                <th className="sh-type-h"></th>
                {dates.map((dt) => <th key={dt} className="sh-day-h">{dayLabel(dt)}</th>)}
                <th className="sh-latest-h">Now</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.symbol}>
                {([['stock', r.latest_stock, 'Stock'], ['options', r.latest_options, 'Options']] as const)
                  .map(([key, latest, typeLabel], ri) => (
                    <tr key={`${r.symbol}-${key}`} className={ri === 0 ? 'sh-rowtop' : ''}>
                      {ri === 0 && (
                        <td className="sh-sym" rowSpan={2}
                          onClick={() => navigate(`/earnings-trade/${r.symbol}${ctx.search}`)}>
                          {r.symbol}
                        </td>
                      )}
                      <td className={`sh-type ${key}`}>{typeLabel}</td>
                      {r.cells.map((c: any, i: number) => {
                        const sig = c[key as string];
                        return (
                          <td key={i} className="sh-cell">
                            <span className={`sh-chip ${(sig || 'none').toLowerCase()} ${c.carried ? 'carried' : ''}`}
                              title={sig ? `${typeLabel}: ${sig}${c.carried ? ' (carried forward)' : ''}${c.live ? ' (live)' : ''}` : 'no data'}>
                              {sig ? LABEL[sig] : '·'}
                            </span>
                          </td>
                        );
                      })}
                      <td className="sh-cell">
                        <span className={`sh-chip strong ${((latest as string) || 'none').toLowerCase()}`}>
                          {latest ? LABEL[latest as string] : '·'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
          <div className="sh-foot">
            <b>Source:</b> the Earnings Trade engine — the same call the colored
            cards and the detail page show.
            <ul className="sh-source">
              <li><b>Stock row</b> — the equity Buy/Sell/Neutral decision that session.</li>
              <li><b>Options row</b> — the options positioning lean that session.</li>
              <li><b>Now column</b> — the live current call.</li>
              <li><b>Dimmed cells</b> — a prior call carried forward across a gap day.</li>
              <li><b>Blank ·</b> — no call captured that day.</li>
            </ul>
            Click a ticker for its Earnings Trade.
          </div>
        </div>
      )}
    </div>
  );
}
