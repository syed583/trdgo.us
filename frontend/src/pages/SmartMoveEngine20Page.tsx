import { Fragment, useState } from 'react';
import { AlertTriangle, Info, RefreshCw } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveEngine20 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './smart-move.css';

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short' });
}
const nice = (s?: string | null) => (s ? s.replace(/_/g, ' ').toLowerCase() : '—');
const LABEL: Record<string, string> = { BUY: 'BUY', SELL: 'SELL', NO_TRADE: 'NO TRADE', NO_VALID_PREDICTION: 'NO VALID PREDICTION', PENDING_REFRESH: 'PENDING REFRESH' };
const CMP_LABEL: Record<string, string> = { CORRECT: 'Correct', INCORRECT: 'Incorrect', NOT_APPLICABLE: 'Not scored', NOT_AVAILABLE: 'Outcome not available yet' };

/** NO TRADE (every gate passed, margin too small) and NO VALID PREDICTION (data missing or insufficient) look and read differently on purpose. */
export function CategoryBadge({ category }: { category: string }) {
  const cls = category === 'NO_VALID_PREDICTION' || category === 'PENDING_REFRESH' ? 'sm-q-badge amber' : `sm-sig ${category.toLowerCase()}`;
  return <span className={cls} data-testid="category">{LABEL[category] || category}</span>;
}

export function missingText(r: { missing_factors?: any[] }): string {
  const m = r.missing_factors || [];
  return m.length ? m.map((x: any) => `${x[0]}: ${nice(x[1])}`).join('; ') : 'none';
}

/** The newest immutable Engine20 prediction snapshot. A historical record of the frozen score40-v1 output at its generation time: this page computes no score, and it never presents a snapshot as a current live signal. */
export default function SmartMoveEngine20Page({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [cat, setCat] = useState('');
  const [open, setOpen] = useState('');
  const snap = useApi<any>((s) => smartMoveEngine20.latest(s), [], { refreshMs: 60_000 });
  const out = useApi<any>((s) => smartMoveEngine20.outcomes(s), [], { refreshMs: 300_000 });
  const reload = () => { snap.refresh(); out.refresh(); };
  const d = snap.data;
  const ok = d?.status === 'OK';
  const rows: any[] = ok ? (d.rows as any[]).filter((r) => !cat || r.category === cat) : [];
  const cmp: Record<string, any> = {};
  for (const o of out.data?.rows || []) cmp[o.symbol + o.event_date] = o;

  return (
    <div className="sm">
      <PageHead title="Smart Move Snapshot (20 stocks)" subtitle="The frozen 40-point engine (score40-v1) output for the 20 approved stocks, kept as an immutable historical snapshot with its generation time and evidence." />
      <div className="sm-q-banner" role="note">
        <Info size={16} />
        <div><b>This is a historical snapshot, not a live signal.</b> It shows what the frozen engine said at the time it was generated. NO TRADE means every gate passed and the margin was too small; NO VALID PREDICTION means data was missing or insufficient and is never a neutral view.</div>
      </div>

      {snap.initialLoading && <div className="sm-none" role="status">Loading snapshot…</div>}
      {snap.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> The snapshot could not be loaded: {snap.error} <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Retry</button></div>}
      {d && !ok && (
        <div className={`sm-q-state ${d.status === 'SNAPSHOT_UNVERIFIED' ? 'bad' : ''}`} role={d.status === 'SNAPSHOT_UNVERIFIED' ? 'alert' : 'status'}>
          <b>{d.status === 'SNAPSHOT_UNVERIFIED' ? 'Snapshot could not be verified' : 'No snapshot yet'}</b>
          <div>{d.detail}</div>
        </div>
      )}

      {ok && (
        <>
          <div className="sm-fresh stale" data-testid="snapshot-label"><AlertTriangle size={14} /> Historical snapshot generated {fmtTime(d.generated_at)} ({d.snapshot_age_h} h ago) · model {d.model_version} · {d.window_open_now ? 'prediction window still open, but this page is not a live feed' : 'prediction window closed'} · audit {d.audit?.verdict}</div>
          <div className="sm-summary" data-testid="counts">
            <div className="sm-card"><div className="sm-card-k">BUY</div><div className="sm-card-v">{d.counts.BUY}</div></div>
            <div className="sm-card"><div className="sm-card-k">SELL</div><div className="sm-card-v">{d.counts.SELL}</div></div>
            <div className="sm-card"><div className="sm-card-k">NO TRADE (genuine)</div><div className="sm-card-v">{d.counts.NO_TRADE}</div></div>
            <div className="sm-card"><div className="sm-card-k">NO VALID PREDICTION</div><div className="sm-card-v">{d.counts.NO_VALID_PREDICTION}</div></div>
          </div>
          <div className="sm-filters">
            <div className="sm-tabs" role="group" aria-label="Category filter">
              {['', 'BUY', 'SELL', 'NO_TRADE', 'NO_VALID_PREDICTION'].map((k) => (
                <button key={k || 'all'} className={`sm-tab ${cat === k ? 'on' : ''}`} aria-pressed={cat === k} onClick={() => setCat(k)}>{k ? LABEL[k] : 'All'}</button>
              ))}
            </div>
            <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Reload</button>
          </div>
          <div className="sm-table-wrap"><table className="sm-table sm-q-table">
            <thead><tr><th>Symbol</th><th>Earnings date</th><th>Session</th><th>Prediction</th><th>Bull</th><th>Bear</th><th>Coverage</th><th>Evidence freshness</th><th>Liquidity</th><th>Missing factors</th><th>Outcome</th></tr></thead>
            <tbody>{rows.map((r) => {
              const k = r.symbol + r.event_date;
              const o = cmp[k];
              return (
                <Fragment key={k}>
                  <tr className="sm-q-row" tabIndex={0} onClick={() => setOpen(open === k ? '' : k)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(open === k ? '' : k); }}>
                    <td><b>{r.symbol}</b><div className="sm-q-muted">{r.company || '—'}</div></td>
                    <td>{r.event_date}</td><td>{r.session === 'BMO' ? 'Before Open' : r.session === 'AMC' ? 'After Close' : r.session}</td>
                    <td><CategoryBadge category={r.category} /></td>
                    <td>{r.bull ?? '—'}</td><td>{r.bear ?? '—'}</td>
                    <td>{r.coverage_points}/{r.coverage_max}</td>
                    <td>{r.evidence_age_h != null ? `${r.evidence_age_h} h old` : 'unknown'}<div className="sm-q-muted">{fmtTime(r.evidence_retrieved?.[0])}</div></td>
                    <td>{r.liquidity}{r.adv20_usd != null && <div className="sm-q-muted">ADV20 ${(r.adv20_usd / 1e6).toFixed(0)}M</div>}</td>
                    <td>{missingText(r)}</td>
                    <td data-testid="outcome">{o ? (o.comparison === 'NOT_AVAILABLE' ? nice(o.outcome_state) : `${CMP_LABEL[o.comparison]}${o.move_pct != null ? ` (${o.move_pct}%)` : ''}`) : '—'}</td>
                  </tr>
                  {open === k && <tr><td colSpan={11}><div data-testid="detail"><div><b>Why:</b> {r.reason}</div><div className="sm-q-muted">{r.category_text}</div><div className="sm-q-muted">{r.snapshot_label} · window closes {fmtTime(r.window_cutoff_utc)}</div></div></td></tr>}
                </Fragment>
              );
            })}</tbody>
          </table></div>
          {out.data?.available && out.data.tally && <div className="sm-q-muted" data-testid="tally">Outcomes verified so far — correct {out.data.tally.CORRECT}, incorrect {out.data.tally.INCORRECT}, not scored {out.data.tally.NOT_APPLICABLE}, not available yet {out.data.tally.NOT_AVAILABLE}. {out.data.note}</div>}
          {out.data && out.data.available === false && <div className="sm-q-muted" data-testid="no-outcomes">{out.data.detail}</div>}
        </>
      )}
    </div>
  );
}
