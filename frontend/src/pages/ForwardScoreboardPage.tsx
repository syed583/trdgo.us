import { useState } from 'react';
import { Loader2, ShieldCheck } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './forward-scoreboard.css';

/**
 * Genuine Forward Scoreboard (Track A) — after-cost performance of the REAL
 * recorded pre-earnings predictions, executions simulated on historical daily
 * bars. Prediction-level stats (model×event×timing) are shown separately from an
 * event-level, daily-marked, equal-weight PORTFOLIO drawdown. Everything is
 * reported both zero-cost and after-cost. Recorded / simulated / live are kept
 * distinct; zero live trades.
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
      <td className={c.win_rate != null && c.win_rate >= 50 ? 'pos' : 'neg'}>
        {c.win_rate == null ? '—' : `${c.win_rate}%`}</td>
      <td className={tone(z.avg_pct)}>{sp(z.avg_pct)}</td>
      <td className={tone(c.avg_pct)}>{sp(c.avg_pct)}</td>
      <td className={tone(c.median_pct)}>{sp(c.median_pct)}</td>
      <td className={c.profit_factor != null && c.profit_factor !== 'inf' && c.profit_factor >= 1 ? 'pos' : 'neg'}>
        {c.profit_factor == null ? '—' : c.profit_factor}</td>
    </tr>
  );
}

function AttrTable({ title, rows }: { title: string; rows: any[] }) {
  if (!rows?.length) return null;
  return (
    <div className="fsb-attr">
      <div className="fsb-attr-h">{title}</div>
      <table className="ma-tbl fsb-tbl">
        <thead><tr><th className="l">Group</th><th>Trades</th><th>Σ net</th><th>Avg net</th></tr></thead>
        <tbody>{rows.map((a) => (
          <tr key={a.key}><td className="l">{a.key}</td><td>{a.n}</td>
            <td className={tone(a.total_net_pct)}>{sp(a.total_net_pct)}</td>
            <td className={tone(a.avg_net_pct)}>{sp(a.avg_net_pct)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export default function ForwardScoreboardPage() {
  const [model, setModel] = useState('');
  const q = useApi<any>((s) => api2.forwardScoreboard(model || undefined, s), [model], { refreshMs: 8_000 });
  const d = q.data;

  if (q.initialLoading || d?.status === 'COMPUTING') return (
    <div className="page"><div className="es-empty"><Loader2 size={18} className="spin" /> Pricing recorded predictions on historical bars… (first load can take ~30s)</div></div>
  );
  if (!d || d.status !== 'OK') return (
    <div className="page"><div className="ma-note">{d?.detail || 'Scoreboard unavailable.'}</div></div>
  );

  const c = d.cost_model || {}, cn = d.counts || {}, t = d.by_entry_timing || {};
  const pf = d.portfolio || {}, attr = d.attribution || {};
  const modelKeys = Object.keys(d.by_model || {});
  const trades: any[] = d.trades || [];

  const Port = ({ label, g }: { label: string; g: any }) => !g ? null : (
    <div className="fsb-stat">
      <span>{label} ({g.net?.positions} events, {g.net?.trading_days}d)</span>
      <b className={tone(g.net?.total_return_pct)}>{sp(g.net?.total_return_pct)} net</b>
      <small>max DD <span className="neg">{sp(g.net?.max_drawdown_pct)}</span> · zero-cost {sp(g.zero_cost?.total_return_pct)}</small>
    </div>
  );

  return (
    <div className="page fsb-wrap">
      <div className="ts-head">
        <h1><ShieldCheck size={20} /> Genuine Forward Scoreboard</h1>
        <p>After-cost performance of the <b>real recorded</b> pre-earnings predictions.
          Executions are <b>simulated</b> on historical daily bars — no live trades.</p>
      </div>

      <div className="fsb-prov">
        <span className="fsb-tag rec">RECORDED predictions</span>
        <span className="fsb-tag sim">SIMULATED executions</span>
        <span className="fsb-tag live">LIVE trades: {d.provenance?.live_trades ?? 0}</span>
        <label className="fsb-mfilter">Model
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">All models</option>
            {modelKeys.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <span className="fsb-asof">as of {d.as_of}</span>
      </div>

      <div className="fsb-stats">
        <div className="fsb-stat"><span>Eligible predictions</span><b>{cn.eligible_predictions}</b></div>
        <div className="fsb-stat"><span>Priced trades</span><b>{cn.priced_trades}</b><small>valid prices {cn.valid_prices}/{cn.priced_trades}</small></div>
        <div className="fsb-stat"><span>Distinct events</span><b>{cn.distinct_events}</b><small>{cn.trades_per_event}× predictions/event</small></div>
        <div className="fsb-stat"><span>Not yet reported</span><b>{d.excluded?.no_exit_reported ?? 0}</b></div>
        <div className="fsb-stat"><span>Look-ahead / pub-violation</span><b>{(d.excluded?.lookahead ?? 0) + (d.excluded?.pub_violation ?? 0)}</b><small>both should be 0</small></div>
      </div>

      <h3 className="fsb-h">Prediction-level performance <small className="fsb-sub">(per model×event×timing — correlated, not a portfolio)</small></h3>
      <div style={{ overflowX: 'auto' }}>
        <table className="ma-tbl fsb-tbl">
          <thead><tr>
            <th className="l">Group</th><th>Trades</th><th>Win rate</th>
            <th>Avg (zero-cost)</th><th>Avg (net)</th><th>Median net</th><th>Profit factor</th>
          </tr></thead>
          <tbody>
            <PerfRow label="Overall" p={d.overall} />
            <PerfRow label="BUY" p={d.by_side?.BUY} />
            <PerfRow label="SELL" p={d.by_side?.SELL} />
            <PerfRow label="T-1 entry" p={t['T-1']} />
            <PerfRow label="T-2 entry" p={t['T-2']} />
            <PerfRow label="T-3 entry" p={t['T-3']} />
            <PerfRow label="T-4+ entry" p={t['T-4+']} />
          </tbody>
        </table>
      </div>

      <h3 className="fsb-h">Portfolio drawdown <small className="fsb-sub">(event-level consensus · daily-marked · equal-weight — the correct sizing)</small></h3>
      <div className="fsb-stats">
        <Port label="All events" g={pf.all_events_consensus} />
        <Port label="T-1 events" g={pf.t1_events_consensus} />
      </div>

      <h3 className="fsb-h">Loss attribution</h3>
      <div className="fsb-attr-grid">
        <AttrTable title="By side" rows={attr.by_side} />
        <AttrTable title="By entry timing" rows={attr.by_timing} />
        <AttrTable title="By sector (worst first)" rows={attr.by_sector} />
        <AttrTable title="By model (worst first)" rows={attr.by_model} />
      </div>

      <h3 className="fsb-h">Trade detail <small className="fsb-sub">({trades.length} trades, worst net first)</small></h3>
      <div style={{ overflowX: 'auto', maxHeight: 420, overflowY: 'auto' }}>
        <table className="ma-tbl fsb-tbl">
          <thead><tr>
            <th className="l">Model</th><th className="l">Symbol</th><th className="l">Side</th>
            <th className="l">Sector</th><th className="l">Earnings</th><th>T-</th><th>Entry</th>
            <th>Exit</th><th>Gross</th><th>Cost</th><th>Net</th>
          </tr></thead>
          <tbody>
            {trades.slice(0, 200).map((x, i) => (
              <tr key={i}>
                <td className="l">{x.model}</td><td className="l"><b>{x.symbol}</b></td>
                <td className="l"><span className={x.side === 'BUY' ? 'pos' : 'neg'}>{x.side}</span></td>
                <td className="l fsb-mut">{x.sector}</td>
                <td className="l fsb-mut">{x.earnings_date}</td>
                <td>{x.t_minus}</td><td className="fsb-mut">{x.entry_price}</td><td className="fsb-mut">{x.exit_price}</td>
                <td className={tone(x.gross_pct)}>{sp(x.gross_pct)}</td>
                <td className="neg">{sp(-x.cost_pct)}</td>
                <td className={tone(x.net_pct)}><b>{sp(x.net_pct)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="fsb-notes">
        <div className="fsb-note"><b>Cost model (assumptions, not fills):</b> half-spread {c.half_spread_bps}bps +
          slippage {c.slippage_bps}bps each side · commission ${c.commission_per_share}/share each side ·
          short-borrow {c.short_borrow_annual_pct}%/yr on SELLs for the holding period.</div>
        <div className="fsb-note"><b>Metrics:</b> {d.metrics_note}</div>
        <div className="fsb-note warn"><b>Missing extended-hours data:</b> {d.missing_data?.extended_hours_exit}</div>
        <div className="fsb-note warn"><b>Missing factor history:</b> {d.missing_data?.factor_history}</div>
        <div className="fsb-note warn"><b>Parameter attribution:</b> {d.missing_data?.parameter_attribution}</div>
        <div className="fsb-note">{d.note}</div>
      </div>
    </div>
  );
}
