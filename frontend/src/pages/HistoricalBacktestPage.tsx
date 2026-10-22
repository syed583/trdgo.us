import { Loader2, FlaskConical } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './forward-scoreboard.css';

/**
 * Track B — 1–2 year PARTIAL-FACTOR historical earnings backtest (SIMULATED).
 * Point-in-time reconstruction uses only price-action + prior-beat history
 * (options/IV/flow/etc. are not retained historically). Entries T-3/T-2/T-1,
 * BUY/SELL split, net-of-cost returns, event-level consensus portfolio drawdown.
 */

const sp = (n: number | null | undefined, suf = '%') =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${Number(n).toFixed(2)}${suf}`;
const tone = (n: number | null | undefined) => (n == null ? '' : n > 0 ? 'pos' : n < 0 ? 'neg' : '');

function PerfRow({ label, p }: { label: string; p: any }) {
  if (!p) return null;
  const c = p.cost || {}, z = p.zero_cost || {};
  return (
    <tr>
      <td className="l"><b>{label}</b></td>
      <td>{c.n ?? '—'}</td>
      <td className={c.win_rate != null && c.win_rate >= 50 ? 'pos' : 'neg'}>{c.win_rate == null ? '—' : `${c.win_rate}%`}</td>
      <td className={tone(z.avg_pct)}>{sp(z.avg_pct)}</td>
      <td className={tone(c.avg_pct)}>{sp(c.avg_pct)}</td>
      <td className={tone(c.median_pct)}>{sp(c.median_pct)}</td>
      <td className={c.profit_factor != null && c.profit_factor !== 'inf' && c.profit_factor >= 1 ? 'pos' : 'neg'}>
        {c.profit_factor == null ? '—' : c.profit_factor}</td>
    </tr>
  );
}

export default function HistoricalBacktestPage() {
  const q = useApi<any>((s) => api2.historicalBacktest(s), [], { refreshMs: 8_000 });
  const d = q.data;

  if (q.initialLoading || d?.status === 'COMPUTING') return (
    <div className="page"><div className="es-empty"><Loader2 size={18} className="spin" /> Reconstructing point-in-time signals & prices over 18 months… (first load can take ~1 min)</div></div>
  );
  if (!d || d.status !== 'OK') return (
    <div className="page"><div className="ma-note">{d?.detail || 'Backtest unavailable.'}</div></div>
  );

  const t = d.by_entry_timing || {}, pf = d.portfolio || {}, cs = d.consensus_side_distribution || {};
  const models = Object.entries(d.by_model || {});

  const Port = ({ label, g }: { label: string; g: any }) => !g ? null : (
    <div className="fsb-stat">
      <span>{label} ({g.net?.positions} pos, {g.net?.trading_days}d)</span>
      <b className={tone(g.net?.total_return_pct)}>{sp(g.net?.total_return_pct)} net</b>
      <small>max DD <span className="neg">{sp(g.net?.max_drawdown_pct)}</span></small>
    </div>
  );

  return (
    <div className="page fsb-wrap">
      <div className="ts-head">
        <h1><FlaskConical size={20} /> Historical Backtest (Track B)</h1>
        <p><b>SIMULATED · PARTIAL-FACTOR</b> — 1–2 year point-in-time earnings backtest.
          Not live performance; never mixed with the genuine recorded predictions.</p>
      </div>

      <div className="fsb-note warn" style={{ marginBottom: 12 }}>
        <b>⚠ PARTIAL-FACTOR SIMULATION.</b> {d.disclaimer}
      </div>

      <div className="fsb-stats">
        <div className="fsb-stat"><span>Window</span><b>{d.months} mo</b></div>
        <div className="fsb-stat"><span>Symbols</span><b>{d.symbols_tested}</b></div>
        <div className="fsb-stat"><span>Events</span><b>{d.events_tested}</b></div>
        <div className="fsb-stat"><span>Model predictions</span><b>{d.model_predictions_count}</b></div>
        <div className="fsb-stat"><span>Consensus trades</span><b>{d.consensus_trades}</b>
          <small>BUY {cs.BUY} · SELL {cs.SELL}</small></div>
      </div>

      <h3 className="fsb-h">Performance <small className="fsb-sub">(prediction-level: per model×event×timing)</small></h3>
      <div style={{ overflowX: 'auto' }}>
        <table className="ma-tbl fsb-tbl">
          <thead><tr><th className="l">Group</th><th>Trades</th><th>Win rate</th>
            <th>Avg (zero-cost)</th><th>Avg (net)</th><th>Median net</th><th>Profit factor</th></tr></thead>
          <tbody>
            <PerfRow label="Overall" p={d.overall} />
            <PerfRow label="BUY" p={d.by_side?.BUY} />
            <PerfRow label="SELL" p={d.by_side?.SELL} />
            <PerfRow label="T-1 entry" p={t['T-1']} />
            <PerfRow label="T-2 entry" p={t['T-2']} />
            <PerfRow label="T-3 entry" p={t['T-3']} />
          </tbody>
        </table>
      </div>

      <h3 className="fsb-h">Portfolio — realistic sizing <small className="fsb-sub">(fixed 10%/trade, ≤10 concurrent, cash otherwise — the honest number)</small></h3>
      <div className="fsb-stats">
        {['T-1', 'T-2', 'T-3', 'all_timings'].map((k) => {
          const g = (d.portfolio_realistic || {})[k]; if (!g) return null;
          return (
            <div className="fsb-stat" key={k}>
              <span>{k === 'all_timings' ? 'All timings*' : k} ({g.positions_taken} pos)</span>
              <b className={tone(g.total_return_pct)}>{sp(g.total_return_pct)} total</b>
              <small>max DD <span className="neg">{sp(g.max_drawdown_pct)}</span></small>
            </div>
          );
        })}
      </div>

      <h3 className="fsb-h">Portfolio — fully-invested equal-weight <small className="fsb-sub">(daily-marked; amplifies when few names are open — kept for reference)</small></h3>
      <div className="fsb-stats">
        <Port label="T-1" g={pf['T-1']} />
        <Port label="T-2" g={pf['T-2']} />
        <Port label="T-3" g={pf['T-3']} />
        <Port label="All timings*" g={pf['all_timings']} />
      </div>
      <div className="fsb-note" style={{ marginTop: 4 }}><small>*All-timings combines T-1/T-2/T-3 as three separate entry strategies per event; per-timing books hold one position per event. The fully-invested book stays ~100% in whatever is open, so its magnitude is a sizing artifact — the realistic fixed-fractional book above is the honest read.</small></div>

      {models.length > 0 && (<>
        <h3 className="fsb-h">By model</h3>
        <div style={{ overflowX: 'auto' }}>
          <table className="ma-tbl fsb-tbl">
            <thead><tr><th className="l">Model</th><th>Trades</th><th>Win rate</th>
              <th>Avg (zero-cost)</th><th>Avg (net)</th><th>Median net</th><th>Profit factor</th></tr></thead>
            <tbody>{models.map(([m, v]: any) => <PerfRow key={m} label={m} p={v} />)}</tbody>
          </table>
        </div>
      </>)}

      <div className="fsb-notes">
        <div className="fsb-note warn"><b>Reconstructable factors:</b> {(d.present_factors || []).join(', ')}.</div>
        <div className="fsb-note warn"><b>Missing (not retained historically):</b> {(d.missing_factors || []).join(', ')}.</div>
        <div className="fsb-note warn"><b>Publication:</b> {d.publication?.classification}. {d.publication?.reason}</div>
        <div className="fsb-note"><b>Metrics:</b> {d.metrics_note}</div>
      </div>
    </div>
  );
}
