import { Loader2, TrendingUp, TrendingDown, Sunrise } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './intraday-v2.css';

// Pre-market board for Trdgo Stock -- what to buy / sell BEFORE the open, from
// the gap, overnight options flow, the daily trend and prior-day levels. Shown
// on the TODAY view while the market is pre-open; the live intraday v2 board
// takes over once RTH begins.

const money = (v: any) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);
const pct = (v: any) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
const callClass = (c: string) => (c === 'BUY' ? 'im2-buy' : c === 'SELL' ? 'im2-sell' : 'im2-noedge');

function Table({ rows, side }: { rows: any[]; side: 'BUY' | 'SELL' }) {
  if (!rows.length) {
    return <p className="im2-note">No {side === 'BUY' ? 'buy' : 'sell'} candidates pre-market.</p>;
  }
  return (
    <div className="im2-wrap">
      <table className="im2-tbl">
        <thead>
          <tr>
            <th>Symbol</th><th>Call</th><th className="n">Score</th><th className="n">Gap</th>
            <th className="n">Entry</th><th className="n">TP</th><th className="n">SL</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td className="sym">{r.symbol}</td>
              <td><span className={`im2-call ${callClass(r.signal)}`}>{r.signal}</span></td>
              <td className="n">{r.score ?? '—'}</td>
              <td className={`n ${(r.gap_pct || 0) > 0 ? 'pos' : (r.gap_pct || 0) < 0 ? 'neg' : ''}`}>{pct(r.gap_pct)}</td>
              <td className="n">{money(r.entry)}</td>
              <td className="n">{money(r.target)}</td>
              <td className="n">{money(r.stop)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PreMarketBoard() {
  const pm = useApi<any>((s) => api2.intradayPremarket(s), [], { refreshMs: 120000 });
  const d = pm.data;

  if (!d || d.status === 'WARMING' || (pm.initialLoading && !d)) {
    return <div className="im2-board"><p className="im2-note">
      <Loader2 size={15} className="spin" /> Scoring the pre-market tape…</p></div>;
  }

  const buyers = d.buyers || [];
  const sellers = d.sellers || [];
  const c = d.counts || {};

  return (
    <div className="im2-board">
      <div className="im2-health">
        <span><Sunrise size={14} /> Pre-market read · gap + overnight flow + daily trend + prior-day levels</span>
        <span className="ok">Scored {d.scored}/{d.universe}</span>
      </div>
      <div className="im2-stats">
        <div className="im2-stat buy"><TrendingUp size={16} /><div>
          <div className="im2-stat-n">{c.buy ?? 0}</div><div className="im2-stat-l">BUY</div></div></div>
        <div className="im2-stat sell"><TrendingDown size={16} /><div>
          <div className="im2-stat-n">{c.sell ?? 0}</div><div className="im2-stat-l">SELL</div></div></div>
        <div className="im2-stat"><div>
          <div className="im2-stat-n">{c.neutral ?? 0}</div><div className="im2-stat-l">NEUTRAL</div></div></div>
      </div>

      <h3 className="im2-h">Top Buyers (pre-market)</h3>
      <Table rows={buyers} side="BUY" />
      <h3 className="im2-h">Top Sellers (pre-market)</h3>
      <Table rows={sellers} side="SELL" />

      <p className="im2-foot">Pre-open ranking from public pre-market data — entry = pre-market price,
        TP / SL from daily ATR (2:1). Not advice. The live intraday board takes over at the open.</p>
    </div>
  );
}
