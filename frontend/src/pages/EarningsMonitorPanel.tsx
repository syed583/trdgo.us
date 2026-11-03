import { Fragment, useState } from 'react';
import { AlertTriangle, Info, RefreshCw } from 'lucide-react';
import { earningsMonitor } from '../api/client';
import { useApi } from '../hooks/useApi';
import './smart-move.css';

type Tab = 'upcoming' | 'models' | 'changes' | 'consensus' | 'accuracy';
const TABS: [Tab, string][] = [['upcoming', 'Upcoming Earnings'], ['models', 'All Models'], ['changes', 'Prediction Changes'], ['consensus', 'Model Consensus'], ['accuracy', 'Historical Accuracy']];

export function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short' });
}
export const nice = (s?: string | null) => (s ? s.replace(/_/g, ' ').toLowerCase() : '—');
export function agoText(iso: string | null | undefined, now: string | null | undefined): string {
  if (!iso || !now) return 'never';
  const m = Math.round((new Date(now).getTime() - new Date(iso).getTime()) / 60000);
  if (Number.isNaN(m)) return 'unknown';
  if (m < 1) return 'just now';
  return m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}
export function scanBadge(s: any): { text: string; tone: string } {
  if (!s || s.status === 'NOT_STARTED') return { text: 'Automatic scan: NOT STARTED on this server', tone: 'gray' };
  if (s.scheduling === 'OFF') return { text: 'Automatic scan: OFF — waiting for owner approval', tone: 'amber' };
  return { text: s.scheduling === 'REFRESH' ? 'Automatic scan: ON (cache reads + budgeted provider refresh)' : 'Automatic scan: ON (cache reads only, no provider requests)', tone: 'green' };
}
const LEVEL_TONE: Record<string, string> = { STRONG: 'green', WEAK: 'gray', CONFLICT: 'red', INSUFFICIENT: 'amber', NONE: 'gray' };
const VER_TONE: Record<string, string> = { CONFIRMED: 'green', SINGLE_VENDOR: 'amber', CONFLICT: 'red', UNVERIFIED: 'gray' };
const LABEL_TEXT: Record<string, string> = { NO_TRADE: 'NO TRADE', NO_VALID_PREDICTION: 'NO VALID PREDICTION', LOW_DATA: 'LOW DATA', PENDING: 'PENDING', STALE: 'STALE', NEUTRAL: 'NEUTRAL' };
const LABEL_HINT: Record<string, string> = {
  NO_TRADE: 'A real call: every gate passed and the model says do nothing.',
  NO_VALID_PREDICTION: 'No usable call: data was missing or insufficient. Not a neutral view.',
  LOW_DATA: 'The model had too little data to call.', PENDING: 'The model has not scored this event yet.', STALE: 'The last call is older than the freshness limit.',
};

/** One model's cell. A label other than BUY / SELL / NEUTRAL / NO TRADE is never drawn as a call. */
export function SignalCell({ c }: { c: any }) {
  if (!c) return <span className="sm-q-muted">—</span>;
  const label: string = c.label || (c.status === 'VALID' ? c.signal : 'PENDING');
  const isCall = ['BUY', 'SELL', 'NEUTRAL', 'NO_TRADE'].includes(label);
  const cls = label === 'BUY' || label === 'SELL' ? `sm-sig ${label.toLowerCase()}` : label === 'NO_TRADE' || label === 'NEUTRAL' ? 'sm-q-badge gray' : label === 'PENDING' ? 'sm-q-badge gray' : 'sm-q-badge amber';
  return (
    <div data-testid="model-cell" title={LABEL_HINT[label] || (c.prediction_recorded_at ? `recorded ${fmtTime(c.prediction_recorded_at)}` : undefined)}>
      <span className={cls} data-testid="cell-label" data-call={isCall ? 'yes' : 'no'}>{LABEL_TEXT[label] || label}</span>
      {c.status === 'VALID' && <div className="sm-q-muted">{c.score != null ? `score ${c.score} · ` : ''}{c.coverage_fraction != null ? `cov ${Math.round(c.coverage_fraction * 100)}%` : 'cov —'} · {c.age_h != null ? `${c.age_h} h old` : ''}</div>}
      {c.status === 'VALID' && c.source && <div className="sm-q-muted">{nice(c.source)}</div>}
    </div>
  );
}

function History({ symbol }: { symbol: string }) {
  const h = useApi<any>((s) => earningsMonitor.history(symbol, s), [symbol], { refreshMs: 0 });
  if (h.initialLoading) return <div className="sm-q-muted">Loading history…</div>;
  const rows: any[] = h.data?.rows || [];
  if (!rows.length) return <div className="sm-q-muted">No stored prediction versions yet.</div>;
  return (
    <table className="sm-mini" data-testid="history"><thead><tr><th>Recorded</th><th>Model</th><th>Call</th><th>Score</th><th>Coverage</th><th>Built (as of)</th><th>Source</th><th>Version</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.id}><td>{fmtTime(r.recorded_at)}</td><td>{r.model}</td><td>{nice(r.signal)}</td><td>{r.score ?? '—'}</td><td>{r.coverage ?? '—'}</td><td>{fmtTime(r.as_of)}</td><td>{nice(r.source)}</td><td>{r.engine_version || '—'}</td></tr>)}</tbody></table>
  );
}

function Sources() {
  const s = useApi<any>((sig) => earningsMonitor.sources(sig), [], { refreshMs: 0 });
  if (s.initialLoading) return <div className="sm-q-muted">Loading sources…</div>;
  if (s.error) return <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {s.error}</div>;
  const rows: any[] = s.data?.models || [];
  return (
    <div data-testid="sources">
      <table className="sm-mini"><thead><tr><th>Source</th><th>What it is</th><th>External AI service?</th><th>Genuine stored predictions now</th><th>Needs separately authorised execution</th></tr></thead>
        <tbody>{rows.map((m) => <tr key={m.id}><td>{m.label}</td><td>{m.what || m.kind}</td><td>{m.external_ai_service ? 'yes' : 'no'}</td><td>{m.stored_predictions_now ? 'yes' : 'no'}</td><td>{m.needs_authorised_execution}</td></tr>)}</tbody></table>
      <div className="sm-q-muted">{s.data?.note}</div>
    </div>
  );
}

/** The Earnings monitor, embedded in the Earnings workspace. Everything shown is read from the monitor's own store through read-only endpoints: this panel computes no prediction, no score and no change. */
export default function EarningsMonitorPanel({ initialTab = 'upcoming' }: { initialTab?: Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [hist, setHist] = useState(false);
  const [kind, setKind] = useState('');
  const [open, setOpen] = useState('');
  const [showSources, setShowSources] = useState(false);
  const status = useApi<any>((s) => earningsMonitor.status(s), [], { refreshMs: 60_000 });
  const upcoming = useApi<any>((s) => earningsMonitor.upcoming(hist, s), [hist], { refreshMs: 60_000, enabled: tab === 'upcoming' });
  const models = useApi<any>((s) => earningsMonitor.models(s), [], { refreshMs: 60_000, enabled: tab === 'models' });
  const changes = useApi<any>((s) => earningsMonitor.changes(kind, s), [kind], { refreshMs: 60_000, enabled: tab === 'changes' });
  const consensus = useApi<any>((s) => earningsMonitor.consensus(s), [], { refreshMs: 60_000, enabled: tab === 'consensus' });
  const accuracy = useApi<any>((s) => earningsMonitor.accuracy(s), [], { refreshMs: 300_000, enabled: tab === 'accuracy' });
  const comparison = useApi<any>((s) => earningsMonitor.comparison(s), [], { refreshMs: 300_000, enabled: tab === 'accuracy' });
  const reload = () => { status.refresh(); upcoming.refresh(); models.refresh(); changes.refresh(); consensus.refresh(); accuracy.refresh(); comparison.refresh(); };
  const s = status.data;
  const badge = scanBadge(s);
  const cur: any = { upcoming, models, changes, consensus, accuracy }[tab];

  return (
    <div className="sm" data-testid="earnings-monitor">
      <div className="sm-q-banner" role="note">
        <Info size={16} />
        <div><b>Each model's prediction is shown exactly as it made it.</b> PENDING, LOW DATA and NO VALID PREDICTION mean no call (never neutral); NO TRADE is a real call to do nothing. Nothing here places a trade.</div>
      </div>

      <div className="sm-summary" data-testid="scan-status">
        <div className="sm-card"><div className="sm-card-k">Automatic scan</div><div className="sm-card-v"><span className={`sm-q-badge ${badge.tone}`} data-testid="scan-badge">{badge.text}</span></div></div>
        <div className="sm-card"><div className="sm-card-k">Last successful scan</div><div className="sm-card-v" data-testid="last-scan">{s?.last_any_success ? `${fmtTime(s.last_any_success)} (${agoText(s.last_any_success, s.now)})` : 'never'}</div></div>
        <div className="sm-card"><div className="sm-card-k">Auto-refresh</div><div className="sm-card-v">this view every {s?.auto_refresh_seconds || 60} s</div></div>
        <div className="sm-card"><div className="sm-card-k">Provider requests today</div><div className="sm-card-v">{s?.provider_requests_today ?? 0} / {s?.daily_budget ?? 0}</div></div>
        <div className="sm-card"><div className="sm-card-k">Pending alerts</div><div className="sm-card-v">{s?.pending_alerts ?? 0}</div></div>
      </div>
      {s?.scheduling === 'OFF' && s.gate_reasons?.length > 0 && <div className="sm-q-muted" data-testid="gate-reasons">Not scanning because: {s.gate_reasons.map((r: string) => nice(r)).join('; ')}.</div>}
      {s?.smart_move_plan?.requests_needed != null && <div className="sm-q-muted" data-testid="sm-plan">Smart Move evidence request on file: {s.smart_move_plan.requests_needed} provider request(s) for {s.smart_move_plan.symbols?.length ?? 0} stock(s) — waiting for the separately authorised collector.</div>}
      {status.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> Status could not be loaded: {status.error}</div>}

      <div className="sm-filters">
        <div className="sm-tabs" role="tablist" aria-label="Earnings monitor views">
          {TABS.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={`sm-tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>{label}</button>)}
        </div>
        <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Reload</button>
      </div>
      {cur?.initialLoading && <div className="sm-none" role="status">Loading…</div>}
      {cur?.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {cur.error}</div>}
      {cur?.data?.status === 'NO_DATA' && <div className="sm-q-state" role="status"><b>No monitor data yet</b><div>{cur.data.detail}</div></div>}

      {tab === 'upcoming' && upcoming.data?.status === 'OK' && (
        <>
          <label className="sm-q-muted"><input type="checkbox" checked={hist} onChange={(e) => setHist(e.target.checked)} /> include reported and canceled events (history)</label>
          {!upcoming.data.rows.length && <div className="sm-none">No upcoming earnings are tracked yet.</div>}
          <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Symbol</th><th>Earnings date</th><th>Session</th><th>Days</th><th>Calendar check</th><th>Status</th><th>Eligible</th><th>Valid predictions</th><th>Latest change</th></tr></thead>
            <tbody>{upcoming.data.rows.map((r: any) => (
              <Fragment key={r.id}>
                <tr className="sm-q-row" tabIndex={0} onClick={() => setOpen(open === 'u' + r.id ? '' : 'u' + r.id)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(open === 'u' + r.id ? '' : 'u' + r.id); }}>
                  <td><b>{r.symbol}</b><div className="sm-q-muted">{r.company || '—'}</div></td><td>{r.earnings_date}</td><td>{r.session === 'BMO' ? 'Before Open' : r.session === 'AMC' ? 'After Close' : 'not confirmed'}</td><td>{r.days_to}</td>
                  <td><span className={`sm-q-badge ${VER_TONE[r.verification] || 'gray'}`} data-testid="verification">{nice(r.verification)}</span></td>
                  <td><span className={`sm-q-badge ${r.status === 'ACTIVE' ? 'green' : r.status === 'CANCELED' ? 'red' : 'gray'}`}>{r.status}</span></td>
                  <td>{r.eligible ? 'yes' : <span className="sm-q-muted">{r.ineligible_reasons.map(nice).join(', ')}</span>}</td><td>{r.valid_predictions}</td>
                  <td>{r.latest_change ? `${nice(r.latest_change.kind)} · ${fmtTime(r.latest_change.detected_at)}` : '—'}</td>
                </tr>
                {open === 'u' + r.id && <tr><td colSpan={9}><div data-testid="versions"><b>Date / session history</b>{r.versions.map((v: any, i: number) => <div key={i} className="sm-q-muted">{fmtTime(v.observed_at)} — {v.earnings_date} {v.session} ({nice(v.reason)})</div>)}
                  {r.sources?.length > 0 && <div className="sm-q-muted">Vendors: {r.sources.map((x: any) => `${x.vendor} ${x.date}`).join(' · ')}</div>}
                  {r.outcome && <div>Real reaction: <b>{r.outcome.move_pct}%</b> ({nice(r.outcome.source)})</div>}</div></td></tr>}
              </Fragment>))}</tbody></table></div>
          <div className="sm-q-muted">Looks {upcoming.data.horizon_days} days ahead. "single vendor" means only one calendar vendor lists the event; "conflict" means the vendors disagree and no prediction is made until they agree.</div>
        </>
      )}

      {tab === 'models' && models.data?.status === 'OK' && (
        <>
          {!models.data.rows.length && <div className="sm-none">No eligible upcoming event has been scanned yet.</div>}
          <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Event</th>{models.data.models.map((m: any) => <th key={m.id}>{m.label}</th>)}</tr></thead>
            <tbody>{models.data.rows.map((r: any) => (
              <Fragment key={r.id}>
                <tr className="sm-q-row" tabIndex={0} onClick={() => setOpen(open === 'm' + r.id ? '' : 'm' + r.id)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(open === 'm' + r.id ? '' : 'm' + r.id); }}>
                  <td><b>{r.symbol}</b><div className="sm-q-muted">{r.earnings_date} {r.session}</div></td>{models.data.models.map((m: any) => <td key={m.id}><SignalCell c={r.cells[m.id]} /></td>)}</tr>
                {open === 'm' + r.id && <tr><td colSpan={models.data.models.length + 1}><History symbol={r.symbol} /></td></tr>}
              </Fragment>))}</tbody></table></div>
          <div className="sm-q-muted">{models.data.note} A call older than {models.data.stale_after_h} h is marked stale. Click a row for every stored version.</div>
          <button className="ghost-btn" onClick={() => setShowSources(!showSources)}>{showSources ? 'Hide' : 'Show'} model sources</button>
          {showSources && <Sources />}
        </>
      )}

      {tab === 'changes' && changes.data?.status === 'OK' && (
        <>
          <div className="sm-filters"><select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Change type"><option value="">All changes</option>{changes.data.kinds.map((k: string) => <option key={k} value={k}>{nice(k)}</option>)}</select>
            <span className="sm-q-muted" data-testid="latest-change">Latest change: {fmtTime(changes.data.latest_change_at)}</span></div>
          {!changes.data.rows.length && <div className="sm-none">No changes recorded.</div>}
          <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>When</th><th>Stock</th><th>Change</th><th>Model</th><th>From</th><th>To</th></tr></thead>
            <tbody>{changes.data.rows.map((r: any) => <tr key={r.id}><td>{fmtTime(r.detected_at)}</td><td><b>{r.symbol}</b></td><td>{nice(r.kind)}{r.detail?.subtype ? <div className="sm-q-muted">{nice(r.detail.subtype)}</div> : null}</td><td>{r.model || '—'}</td><td>{r.old_value ?? '—'}</td><td>{r.new_value ?? '—'}</td></tr>)}</tbody></table></div>
        </>
      )}

      {tab === 'consensus' && consensus.data?.status === 'OK' && (
        <>
          {!consensus.data.rows.length && <div className="sm-none">No events to compare yet.</div>}
          <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Event</th><th>Agreement</th><th>Majority</th><th>Comparable models</th><th>BUY</th><th>SELL</th><th>Neutral / no trade</th><th>Not counted</th></tr></thead>
            <tbody>{consensus.data.rows.map((r: any) => (
              <tr key={r.id}><td><b>{r.symbol}</b><div className="sm-q-muted">{r.earnings_date} {r.session}</div></td><td><span className={`sm-q-badge ${LEVEL_TONE[r.level] || 'gray'}`} data-testid="level">{r.level}</span></td><td>{r.majority || '—'}</td>
                <td>{r.comparable} of {r.total_models}</td><td>{r.buy.join(', ') || '—'}</td><td>{r.sell.join(', ') || '—'}</td><td>{r.flat.join(', ') || '—'}</td><td className="sm-q-muted">{r.excluded.join(', ') || '—'}</td></tr>))}</tbody></table></div>
          <div className="sm-q-muted">{consensus.data.note} STRONG needs at least {consensus.data.rule.min_comparable_models} comparable models and {Math.round(consensus.data.rule.strong_share * 100)}% pointing the same way.</div>
        </>
      )}

      {tab === 'accuracy' && accuracy.data?.status === 'OK' && (
        <>
          {!accuracy.data.rows.length && <div className="sm-none" data-testid="no-accuracy">No verified outcomes yet, so no accuracy can be shown. {accuracy.data.events_unverified} event(s) are waiting for their real reaction.</div>}
          {accuracy.data.rows.length > 0 && <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Model</th><th>Scored BUY/SELL calls (n)</th><th>Correct</th><th>Incorrect</th><th>Accuracy</th><th>Flat reaction</th><th>No directional call</th></tr></thead>
            <tbody>{accuracy.data.rows.map((r: any) => <tr key={r.model}><td><b>{r.label}</b></td><td>{r.decisive}</td><td>{r.correct}</td><td>{r.incorrect}</td>
              <td data-testid="accuracy-cell">{r.accuracy_pct != null ? `${r.accuracy_pct}%` : <span className="sm-q-muted" title={r.sample_status}>needs {accuracy.data.min_sample}+ scored calls</span>}</td><td>{r.flat_outcome}</td><td>{r.abstained}</td></tr>)}</tbody></table></div>}
          <div className="sm-q-muted">{accuracy.data.note} Events with a verified outcome: {accuracy.data.events_with_verified_outcome}; still unverified: {accuracy.data.events_unverified}.</div>
          {comparison.data?.status === 'OK' && comparison.data.rows.length > 0 && (
            <div data-testid="comparison"><b>Model vs model</b> (same events, both called BUY/SELL, decisive real reaction; shown from {comparison.data.min_shared} shared events)
              <table className="sm-mini"><thead><tr><th>Models</th><th>Shared events</th><th>Both right</th><th>Only first right</th><th>Only second right</th><th>Both wrong</th><th>Agreed</th></tr></thead>
                <tbody>{comparison.data.rows.map((r: any, i: number) => r.status === 'OK'
                  ? <tr key={i}><td>{comparison.data.labels[r.a] || r.a} vs {comparison.data.labels[r.b] || r.b}</td><td>{r.shared_events}</td><td>{r.both_right}</td><td>{r.only_a_right}</td><td>{r.only_b_right}</td><td>{r.both_wrong}</td><td>{r.agree}</td></tr>
                  : <tr key={i}><td>{comparison.data.labels[r.a] || r.a} vs {comparison.data.labels[r.b] || r.b}</td><td>{r.shared_events}</td><td colSpan={5} className="sm-q-muted">not enough shared events yet</td></tr>)}</tbody></table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
