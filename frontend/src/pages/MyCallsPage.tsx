import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { TrendingUp, History, Loader2 } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { num } from '../lib/format';
import EarningsTradePage from './EarningsTradePage';
import './my-calls.css';

// -----------------------------------------------------------------------------
// My Calls: the same earnings engine view as Earnings Trade, scored on the
// "Final Earnings Structure" parameter profile ("final"), plus a Call History
// track-record of how past earnings calls resolved.
//   Tab 1 "My Calls"     = the upcoming-earnings list / per-stock analysis
//                          (reuses EarningsTradePage with profile="final").
//   Tab 2 "Call History" = each name's last real post-earnings reactions.
// -----------------------------------------------------------------------------

const CALL_TONE: Record<string, string> = {
  BUY: 'up', SELL: 'down', NEUTRAL: 'flat', STRADDLE: 'up', STRANGLE: 'up', 'NO TRADE': 'flat',
};

function CallHistory({ search }: { search: string }) {
  const q = useApi<any>((s) => api2.earningsSignalHistory(6, s), [], { refreshMs: 120_000 });
  const rows: any[] = q.data?.rows || [];
  if (q.loading && !q.data) {
    return <div className="mc-empty"><Loader2 size={22} className="mc-spin" /><p>Loading call history…</p></div>;
  }
  if (!rows.length) {
    return (
      <div className="mc-empty">
        <History size={26} />
        <p>No resolved calls yet. Each name's past post-earnings reactions appear here as they settle.</p>
      </div>
    );
  }
  return (
    <div className="ets-card mc-htable">
      <div className="mc-hint">
        Each row is a reporting name: its last real post-earnings moves (oldest → newest),
        then the current call. Green = moved up (Buy), red = down (Sell).
      </div>
      {rows.map((r) => (
        <div key={r.symbol} className="mc-hrow2">
          <Link className="mc-hsym" to={`/my-calls/${r.symbol}${search}`}>{r.symbol}</Link>
          <div className="mc-cells">
            {(r.cells || []).map((c: any, i: number) => (
              <span key={i} className={`mc-cell ${c.signal ? CALL_TONE[c.signal] || 'flat' : 'empty'}`}
                title={c.label || c.date || ''}>
                {c.move_pct == null ? '·' : `${c.move_pct > 0 ? '+' : ''}${num(c.move_pct, 1)}%`}
              </span>
            ))}
          </div>
          <span className={`mc-now ${r.now ? CALL_TONE[r.now] || 'flat' : 'empty'}`}>
            {r.now || '—'}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function MyCallsPage({ ctx }: { ctx: PageContext }) {
  const { symbol: pathSym } = useParams();
  const [tab, setTab] = useState<'calls' | 'history'>('calls');

  // A ticker is selected -> show the full analysis on the "final" profile,
  // reusing the Earnings Trade detail under the /my-calls route.
  if (pathSym) {
    return <EarningsTradePage ctx={ctx} base="/my-calls" profile="final" embedded />;
  }

  return (
    <div className="ets-page mc-page">
      <div className="mc-top">
        <h1 className="mc-title">My Calls</h1>
        <p className="mc-sub">
          Upcoming earnings scored on the Final Earnings Structure — Equity (100-pt) and
          Options (100-pt) — with the call history for each name.
        </p>
      </div>

      <div className="mc-tabs">
        <button className={`mc-tab ${tab === 'calls' ? 'on' : ''}`} onClick={() => setTab('calls')}>
          <TrendingUp size={15} /> My Calls
        </button>
        <button className={`mc-tab ${tab === 'history' ? 'on' : ''}`} onClick={() => setTab('history')}>
          <History size={15} /> Call History
        </button>
      </div>

      {tab === 'calls'
        ? <EarningsTradePage ctx={ctx} base="/my-calls" profile="final" embedded />
        : <CallHistory search={ctx.search} />}
    </div>
  );
}
