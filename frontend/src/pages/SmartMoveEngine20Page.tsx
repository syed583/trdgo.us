import { Fragment, useState } from 'react';
import { AlertTriangle, Info, RefreshCw } from 'lucide-react';
import type { PageContext } from '../App';
import { earningsMonitor, smartMoveEngine20 } from '../api/client';
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
export function SnapshotView() {
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

const LIVE_TONE: Record<string, [string, string]> = {
  LIVE: ['green', 'The prediction window is open and the last successful scan is recent.'],
  STALE: ['amber', 'The window is open but the last successful scan is older than its limit: this call may be out of date.'],
  HISTORICAL: ['gray', 'The prediction window has closed: a record of what was predicted, not a current signal.'],
  'WINDOW MISSED': ['amber', 'The window closed before any prediction was produced. Nothing is back-filled.'],
  UNAVAILABLE: ['amber', 'The prediction ledger failed its integrity check, so no call is shown.'],
};
const LIVE_CATS = ['', 'BUY', 'SELL', 'NO_TRADE', 'NO_VALID_PREDICTION', 'PENDING_REFRESH', 'MISSED'];
const LIVE_CAT_LABEL: Record<string, string> = { '': 'All', BUY: 'BUY', SELL: 'SELL', NO_TRADE: 'NO TRADE', NO_VALID_PREDICTION: 'NO VALID PREDICTION', PENDING_REFRESH: 'PENDING (not a prediction)', MISSED: 'Missed windows' };

/** The LIVE scanner: the collector's own append-only prediction ledger and status, read from files by the server. This page computes no score and never relabels a missed window as a prediction. */
export function LiveView() {
  const live = useApi<any>((s) => earningsMonitor.smartLive(s), [], { refreshMs: 60_000 });
  const [cat, setCat] = useState('');
  const [open, setOpen] = useState('');
  const d = live.data;
  const all: any[] = d?.rows || [];
  const missed = all.filter((r) => r.label === 'WINDOW MISSED');
  const calls = all.filter((r) => r.label !== 'WINDOW MISSED');
  const n = (code: string) => calls.filter((r) => r.signal_code === code).length;
  const shown = cat === 'MISSED' ? missed : calls.filter((r) => !cat || r.signal_code === cat);
  const s = d?.scan || {};
  const q = d?.requests || {};
  const e = d?.events || {};
  return (
    <div className="sm" data-testid="sm-live-view">
      <PageHead title="Smart Money Move â€” LIVE scanner" subtitle="Live predictions of the frozen 40-point engine (score40-v1) from this server's own collector. Each call is recorded in an append-only ledger before its prediction window closes." />
      {live.initialLoading && <div className="sm-none" role="status">Loading the live scannerâ€¦</div>}
      {live.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> The live scanner could not be loaded: {live.error} <button className="ghost-btn" onClick={() => live.refresh()}><RefreshCw size={14} /> Retry</button></div>}
      {d && !d.configured && (
        <div className="sm-q-state" role="status" data-testid="sm-live-off"><b>The live scanner is not installed here</b><div>{d.note}</div></div>
      )}
      {d?.configured && (
        <>
          {d.ledger && !d.ledger.ok && <div className="sm-q-state bad" role="alert" data-testid="sm-live-ledger"><b>Prediction ledger integrity check FAILED</b><div>{d.ledger.problem}. No call is shown.</div></div>}
          {d.state === 'OK'
            ? <div className="sm-q-banner" role="note" data-testid="sm-live-state"><Info size={16} /><div><b>Live.</b> {d.state_text} NO TRADE means every gate passed and the margin was too small; NO VALID PREDICTION means data was missing or insufficient and is never a neutral view; PENDING means the evidence has not been collected yet and is not a prediction.</div></div>
            : <div className="sm-fresh stale" role="alert" data-testid="sm-live-state"><AlertTriangle size={14} /> {String(d.state).replace(/_/g, ' ')}: {d.state_text}</div>}
          <div className="sm-q-muted" data-testid="sm-live-status">
            Last successful scan: <b>{s.last_successful_scan_et ?? 'none yet'}</b>{s.minutes_since_success != null && <> ({s.minutes_since_success} min ago; stale after {s.stale_after_min} min)</>}
            {' Â· '}Mode: {s.mode ?? 'â€”'}{d.kill_switch && <b> Â· KILL SWITCH ENGAGED</b>}
            {' Â· '}Now: {d.now_et}
          </div>
          <div className="sm-q-muted" data-testid="sm-live-requests">
            Unusual Whales requests by the collector today: <b>{q.used_today ?? 'unknown'}</b> of {q.daily_limit ?? 'unknown'} Â· remaining <b>{q.remaining_today ?? 'unknown'}</b> Â· per run â‰¤ {q.run_limit ?? 'unknown'}
            {d.calendar?.through && <> Â· calendar through {d.calendar.through} (age {d.calendar.age_h} h)</>}
            {e.open_scanned != null && <> Â· events in an open window scanned: <b>{e.open_scanned}</b></>}
          </div>
          <div className="sm-summary" data-testid="live-counts">
            <div className="sm-card"><div className="sm-card-k">BUY</div><div className="sm-card-v">{n('BUY')}</div></div>
            <div className="sm-card"><div className="sm-card-k">SELL</div><div className="sm-card-v">{n('SELL')}</div></div>
            <div className="sm-card"><div className="sm-card-k">NO TRADE (genuine)</div><div className="sm-card-v">{n('NO_TRADE')}</div></div>
            <div className="sm-card"><div className="sm-card-k">NO VALID PREDICTION</div><div className="sm-card-v">{n('NO_VALID_PREDICTION')}</div></div>
            <div className="sm-card"><div className="sm-card-k">PENDING (not a prediction)</div><div className="sm-card-v">{n('PENDING_REFRESH')}</div></div>
          </div>
          <div className="sm-filters">
            <div className="sm-tabs" role="group" aria-label="Category filter">
              {LIVE_CATS.map((k) => (
                <button key={k || 'all'} className={`sm-tab ${cat === k ? 'on' : ''}`} aria-pressed={cat === k} onClick={() => setCat(k)}>{LIVE_CAT_LABEL[k]}{k === 'MISSED' ? ` (${missed.length})` : ''}</button>
              ))}
            </div>
            <button className="ghost-btn" onClick={() => live.refresh()}><RefreshCw size={14} /> Reload</button>
          </div>
          {!shown.length && <div className="sm-none" data-testid="sm-live-none">{all.length ? 'No event in this category.' : 'No live prediction has been recorded yet.'}</div>}
          {shown.length > 0 && (
            <div className="sm-table-wrap"><table className="sm-table sm-q-table" data-testid="sm-live-table">
              <thead><tr><th>Symbol</th><th>Earnings date</th><th>Session</th><th>Prediction</th><th>Bull</th><th>Bear</th><th>Coverage</th><th>Evidence freshness</th><th>Liquidity</th><th>Missing factors</th><th>Window closes (ET)</th></tr></thead>
              <tbody>{shown.map((r) => {
                const k = r.symbol + r.earnings_date;
                const tone = LIVE_TONE[r.label] || ['gray', ''];
                return (
                  <Fragment key={k}>
                    <tr className="sm-q-row" data-testid="sm-live-row" tabIndex={0} onClick={() => setOpen(open === k ? '' : k)} onKeyDown={(ev) => { if (ev.key === 'Enter') setOpen(open === k ? '' : k); }}>
                      <td><b>{r.symbol}</b><div className="sm-q-muted">{r.company || 'â€”'}</div></td>
                      <td>{r.earnings_date}</td><td>{r.session === 'BMO' ? 'Before Open' : r.session === 'AMC' ? 'After Close' : r.session}</td>
                      <td>{r.signal_code ? <CategoryBadge category={r.signal_code} /> : <span className="sm-q-badge amber">WINDOW MISSED</span>} <span className={`sm-q-badge ${tone[0]}`} title={tone[1]} data-testid="sm-live-label">{r.label}</span></td>
                      <td>{r.bull ?? 'â€”'}</td><td>{r.bear ?? 'â€”'}</td>
                      <td>{r.coverage ?? 'â€”'}</td>
                      <td>{r.evidence_age_h != null ? <span className={r.evidence_warning ? 'sm-fresh stale' : undefined}>{r.evidence_age_h} h old{r.evidence_warning ? ' â€” old evidence' : ''}</span> : 'none'}<div className="sm-q-muted">{r.evidence_latest_et ?? ''}</div></td>
                      <td>{r.liquidity ?? 'â€”'}{r.adv20_usd != null && <div className="sm-q-muted">ADV20 ${(r.adv20_usd / 1e6).toFixed(0)}M</div>}</td>
                      <td>{r.missing?.length ? r.missing.join('; ') : 'none'}</td>
                      <td>{r.window_cutoff_et ?? 'â€”'}{r.hours_to_cutoff != null && <div className="sm-q-muted">{r.hours_to_cutoff} h left</div>}</td>
                    </tr>
                    {open === k && <tr><td colSpan={11}><div data-testid="live-detail"><div><b>Why:</b> {r.reason ?? 'â€”'}</div><div className="sm-q-muted">Generated {r.generated_at_et ?? 'â€”'} Â· mode {r.mode ?? 'â€”'} Â· record {r.record_sha256}</div></div></td></tr>}
                  </Fragment>
                );
              })}</tbody>
            </table></div>
          )}
          <div className="sm-q-muted">{d.note}</div>
        </>
      )}
    </div>
  );
}

/** Smart Money Move: the LIVE scanner (default) and the frozen historical snapshot, as two clearly separate views. A historical snapshot is never shown as a live signal. */
export default function SmartMoveEngine20Page({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [view, setView] = useState<'live' | 'snapshot'>('live');
  return (
    <div>
      <div className="sm" style={{ paddingBottom: 0 }}>
        <div className="sm-tabs" role="tablist" aria-label="Smart Money Move view">
          <button role="tab" aria-selected={view === 'live'} className={`sm-tab ${view === 'live' ? 'on' : ''}`} data-testid="tab-live" onClick={() => setView('live')}>LIVE scanner</button>
          <button role="tab" aria-selected={view === 'snapshot'} className={`sm-tab ${view === 'snapshot' ? 'on' : ''}`} data-testid="tab-snapshot" onClick={() => setView('snapshot')}>Historical snapshot (20 stocks)</button>
        </div>
      </div>
      {view === 'live' ? <LiveView /> : <SnapshotView />}
    </div>
  );
}
