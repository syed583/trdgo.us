import { Loader2, ShieldCheck } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './forward-scoreboard.css';

/**
 * Genuine Forward Scoreboard (Track A) — after-cost performance of the REAL
 * recorded pre-earnings predictions, with executions simulated on historical
 * daily bars. Honest provenance: recorded predictions, simulated executions,
 * zero live trades. Depth = how long capture has run (no backfill).
 */

const pct = (n: number | null | undefined, suffix = '%') =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${Number(n).toFixed(2)}${suffix}`;
const plain = (n: number | null | undefined) => (n == null ? '—' : String(n));

function metricTone(n: number | null | undefined) {
  if (n == null) return '';
  return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
}

function Row({ label, m }: { label: string; m: any }) {
  if (!m) return null;
  return (
    <tr>
      <td className="l"><b>{label}</b></td>
      <td>{plain(m.n)}</td>
      <td className={m.win_rate != null && m.win_rate >= 50 ? 'pos' : 'neg'}>
        {m.win_rate == null ? '—' : `${m.win_rate}%`}</td>
      <td className={metricTone(m.avg_net_pct)}>{pct(m.avg_net_pct)}</td>
      <td className={metricTone(m.median_net_pct)}>{pct(m.median_net_pct)}</td>
      <td className={m.profit_factor != null && m.profit_factor !== 'inf' && m.profit_factor >= 1 ? 'pos' : 'neg'}>
        {m.profit_factor == null ? '—' : m.profit_factor}</td>
      <td className="neg">{pct(m.max_drawdown_pct)}</td>
      <td className={metricTone(m.total_net_pct)}>{pct(m.total_net_pct)}</td>
    </tr>
  );
}

export default function ForwardScoreboardPage() {
  const q = useApi<any>((s) => api2.forwardScoreboard(undefined, s), [], { refreshMs: 8_000 });
  const d = q.data;

  if (q.initialLoading || d?.status === 'COMPUTING') return (
    <div className="page"><div className="es-empty"><Loader2 size={18} className="spin" /> Pricing recorded predictions on historical bars… (first load can take ~30s)</div></div>
  );
  if (!d || d.status !== 'OK') return (
    <div className="page"><div className="ma-note">{d?.detail || 'Scoreboard unavailable.'}</div></div>
  );

  const c = d.cost_model || {};
  const timing = d.by_entry_timing || {};
  const models = Object.entries(d.by_model || {});

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
        <span className="fsb-asof">as of {d.as_of}</span>
      </div>

      <div className="fsb-stats">
        <div className="fsb-stat"><span>Eligible predictions</span><b>{d.eligible_predictions}</b></div>
        <div className="fsb-stat"><span>Priced trades</span><b>{d.priced_trades}</b></div>
        <div className="fsb-stat"><span>Symbols priced</span><b>{d.symbols_priced}</b></div>
        <div className="fsb-stat"><span>Not yet reported</span><b>{d.excluded?.no_exit_reported ?? 0}</b></div>
        <div className="fsb-stat"><span>Look-ahead excluded</span><b>{d.excluded?.lookahead ?? 0}</b></div>
      </div>

      <h3 className="fsb-h">Net-of-cost performance — overall & by entry timing</h3>
      <div style={{ overflowX: 'auto' }}>
        <table className="ma-tbl fsb-tbl">
          <thead><tr>
            <th className="l">Group</th><th>Trades</th><th>Win rate</th><th>Avg net</th>
            <th>Median net</th><th>Profit factor</th><th>Max DD</th><th>Σ net</th>
          </tr></thead>
          <tbody>
            <Row label="Overall" m={d.overall} />
            <Row label="T-1 entry" m={timing['T-1']} />
            <Row label="T-2 entry" m={timing['T-2']} />
            <Row label="T-3 entry" m={timing['T-3']} />
            <Row label="T-4+ entry" m={timing['T-4+']} />
          </tbody>
        </table>
      </div>

      {models.length > 0 && (<>
        <h3 className="fsb-h">By model</h3>
        <div style={{ overflowX: 'auto' }}>
          <table className="ma-tbl fsb-tbl">
            <thead><tr>
              <th className="l">Model</th><th>Trades</th><th>Win rate</th><th>Avg net</th>
              <th>Median net</th><th>Profit factor</th><th>Max DD</th><th>Σ net</th>
            </tr></thead>
            <tbody>
              {models.map(([m, v]: any) => <Row key={m} label={m} m={v} />)}
            </tbody>
          </table>
        </div>
      </>)}

      <div className="fsb-notes">
        <div className="fsb-note"><b>Cost model (assumptions, not fills):</b> half-spread {c.half_spread_bps}bps +
          slippage {c.slippage_bps}bps each side · commission ${c.commission_per_share}/share each side ·
          short-borrow {c.short_borrow_annual_pct}%/yr on SELLs for the holding period.</div>
        <div className="fsb-note"><b>Metrics:</b> {d.metrics_note}</div>
        <div className="fsb-note warn"><b>Missing extended-hours data:</b> {d.missing_data?.extended_hours_exit}</div>
        <div className="fsb-note warn"><b>Missing historical factors:</b> {d.missing_data?.factor_history}</div>
        <div className="fsb-note">{d.note}</div>
      </div>
    </div>
  );
}
