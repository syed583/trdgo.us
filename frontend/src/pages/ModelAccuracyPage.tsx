import { useState } from 'react';
import { BarChart3, Loader2 } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';

// Earnings Model Accuracy Scoreboard (read-only). Grades each model's recorded
// BUY/SELL/NEUTRAL calls against the real post-earnings move. Every accuracy
// figure carries its sample size; unreached events stay UNVERIFIED.

const accClass = (a: number | null) =>
  a == null ? 'na' : a >= 55 ? 'good' : a >= 45 ? 'mid' : 'bad';
const fmtAcc = (a: number | null) => (a == null ? '—' : `${a}%`);
const signed = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`;

export default function ModelAccuracyPage() {
  const sb = useApi<any>((s) => api2.modelAccuracyScoreboard(s), [], { refreshMs: 300_000 });
  const [model, setModel] = useState('');
  const hist = useApi<any>((s) => api2.modelAccuracyHistory(model, '', 400, s), [model],
    { refreshMs: 300_000 });

  const d = sb.data;
  const models: any[] = d?.models || [];
  const totals = d?.totals || {};
  const consensus = d?.consensus || {};

  return (
    <div className="page ma-wrap">
      <div className="ts-head">
        <h1><BarChart3 size={20} /> Earnings Model Accuracy</h1>
        <p>How often each model's pre-earnings call matched the real post-earnings
          move. Read-only — no model, weight, or live signal is changed.</p>
      </div>

      <div className="ma-note">
        {d?.note || 'Accuracy is measured only on earnings that have already reported; '
          + 'each figure shows its sample size (n). Models added recently have little '
          + 'or no evaluated history yet.'}
      </div>

      {sb.initialLoading ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Loading scoreboard…</div>
      ) : (
        <>
          <div className="ma-cards">
            <div className="ma-card"><div className="k">Models tracked</div>
              <div className="v">{totals.models ?? '—'}</div></div>
            <div className="ma-card"><div className="k">Evaluated events</div>
              <div className="v">{totals.evaluated_events ?? 0}</div></div>
            <div className="ma-card"><div className="k">Best model</div>
              <div className="v" style={{ fontSize: 15 }}>{totals.best_model || '—'}
                {totals.best_accuracy != null && <span className="ma-pos"> · {totals.best_accuracy}%</span>}</div></div>
            <div className="ma-card"><div className="k">Worst model</div>
              <div className="v" style={{ fontSize: 15 }}>{totals.worst_model || '—'}
                {totals.worst_accuracy != null && <span className="ma-neg"> · {totals.worst_accuracy}%</span>}</div></div>
          </div>

          <div className="ma-h">Model ranking — directional accuracy (BUY/SELL only)</div>
          <p className="ma-n" style={{ margin: '0 0 8px' }}>
            Headline accuracy counts only BUY/SELL calls against the real move. NEUTRAL (no-trade)
            is tracked separately. Records split into Verified · Pending · Unverifiable — missing
            history is never shown as 0%.
          </p>
          <table className="ma-tbl">
            <thead>
              <tr>
                <th className="l">Model</th><th>Dir. acc</th><th>BUY acc</th><th>SELL acc</th>
                <th>NEUTRAL</th><th>Avg return</th>
                <th>Verified</th><th>Pending</th><th>Unverif.</th>
                <th>30d</th><th>90d</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.model}>
                  <td className="l ma-mdl">{m.label}</td>
                  <td><span className={`ma-acc ${accClass(m.directional_accuracy)}`}>{fmtAcc(m.directional_accuracy)}</span>
                    <span className="ma-n"> n{m.directional_n || 0}</span></td>
                  <td>{fmtAcc(m.buy_accuracy)} <span className="ma-n">({m.buy_n})</span></td>
                  <td>{fmtAcc(m.sell_accuracy)} <span className="ma-n">({m.sell_n})</span></td>
                  <td className="ma-n" title="NEUTRAL = no-trade call; flat-band match rate">
                    {fmtAcc(m.neutral_flat_accuracy)} ({m.neutral_n})</td>
                  <td className={m.avg_return_pct > 0 ? 'ma-pos' : m.avg_return_pct < 0 ? 'ma-neg' : ''}>{signed(m.avg_return_pct)}</td>
                  <td className="ma-pos">{m.verified || 0}</td>
                  <td className="ma-n">{m.pending || 0}</td>
                  <td className="ma-n">{m.unverifiable || 0}</td>
                  <td>{fmtAcc(m.acc_30d)} <span className="ma-n">({m.n_30d})</span></td>
                  <td>{fmtAcc(m.acc_90d)} <span className="ma-n">({m.n_90d})</span></td>
                </tr>
              ))}
              {!models.length && <tr><td className="l" colSpan={11}>No predictions recorded yet.</td></tr>}
            </tbody>
          </table>

          <div className="ma-h">Consensus accuracy</div>
          <p className="ma-n" style={{ margin: '0 0 8px' }}>
            When at least N of the models agree on a direction, how often the majority call was right.
          </p>
          <table className="ma-tbl" style={{ maxWidth: 420 }}>
            <thead><tr><th className="l">Agreement</th><th>Signals</th><th>Accuracy</th></tr></thead>
            <tbody>
              {['6', '7', '8', '9', '10'].map((k) => (
                <tr key={k}>
                  <td className="l">≥ {k} models agree</td>
                  <td>{consensus[k]?.n ?? 0}</td>
                  <td><span className={`ma-acc ${accClass(consensus[k]?.accuracy ?? null)}`}>
                    {fmtAcc(consensus[k]?.accuracy ?? null)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="ma-h">Prediction verification</div>
          <div className="ma-toolbar">
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">All models</option>
              {models.map((m) => <option key={m.model} value={m.model}>{m.label}</option>)}
            </select>
            <span className="ma-n">{hist.data?.count ?? 0} graded/pending rows</span>
          </div>
          <table className="ma-tbl">
            <thead>
              <tr>
                <th className="l">Model</th><th className="l">Symbol</th><th>Earnings</th>
                <th>Call</th><th>Score</th><th>Actual move</th><th>Actual</th><th>Result</th>
              </tr>
            </thead>
            <tbody>
              {(hist.data?.rows || []).slice(0, 300).map((r: any, i: number) => (
                <tr key={i}>
                  <td className="l">{r.model_label}</td>
                  <td className="l ma-mdl">{r.symbol}</td>
                  <td>{r.earnings_date}</td>
                  <td><span className={`ma-sig ${r.signal}`}>{r.signal}</span></td>
                  <td>{r.score ?? '—'}</td>
                  <td className={r.actual_move_pct > 0 ? 'ma-pos' : r.actual_move_pct < 0 ? 'ma-neg' : ''}>
                    {r.actual_move_pct == null ? '—' : signed(r.actual_move_pct)}</td>
                  <td>{r.actual_direction || '—'}</td>
                  <td><span className={`ma-res ${r.result}`}>{r.result}</span></td>
                </tr>
              ))}
              {!(hist.data?.rows || []).length && <tr><td className="l" colSpan={8}>No rows.</td></tr>}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
