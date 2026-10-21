import { useState } from 'react';
import { Loader2, Receipt } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './strategy-trades.css';

/**
 * Strategy Trades -- demo (paper) 100-share trades opened from the intraday
 * model signals, tracked live until stop / target / score-drop / timed exit.
 * Paper only; no real orders. Live P&L refreshes every ~15s.
 */

const money = (n: number | null | undefined) =>
  n == null ? '—' : `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (n: number | null | undefined, suffix = '') =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${n}${suffix}`;
const px = (n: number | null | undefined) =>
  n == null ? '—' : Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const STATUS_LABEL: Record<string, string> = {
  OPEN: 'Open', STOP: 'Stopped', TARGET: 'Target', TIMED_EXIT: 'Timed Exit', SCORE_EXIT: 'Score Exit',
};

export default function StrategyTradesPage() {
  const [strategy, setStrategy] = useState('all');
  const q = useApi<any>((s) => api2.strategyTrades(strategy, '', s), [strategy], { refreshMs: 15_000 });
  const d = q.data;
  const rows: any[] = d?.rows || [];
  const strategies: any[] = d?.strategies || [];
  const total = d?.total_profit ?? 0;

  return (
    <div className="page st-wrap">
      <div className="ts-head">
        <h1><Receipt size={20} /> Strategy Trades</h1>
        <p>Demo 100-share paper trades opened from the intraday model signals, tracked live until
          stop / target / score-drop / timed exit. Paper only — no real orders.</p>
      </div>

      <div className="st-bar">
        <span className={`st-total ${total > 0 ? 'pos' : total < 0 ? 'neg' : ''}`}>
          Profit <b>{money(total)}</b>
        </span>
        <label className="st-filter">Strategy
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="all">All Strategies</option>
            {strategies.map((s) => <option value={s.key} key={s.key}>{s.label}</option>)}
          </select>
        </label>
        {d && <span className="ma-n">{d.count} trades · {d.open} open · {d.wins} winners
          {d.date ? ` · ${d.date}` : ''}{d.after_close ? ' · market closed' : d.rth ? ' · live' : ''}</span>}
      </div>

      {q.initialLoading ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Loading trades…</div>
      ) : rows.length === 0 ? (
        <div className="ma-note">No demo trades yet today. Trades open automatically when an intraday
          model signals BUY or SELL during market hours.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="ma-tbl st-tbl">
            <thead>
              <tr>
                <th className="l">Entry Time</th><th className="l">L/S</th><th className="l">Symbol</th>
                <th className="l">Strategy</th><th>Shares</th><th>Entry</th><th>Chg $</th><th>Last</th>
                <th>Profit</th><th>Profit %</th><th>Stop</th><th>Target</th><th className="l">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const win = (r.profit ?? 0) > 0;
                const loss = (r.profit ?? 0) < 0;
                return (
                  <tr key={r.id} className={win ? 'st-win' : loss ? 'st-loss' : ''}>
                    <td className="l">{r.entry_time}</td>
                    <td className="l"><span className={`st-ls ${r.side === 'LONG' ? 'long' : 'short'}`}>
                      {r.side === 'LONG' ? 'Long' : 'Short'}</span></td>
                    <td className="l"><b>{r.symbol}</b></td>
                    <td className="l ma-n">{r.strategy_label?.replace(' Intraday', '')}</td>
                    <td>{r.shares}</td>
                    <td>{px(r.entry_price)}</td>
                    <td className={r.chg > 0 ? 'pos' : r.chg < 0 ? 'neg' : ''}>{signed(r.chg)}</td>
                    <td>{px(r.last_price)}</td>
                    <td className={win ? 'pos' : loss ? 'neg' : ''}><b>{money(r.profit)}</b></td>
                    <td className={win ? 'pos' : loss ? 'neg' : ''}>{signed(r.profit_pct, '%')}</td>
                    <td className="ma-n">{px(r.stop)}</td>
                    <td className="ma-n">{px(r.target)}</td>
                    <td className="l"><span className={`st-stat ${r.status}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="st-note">Live P&amp;L updates every ~15s. One trade per symbol per strategy per day,
        no re-entry. Entry/stop/target come from each model's ATR levels; a position closes on stop,
        target, a 15‑point score drop, or the 4:00pm ET close (Timed Exit).</div>
    </div>
  );
}
