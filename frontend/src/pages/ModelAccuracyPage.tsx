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
const fmtCi = (c: number[] | null | undefined) => (c ? `${c[0]}–${c[1]}%` : '');
// One signal's graded count, hit rate and 95% interval, always with its sample size.
const sigCell = (s: any) => (!s || !s.graded ? <span className="ma-n">n0</span> : (
  <>{fmtAcc(s.accuracy)} <span className="ma-n">({s.correct}/{s.graded}) {fmtCi(s.ci95)}</span></>
));

export default function ModelAccuracyPage() {
  const sb = useApi<any>((s) => api2.modelAccuracyScoreboard(s), [], { refreshMs: 300_000 });
  const [model, setModel] = useState('');
  const hist = useApi<any>((s) => api2.modelAccuracyHistory(model, '', 400, s), [model],
    { refreshMs: 300_000 });
  const bt = useApi<any>((s) => api2.earningsBacktestScoreboard(s), [], { refreshMs: 600_000 });

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
          + 'each figure shows its sample size (n).'}
      </div>
      {d?.provisional && (
        <div className="ma-note" style={{ borderColor: 'rgba(180,83,9,.4)', background: 'rgba(180,83,9,.07)' }}>
          <b style={{ color: '#b45309' }}>Provisional.</b>
          <ul style={{ margin: '4px 0 0 18px' }}>
            {(d.provisional_reasons || []).map((r: string, i: number) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}

      {sb.initialLoading ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Loading scoreboard…</div>
      ) : (
        <>
          <div className="ma-cards">
            <div className="ma-card"><div className="k">Models tracked</div>
              <div className="v">{totals.models ?? '—'}</div></div>
            <div className="ma-card"><div className="k">Eligible events</div>
              <div className="v">{totals.eligible_events ?? 0}</div></div>
            <div className="ma-card"><div className="k">Graded events</div>
              <div className="v">{totals.evaluated_events ?? 0}</div></div>
            <div className="ma-card"><div className="k">Outcome missing</div>
              <div className="v">{totals.missing_outcome ?? 0}</div></div>
            <div className="ma-card"><div className="k">Excluded events</div>
              <div className="v">{totals.excluded_events ?? 0}</div></div>
          </div>

          <div className="ma-h">Verified accuracy by signal (one prediction per stock, event and model)</div>
          <p className="ma-n" style={{ margin: '0 0 8px' }}>
            Only snapshots captured before the estimated release are used; retrospective, post-release and
            ambiguous snapshots are excluded. BUY and SELL are scored against the real reaction move
            (flat band ±{d?.method?.flat_band_pct ?? 0.5}%). NEUTRAL is a no-trade call: only its count is shown.
            Compare each rate with the market baseline: a model that always said BUY would have been right as
            often as the &quot;up&quot; share. No ranking is implied at these sample sizes.
          </p>
          <table className="ma-tbl">
            <thead>
              <tr>
                <th className="l">Model</th><th>BUY</th><th>SELL</th><th>NEUTRAL</th><th>BUY+SELL</th>
                <th title="Share of graded events where the stock rose, fell or stayed flat">Market baseline</th>
                <th>Avg return</th><th>Graded</th><th>Pending</th><th>Outcome missing</th><th>Excluded</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.model}>
                  <td className="l ma-mdl">{m.label}</td>
                  <td>{sigCell(m.buy)}</td>
                  <td>{sigCell(m.sell)}</td>
                  <td className="ma-n" title={m.neutral?.note}>{m.neutral?.graded ?? 0} calls</td>
                  <td><span className={`ma-acc ${accClass(m.directional?.accuracy ?? null)}`}>{fmtAcc(m.directional?.accuracy ?? null)}</span>
                    <span className="ma-n"> n{m.directional?.graded || 0} {fmtCi(m.directional?.ci95)}</span></td>
                  <td className="ma-n">{m.baseline?.graded
                    ? `up ${m.baseline.up_pct}% · down ${m.baseline.down_pct}% · flat ${m.baseline.flat_pct}% (n${m.baseline.graded})` : '—'}</td>
                  <td className={m.avg_return_pct > 0 ? 'ma-pos' : m.avg_return_pct < 0 ? 'ma-neg' : ''}>{signed(m.avg_return_pct)}</td>
                  <td className="ma-pos">{m.events_graded || 0}</td>
                  <td className="ma-n">{m.events_pending || 0}</td>
                  <td className="ma-n" title="The reaction session has closed but the outcome is not stored yet (retried automatically)">{m.events_missing_outcome || 0}</td>
                  <td className="ma-n" title={`post-release only ${m.excluded?.post_release_only ?? 0} · ambiguous only ${m.excluded?.ambiguous_only ?? 0} · retrospective only ${m.excluded?.retrospective_only ?? 0}`}>{m.excluded_events || 0}</td>
                </tr>
              ))}
              {!models.length && <tr><td className="l" colSpan={11}>No predictions recorded yet.</td></tr>}
            </tbody>
          </table>

          <div className="ma-h">Consensus accuracy (verified history)</div>
          <p className="ma-n" style={{ margin: '0 0 8px' }}>
            When at least N of the 8 models made the SAME directional (BUY/SELL) call on one
            earnings event, how often that majority call was right. NEUTRAL is never counted as
            agreement. Uses the one eligible pre-release call per model; events without a graded outcome are pending.
          </p>
          {!d?.consensus_has_history && (
            <div className="ma-note" style={{ maxWidth: 620 }}>
              <b>No verified consensus history yet.</b> Verified accuracy needs the agreeing models
              to have recorded calls on an earnings event that has already reported. The signals
              below are <b>current</b> (upcoming earnings), so their accuracy is pending — it fills
              in automatically as those earnings report.
            </div>
          )}
          <table className="ma-tbl" style={{ maxWidth: 680 }}>
            <thead><tr><th className="l">Agreement</th><th>Current signals</th><th>Verified</th>
              <th>Pending</th><th>Correct</th><th>Incorrect</th><th>Verified accuracy</th></tr></thead>
            <tbody>
              {['5', '6', '7', '8'].map((k) => {
                const c = consensus[k] || {};
                return (
                  <tr key={k}>
                    <td className="l">≥ {k} models agree</td>
                    <td>{c.signals ?? 0}</td>
                    <td>{c.verified ?? 0}</td>
                    <td className="ma-n">{c.pending ?? 0}</td>
                    <td className="ma-pos">{c.correct ?? 0}</td>
                    <td className="ma-neg">{c.incorrect ?? 0}</td>
                    <td>{(c.verified ?? 0) === 0
                      ? <span className="ma-res PENDING">No verified history</span>
                      : <span className={`ma-acc ${accClass(c.accuracy ?? null)}`}>{fmtAcc(c.accuracy ?? null)}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="ma-h">Historical backtest</div>
          {bt.data?.full_backtest_available === false && (
            <div className="ma-note" style={{ maxWidth: 680, borderColor: 'rgba(180,83,9,.4)', background: 'rgba(180,83,9,.07)' }}>
              <b style={{ color: '#b45309' }}>Full historical backtest unavailable.</b> {bt.data.unavailable_reason}
              {' '}The original models therefore <b>cannot be faithfully reproduced</b>. The table below is a
              <b> partial-factor simulation only</b> — it is NOT the models' historical accuracy and must not be
              read as such.
            </div>
          )}
          <p className="ma-n" style={{ margin: '8px 0' }}>
            The unmodified model scorers are re-run through a separate point-in-time adapter.
            {bt.data?.events_tested ? ` ${bt.data.events_tested} past earnings events tested.` : ''}
            {' '}Each row shows input coverage; lower coverage = less of the real model reproduced.
          </p>
          {!bt.data || bt.data.status !== 'OK' || !(bt.data.models || []).length ? (
            <div className="ma-note" style={{ maxWidth: 560 }}>
              {bt.initialLoading ? 'Loading backtest…' : 'No backtest results yet.'}
            </div>
          ) : (
            <>
              <table className="ma-tbl">
                <thead><tr><th className="l">Model (partial-factor sim.)</th><th>Events</th>
                  <th>BUY acc</th><th>SELL acc</th><th>Dir. acc</th><th>Correct / Incorrect</th>
                  <th>Avg move</th><th>Coverage</th><th>Status</th></tr></thead>
                <tbody>
                  {bt.data.models.map((m: any) => (
                    m.unavailable ? (
                      <tr key={m.model}>
                        <td className="l ma-mdl">{m.label}</td>
                        <td colSpan={7} className="ma-res UNVERIFIABLE" style={{ textAlign: 'left' }}>
                          Full historical backtest unavailable — derived from the equity engine's score/decision (not reconstructable).</td>
                        <td className="ma-res UNVERIFIABLE">N/A</td>
                      </tr>
                    ) : (
                      <tr key={m.model}>
                        <td className="l ma-mdl">{m.label}</td>
                        <td>{m.events_tested}</td>
                        <td>{fmtAcc(m.buy_accuracy)} <span className="ma-n">({m.buy_n})</span></td>
                        <td>{fmtAcc(m.sell_accuracy)} <span className="ma-n">({m.sell_n})</span></td>
                        <td><span className={`ma-acc ${accClass(m.directional_accuracy)}`}>
                          {fmtAcc(m.directional_accuracy)}</span> <span className="ma-n">n{m.directional_n}</span></td>
                        <td><span className="ma-pos">{m.directional_correct}</span> / <span className="ma-neg">{m.directional_incorrect}</span></td>
                        <td className={m.avg_move_pct > 0 ? 'ma-pos' : m.avg_move_pct < 0 ? 'ma-neg' : ''}>{signed(m.avg_move_pct)}</td>
                        <td className="ma-n">{m.coverage_pct != null ? `${m.coverage_pct}%` : '—'}</td>
                        <td className="ma-res UNVERIFIABLE">Partial</td>
                      </tr>
                    )
                  ))}
                </tbody>
              </table>
              <p className="ma-n" style={{ marginTop: 8 }}>
                <b>Reconstructed point-in-time:</b> {(bt.data.present_factors || []).join(', ')}.<br />
                <b>Missing (left absent, never back-filled with today's data):</b> {(bt.data.missing_factors || []).join(', ')}.
              </p>
            </>
          )}

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
