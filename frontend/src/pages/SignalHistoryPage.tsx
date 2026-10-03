import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './signal-history.css';

const SESSION_OPTS = [5, 10, 15, 20, 30];

const LABEL: Record<string, string> = { BUY: 'B', SELL: 'S', NEUTRAL: 'N' };

function dayLabel(iso: string): string {
  // "2026-10-03" -> "Oct 03"
  try {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit' });
  } catch { return iso.slice(5); }
}

export default function SignalHistoryPage({ ctx }: { ctx: PageContext }) {
  const { demo } = ctx;
  const navigate = useNavigate();
  const [sessions, setSessions] = useState(10);
  const [end, setEnd] = useState('');   // YYYY-MM-DD "up to" date, '' = latest

  const q = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.signalHistory(sessions, end, s)),
    [demo, sessions, end],
    { refreshMs: demo ? undefined : 300_000 },
  );
  const d = q.data;
  const dates: string[] = d?.dates || [];
  const rows: any[] = d?.rows || [];

  return (
    <div className="page sh">
      <PageHead
        title="Signal History"
        subtitle={<>The last 10 sessions of the model's call — <b>Buy</b>,{' '}
          <b>Sell</b> or <b>Neutral</b> — for each watched stock. Analysis, not advice.</>}
      />

      <div className="sh-controls">
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
      ) : q.initialLoading ? (
        <div className="sh-empty">Loading signal history…</div>
      ) : d?.status !== 'OK' || !rows.length ? (
        <div className="sh-empty">
          {d?.detail || 'No daily snapshots captured yet — this fills in over the next sessions.'}
        </div>
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
                  <td className="sh-sym" onClick={() => navigate(`/trade-plan/${r.symbol}${ctx.search}`)}>
                    {r.symbol}
                  </td>
                  {r.cells.map((c: any, i: number) => (
                    <td key={i} className="sh-cell">
                      <span className={`sh-chip ${(c.signal || 'none').toLowerCase()}`}
                        title={c.signal ? `${c.signal}${c.score != null ? ` · ${Math.round(c.score)}` : ''}` : 'no data'}>
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
          <p className="sh-foot">Each cell is the model's decision that session (click a
            ticker for its Trade Plan). Built from the app's own daily score snapshots.</p>
        </div>
      )}
    </div>
  );
}
