import { Fragment, useState } from 'react';
import { AlertTriangle, Info, RefreshCw } from 'lucide-react';
import { earningsMonitor } from '../api/client';
import { useApi } from '../hooks/useApi';
import './smart-move.css';

type Tab = 'upcoming' | 'models' | 'smart' | 'changes' | 'evidence' | 'consensus' | 'accuracy' | 'health';
const TABS: [Tab, string][] = [['upcoming', 'Upcoming Earnings'], ['models', 'All 9 Models'], ['smart', 'Smart Money Move'], ['changes', 'Prediction Changes'], ['evidence', 'News & Options Activity'], ['consensus', 'Model Consensus'], ['accuracy', 'Historical Accuracy'], ['health', 'Scanner Health']];

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
const LABEL_TEXT: Record<string, string> = { NOT_COVERED: 'NOT SCORED', NO_PREDICTION_AVAILABLE: 'NO PREDICTION AVAILABLE', OUT_OF_WINDOW: 'OUTSIDE MODEL WINDOW', NO_TRADE: 'NO TRADE', NO_VALID_PREDICTION: 'NO VALID PREDICTION', LOW_DATA: 'LOW DATA', PENDING: 'PENDING', STALE: 'STALE', NEUTRAL: 'NEUTRAL' };
const LABEL_HINT: Record<string, string> = {
  NOT_COVERED: "This stock is outside this model's universe: the model scored other stocks but not this one. It is not a neutral view and not 'coming soon'.",
  NO_PREDICTION_AVAILABLE: 'No prediction exists for this event. The reason is shown with it; it is never a neutral view.',
  OUT_OF_WINDOW: 'On the calendar, but further out than the models score. No prediction exists yet; this is not a neutral view.',
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
    <div data-testid="model-cell" title={(label === 'NO_PREDICTION_AVAILABLE' && c.reason) || LABEL_HINT[label] || (c.prediction_recorded_at ? `recorded ${fmtTime(c.prediction_recorded_at)}` : undefined)}>
      <span className={cls} data-testid="cell-label" data-call={isCall ? 'yes' : 'no'}>{LABEL_TEXT[label] || label}</span>
      {c.status === 'VALID' && <div className="sm-q-muted">{c.score != null ? `score ${c.score} · ` : ''}{c.coverage_fraction != null ? `cov ${Math.round(c.coverage_fraction * 100)}%` : 'cov —'} · {c.age_h != null ? `${c.age_h} h old` : ''}</div>}
      {c.status === 'VALID' && c.source && <div className="sm-q-muted">{nice(c.source)}</div>}
      {label === 'NO_PREDICTION_AVAILABLE' && c.reason && <div className="sm-q-muted" data-testid="cell-reason">{c.reason}</div>}
      {c.origin && c.status === 'VALID' && <div className="sm-q-muted" data-testid="cell-origin">{c.origin === 'HISTORICAL_SNAPSHOT' ? 'historical snapshot' : c.origin === 'LIVE_ENGINE' ? 'live engine' : nice(c.origin)}</div>}
      {c.previous && <div className="sm-q-muted" data-testid="cell-previous">was {nice(c.previous.signal)}{c.previous.score != null ? ` (${c.previous.score})` : ''} · {fmtTime(c.previous.recorded_at)}{c.reason ? ` — ${c.reason}` : ''}</div>}
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

const STAGE_TONE: Record<string, string> = { VERIFIED: 'green', COLLECTED: 'gray', DISCOVERED: 'gray' };

const ORIGIN: Record<string, [string, string, string]> = { HISTORICAL_SNAPSHOT: ['historical snapshot', 'amber', 'A stored result of an earlier run, not a new live prediction'], LIVE_ENGINE: ['live engine', 'green', 'Scored now by the installed frozen engine'], NOT_GENERATED: ['not generated', 'gray', 'No prediction was generated for this event'] };

/** Rows are grouped by the MARKET date of the report (the plain calendar date the exchange uses): no timezone conversion is applied to a date. */
function SmartMovePanel({ d }: { d: any }) {
  const rows: any[] = d.rows || [];
  const dates: string[] = Array.from(new Set(rows.map((r) => r.earnings_date))).sort();
  return (
    <div data-testid="smart-move-panel">
      <div className="sm-q-muted" data-testid="sm-engine">{d.engine?.model_version}: {d.engine?.how_predictions_arise}. Counts: {Object.entries(d.counts || {}).map(([k, v]) => `${nice(k)} ${v}`).join(' · ') || 'none yet'}.</div>
      {!rows.length && <div className="sm-none">No upcoming event is tracked yet.</div>}
      <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Event</th><th>Smart Money Move</th><th>Origin</th><th>Coverage</th><th>Evidence as of</th><th>Engine detail</th></tr></thead>
        <tbody>{dates.flatMap((dt) => {
          const day = rows.filter((r) => r.earnings_date === dt);
          const withCall = day.filter((r) => ['BUY', 'SELL', 'NO_TRADE', 'NEUTRAL'].includes(r.label)).length;
          return [<tr key={'h' + dt} data-testid="sm-date-header"><td colSpan={6}><b>{dt}</b> <span className="sm-q-muted">market date · {day.length} event(s) · {withCall} with a prediction · {day.length - withCall} without</span></td></tr>,
            ...day.map((r) => (
          <tr key={r.id}><td><b>{r.symbol}</b><div className="sm-q-muted">{r.earnings_date} {r.session} · {r.days_to} d</div></td>
            <td><SignalCell c={{ label: r.label, status: ['BUY', 'SELL', 'NO_TRADE', 'NEUTRAL'].includes(r.label) ? 'VALID' : r.label, signal: r.signal, coverage_fraction: null, age_h: r.age_h, source: r.source, reason: r.reason?.text }} /></td>
            <td data-testid="sm-origin">{ORIGIN[r.origin] ? <span className={`sm-q-badge ${ORIGIN[r.origin][1]}`} title={ORIGIN[r.origin][2]}>{ORIGIN[r.origin][0]}</span> : <span className="sm-q-muted">—</span>}</td>
            <td>{r.coverage ?? '—'}</td><td>{r.as_of ? `${fmtTime(r.as_of)}${r.age_h != null ? ` (${r.age_h} h)` : ''}` : '—'}</td>
            <td className="sm-q-muted">{r.details ? Object.entries(r.details).filter(([k]) => k !== 'evidence_retrieved').map(([k, v]) => `${nice(k)}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ') : (r.reason ? r.reason.text : (['BUY', 'SELL', 'NO_TRADE', 'NEUTRAL'].includes(r.label) ? '—' : 'the engine has not scored this event'))}</td></tr>))];
        })}</tbody></table></div>
      {d.windows ? <div className={d.windows.open_without_result ? 'sm-fresh stale' : 'sm-q-muted'} data-testid="sm-windows" role={d.windows.open_without_result ? 'alert' : undefined}>Prediction windows (from the live engine, {fmtTime(d.windows.as_of)}): {d.windows.open} event(s) inside the window now, {d.windows.open_without_result} still without a result{d.windows.first_cutoff_utc ? `; the first window closes ${fmtTime(d.windows.first_cutoff_utc)} and after that no prediction is allowed` : ''}{Object.keys(d.windows.missing_by_date || {}).length ? ` (missing by date: ${Object.entries(d.windows.missing_by_date).map(([k, v]) => `${k} ${v}`).join(', ')})` : ''}.</div> : <div className="sm-q-muted" data-testid="sm-windows-na">Prediction-window monitoring needs the live engine; with only a stored snapshot the windows cannot be read, so they are not guessed.</div>}
      {d.coverage && <div className="sm-q-muted" data-testid="sm-coverage">Latest {d.coverage.source === 'HISTORICAL_SNAPSHOT' ? 'historical snapshot' : 'engine run'}: made {fmtTime(d.coverage.generated_at)}, scored {d.coverage.rows} event(s) dated {(d.coverage.event_dates || []).join(', ') || 'n/a'}. Events outside those dates, or not among those scored, have no prediction and say why.</div>}
      <div className="sm-q-muted">{d.note}</div>
      {d.plan?.requests_needed != null && <div className="sm-q-muted">Evidence request on file: {d.plan.requests_needed} provider request(s) for {d.plan.symbols?.length ?? 0} stock(s), waiting for the separately authorised collector.</div>}
    </div>
  );
}

function EvidencePanel({ d, symbol, setSymbol }: { d: any; symbol: string; setSymbol: (v: string) => void }) {
  const rows: any[] = d.rows || [];
  return (
    <div data-testid="evidence-panel">
      <div className="sm-filters"><input aria-label="Filter by symbol" placeholder="Symbol (all)" value={symbol} maxLength={12} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        <span className="sm-q-muted">{d.last_scan ? `Last evidence scan ${fmtTime(d.last_scan.at)}: ${d.last_scan.items} items, ${d.last_scan.new} new, ${d.last_scan.changed} changed` : 'The evidence scan has not run yet.'}</span></div>
      <table className="sm-mini" data-testid="evidence-summary"><thead><tr><th>Evidence type</th><th>Collected</th><th>Verified (provider field)</th><th>Can change a score?</th></tr></thead>
        <tbody>{(d.summary || []).map((k: any) => <tr key={k.kind}><td>{nice(k.kind)}</td><td>{k.collected}</td><td>{k.verified}</td><td>{k.feeds_a_score ? 'only if a model reads it' : 'never: shown here only'}</td></tr>)}</tbody></table>
      {!rows.length && <div className="sm-none">No evidence items stored yet.</div>}
      {rows.length > 0 && <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Stock</th><th>Type</th><th>Item</th><th>Stage</th><th>Relevant to</th><th>Used by</th><th>Seen</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id}><td><b>{r.symbol}</b></td><td>{nice(r.kind)}</td><td>{r.title}{r.data?.total_premium != null ? <div className="sm-q-muted">${Math.round(r.data.total_premium / 1000)}k premium · {r.data.bullish_share ?? '—'}% bullish · {r.data.prints} prints</div> : null}</td>
          <td><span className={`sm-q-badge ${STAGE_TONE[r.stage] || 'gray'}`}>{nice(r.stage)}</span></td><td>{r.relevant_models.join(', ') || '—'}</td><td>{r.used_by_models.join(', ') || '—'}</td><td className="sm-q-muted">{fmtTime(r.last_seen)}</td></tr>)}</tbody></table></div>}
      <div className="sm-q-muted">{d.note}</div>
      {(d.unsupported || []).map((u: string) => <div key={u} className="sm-q-muted">• {u}</div>)}
    </div>
  );
}

function HealthPanel({ d }: { d: any }) {
  return (
    <div data-testid="health-panel">
      <div className="sm-summary">
        <div className="sm-card"><div className="sm-card-k">Overall</div><div className="sm-card-v"><span className={`sm-q-badge ${d.healthy ? 'green' : 'red'}`} data-testid="health-badge">{d.healthy ? 'HEALTHY' : 'NEEDS ATTENTION'}</span></div></div>
        <div className="sm-card"><div className="sm-card-k">Queue</div><div className="sm-card-v">{Object.entries(d.queue || {}).map(([k, v]) => `${k} ${v}`).join(' · ') || 'empty'}</div></div>
        <div className="sm-card"><div className="sm-card-k">Scan heartbeat</div><div className="sm-card-v">{d.heartbeat?.scans_age_s != null ? `${d.heartbeat.scans_age_s} s ago` : 'none'}</div></div>
        <div className="sm-card"><div className="sm-card-k">Provider requests today</div><div className="sm-card-v">{Object.values(d.provider_requests_today || {}).reduce((a: number, b: any) => a + Number(b || 0), 0) || d.provider_requests_today || 0}</div></div>
        <div className="sm-card"><div className="sm-card-k">Pending alerts</div><div className="sm-card-v">{d.pending_alerts ?? 0}</div></div>
      </div>
      {d.approval_days_left != null && <div className={d.approval_days_left <= 3 ? 'sm-fresh stale' : 'sm-q-muted'} data-testid="approval-left" role={d.approval_days_left <= 3 ? 'alert' : undefined}>Owner approval ends {fmtTime(d.approval_expires_at)} ({d.approval_days_left} days left). When it ends the scheduler stops until a new approval is signed.</div>}
      {d.usage_by_day?.length > 0 && <table className="sm-mini" data-testid="usage"><thead><tr><th>Day</th><th>Provider requests by the monitor</th><th>Failures</th></tr></thead><tbody>{d.usage_by_day.map((u: any) => <tr key={u.day}><td>{u.day}</td><td>{u.requests}</td><td>{u.failures}</td></tr>)}</tbody></table>}
      {d.refresh_plan && <div data-testid="refresh-plan" className="sm-q-muted">Refresh plan ({d.refresh_plan.profile}): daily cap {d.refresh_plan.daily_cap}, reserved {d.refresh_plan.external_reserve} for the app's other collectors, {d.refresh_plan.calendar_reserve} for the calendar and {d.refresh_plan.outcome_reserve} for outcomes → {d.refresh_plan.event_slots} event refresh(es) fit; {d.refresh_plan.unfunded_count} of {d.refresh_plan.needing_refresh} events that need one do not. {d.refresh_plan.app_requests_today != null ? `The application has made ${d.refresh_plan.app_requests_today} request(s) today in total.` : ''}
        {d.refresh_plan.unfunded?.length > 0 && <div>Not covered: {d.refresh_plan.unfunded.slice(0, 25).map((u: any) => `${u.symbol} ${u.earnings_date}`).join(', ')}{d.refresh_plan.unfunded_count > 25 ? ` and ${d.refresh_plan.unfunded_count - 25} more` : ''}</div>}</div>}
      <table className="sm-mini" data-testid="recent-runs"><thead><tr><th>Job</th><th>Started</th><th>Finished</th><th>Execution (ms)</th><th>Events processed</th><th>Waited in queue (ms)</th><th>Result</th></tr></thead>
        <tbody>{(d.recent_runs || []).map((r: any, i: number) => <tr key={i}><td>{nice(r.kind)}</td><td>{fmtTime(r.started_at)}</td><td>{r.finished_at ? fmtTime(r.finished_at) : '—'}</td>
          <td>{r.duration_ms != null ? r.duration_ms : <span className="sm-q-muted">not recorded</span>}</td><td>{r.events_processed ?? '—'}</td><td>{r.queue_wait_ms != null ? r.queue_wait_ms : '—'}</td>
          <td>{r.ok === null ? (r.interrupted ? 'interrupted (never finished)' : 'running') : r.ok ? 'ok' : 'failed or blocked'}</td></tr>)}</tbody></table>
      {d.stuck_jobs?.length > 0 && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {d.stuck_jobs.length} job(s) past their lease (stuck): {d.stuck_jobs.map((j: any) => j.kind).join(', ')}</div>}
      {d.dead_jobs?.length > 0 && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {d.dead_jobs.length} job(s) gave up after repeated failures.</div>}
      {d.duplicate_active_events?.length > 0 && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> Duplicate active events: {d.duplicate_active_events.map((x: any) => x.symbol).join(', ')}</div>}
      {d.duplicate_prediction_rows > 0 && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {d.duplicate_prediction_rows} duplicated prediction version(s).</div>}
      <div className="sm-q-muted">No stuck jobs, duplicate events or duplicate predictions are expected; the figures above come from the durable queue and the run log.</div>
    </div>
  );
}

const PAGE = 200;
const SCOPE_TEXT: Record<string, string> = { all: 'All events', window: 'Inside the eight-model window', outside: 'Outside the window (listed, never scored)', withcall: 'With at least one fresh model call', nocall: 'Inside the window, no fresh call' };

/** One definition of scope for every list: market date, scope and page, plus the totals that always describe ALL tracked events. */
function Pager({ d, date, setDate, scope, setScope, page, setPage, days, setDays, smv, setSmv }: { d: any; date: string; setDate: (v: string) => void; scope: string; setScope: (v: string) => void; page: number; setPage: (n: number) => void; days: string; setDays: (v: string) => void; smv: string; setSmv: (v: string) => void }) {
  const sm = d.summary || {};
  const total: number = d.total ?? 0;
  const from = total ? page * PAGE + 1 : 0, to = Math.min(total, page * PAGE + (d.count ?? 0));
  return (
    <div data-testid="pager">
      <div className="sm-q-muted" data-testid="scope-summary">{sm.events ?? 0} event(s) on {sm.dates ?? 0} market date(s), up to {sm.max_days_ahead ?? 0} days ahead · {sm.in_window ?? 0} inside the eight-model window ({sm.with_fresh_call ?? 0} with a fresh call, {sm.in_window_without_call ?? 0} without) · {sm.outside_window ?? 0} outside it, listed and never scored.
        {sm.no_call_reasons && Object.keys(sm.no_call_reasons).length ? <span data-testid="why-no-call"> Why {sm.in_window_without_call ?? 0} in-window event(s) have no fresh call: {Object.entries(sm.no_call_reasons).map(([k, v]: any) => `${nice(k)} ${v.events}`).join(' · ')}.</span> : null}
        {sm.fresh_calls_per_model ? ` Fresh calls per model: ${Object.entries(sm.fresh_calls_per_model).map(([m, n]) => `${m} ${n}`).join(' · ')}.` : ''}</div>
      <div className="sm-filters">
        <select aria-label="Market date" value={date} onChange={(e) => { setDate(e.target.value); setPage(0); }}>
          <option value="">All dates ({sm.events ?? 0})</option>
          {(d.by_date || []).map((x: any) => <option key={x.date} value={x.date}>{x.date} · {x.events} event(s), {x.in_window} in window, {x.with_fresh_call} with a call</option>)}
        </select>
        <select aria-label="Days ahead" value={days} onChange={(e) => { setDays(e.target.value); setPage(0); }}>
          <option value="">Next 30 days</option><option value="0">Today</option><option value="1">Today and tomorrow</option><option value="7">Next 7 days</option><option value="15">Next 15 days</option>
        </select>
        <select aria-label="Smart Money Move" value={smv} onChange={(e) => { setSmv(e.target.value); setPage(0); }}>
          <option value="">Any Smart Money Move status</option><option value="BUY">BUY</option><option value="SELL">SELL</option><option value="NO_TRADE">NO TRADE</option><option value="NOT_SCORED">NOT SCORED</option>
        </select>
        <select aria-label="Scope" value={scope} onChange={(e) => { setScope(e.target.value); setPage(0); }}>
          {Object.entries(SCOPE_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="sm-q-muted" data-testid="page-range">{from}–{to} of {total}</span>
        <button className="ghost-btn" disabled={page <= 0} onClick={() => setPage(page - 1)}>Previous</button>
        <button className="ghost-btn" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</button>
      </div>
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
  const [evSymbol, setEvSymbol] = useState('');
  const [mDate, setMDate] = useState('');
  const [mScope, setMScope] = useState('all');
  const [mPage, setMPage] = useState(0);
  const [mDays, setMDays] = useState('');
  const [mSm, setMSm] = useState('');
  const q = { date: mDate, scope: mScope === 'all' ? '' : mScope, limit: PAGE, offset: mPage * PAGE, days: (mDays === '' ? '' : Number(mDays)) as number | '', sm: mSm };
  const status = useApi<any>((s) => earningsMonitor.status(s), [], { refreshMs: 60_000 });
  const upcoming = useApi<any>((s) => earningsMonitor.upcoming(hist, q, s), [hist, mDate, mScope, mPage, mDays, mSm], { refreshMs: 60_000, enabled: tab === 'upcoming' });
  const models = useApi<any>((s) => earningsMonitor.models(q, s), [mDate, mScope, mPage, mDays, mSm], { refreshMs: 60_000, enabled: tab === 'models' });
  const changes = useApi<any>((s) => earningsMonitor.changes(kind, s), [kind], { refreshMs: 60_000, enabled: tab === 'changes' });
  const consensus = useApi<any>((s) => earningsMonitor.consensus(s), [], { refreshMs: 60_000, enabled: tab === 'consensus' });
  const accuracy = useApi<any>((s) => earningsMonitor.accuracy(s), [], { refreshMs: 300_000, enabled: tab === 'accuracy' });
  const outcomes = useApi<any>((s) => earningsMonitor.outcomes(s), [], { refreshMs: 300_000, enabled: tab === 'accuracy' });
  const comparison = useApi<any>((s) => earningsMonitor.comparison(s), [], { refreshMs: 300_000, enabled: tab === 'accuracy' });
  const smart = useApi<any>((s) => earningsMonitor.smartMove(q, s), [mDate, mScope, mPage, mDays, mSm], { refreshMs: 60_000, enabled: tab === 'smart' });
  const evidence = useApi<any>((s) => earningsMonitor.evidence(evSymbol, s), [evSymbol], { refreshMs: 60_000, enabled: tab === 'evidence' });
  const health = useApi<any>((s) => earningsMonitor.health(s), [], { refreshMs: 30_000, enabled: tab === 'health' });
  const reload = () => { status.refresh(); upcoming.refresh(); models.refresh(); changes.refresh(); consensus.refresh(); accuracy.refresh(); comparison.refresh(); smart.refresh(); evidence.refresh(); health.refresh(); };
  const s = status.data;
  const badge = scanBadge(s);
  const cur: any = { upcoming, models, smart, changes, evidence, consensus, accuracy, health }[tab];

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
          {TABS.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={`sm-tab ${tab === k ? 'on' : ''}`} onClick={() => { setTab(k); setMPage(0); }}>{label}</button>)}
        </div>
        <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Reload</button>
      </div>
      {cur?.initialLoading && <div className="sm-none" role="status">Loading…</div>}
      {cur?.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {cur.error}</div>}
      {cur?.data?.status === 'NO_DATA' && <div className="sm-q-state" role="status"><b>No monitor data yet</b><div>{cur.data.detail}</div></div>}

      {tab === 'upcoming' && upcoming.data?.status === 'OK' && (
        <>
          <Pager d={upcoming.data} date={mDate} setDate={setMDate} scope={mScope} setScope={setMScope} page={mPage} setPage={setMPage} days={mDays} setDays={setMDays} smv={mSm} setSmv={setMSm} />
          <label className="sm-q-muted"><input type="checkbox" checked={hist} onChange={(e) => setHist(e.target.checked)} /> include reported and canceled events (history)</label>
          {!upcoming.data.rows.length && <div className="sm-none">No upcoming earnings are tracked yet.</div>}
          <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Symbol</th><th>Earnings date</th><th>Session</th><th>Days</th><th>Calendar check</th><th>Last verified</th><th>Status</th><th>Eligible</th><th>Models with a fresh call</th><th>Evidence</th><th>Latest change</th></tr></thead>
            <tbody>{upcoming.data.rows.map((r: any) => (
              <Fragment key={r.id}>
                <tr className="sm-q-row" tabIndex={0} onClick={() => setOpen(open === 'u' + r.id ? '' : 'u' + r.id)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(open === 'u' + r.id ? '' : 'u' + r.id); }}>
                  <td><b>{r.symbol}</b><div className="sm-q-muted">{r.company || '—'}</div></td><td>{r.earnings_date}</td><td>{r.session === 'BMO' ? 'Before Open' : r.session === 'AMC' ? 'After Close' : 'not confirmed'}</td><td>{r.days_to}</td>
                  <td><span className={`sm-q-badge ${VER_TONE[r.verification] || 'gray'}`} data-testid="verification">{nice(r.verification)}</span></td><td className="sm-q-muted">{r.last_verified ? fmtTime(r.last_verified) : 'never'}</td>
                  <td><span className={`sm-q-badge ${r.status === 'ACTIVE' ? 'green' : r.status === 'CANCELED' ? 'red' : 'gray'}`}>{r.status}</span></td>
                  <td>{r.eligible ? 'yes' : r.out_of_window ? <span className="sm-q-badge gray" data-testid="oow">outside {upcoming.data.model_window_days}-day model window</span> : <span className="sm-q-muted">{r.ineligible_reasons.map(nice).join(', ')}</span>}</td>
                  <td data-testid="coverage">{r.out_of_window ? <span className="sm-q-muted">outside the model window</span> : r.no_call_reason && !(r.fresh_predictions > 0) ? <span className="sm-q-badge gray" title={upcoming.data?.summary?.reason_help?.[r.no_call_reason]}>{nice(r.no_call_reason)}</span> : <span className={`sm-q-badge ${r.fresh_predictions >= 7 ? 'green' : r.fresh_predictions >= 4 ? 'amber' : 'gray'}`}>{r.fresh_predictions ?? r.valid_predictions} of {r.models_total ?? 9}</span>}{r.valid_predictions > (r.fresh_predictions ?? r.valid_predictions) ? <div className="sm-q-muted">{r.valid_predictions - r.fresh_predictions} stale</div> : null}</td>
                  <td className="sm-q-muted" data-testid="evidence-fresh">{r.evidence_items ? `${r.evidence_items} item(s), newest ${fmtTime(r.last_evidence_at)}` : 'none stored'}</td>
                  <td>{r.latest_change ? `${nice(r.latest_change.kind)} · ${fmtTime(r.latest_change.detected_at)}` : '—'}</td>
                </tr>
                {open === 'u' + r.id && <tr><td colSpan={12}><div data-testid="versions"><b>Date / session history</b>{r.versions.map((v: any, i: number) => <div key={i} className="sm-q-muted">{fmtTime(v.observed_at)} — {v.earnings_date} {v.session} ({nice(v.reason)})</div>)}
                  {r.sources?.length > 0 && <div className="sm-q-muted">Vendors: {r.sources.map((x: any) => `${x.vendor} ${x.date}`).join(' · ')}</div>}
                  {r.outcome && <div>Real reaction: <b>{r.outcome.move_pct}%</b> ({nice(r.outcome.source)})</div>}</div></td></tr>}
              </Fragment>))}</tbody></table></div>
          <div className="sm-q-muted">Looks {upcoming.data.horizon_days} days ahead; the eight models score the next {upcoming.data.model_window_days} days. "single vendor" means only one calendar vendor lists the event; "conflict" means the vendors disagree and no prediction is made until they agree.</div>
        </>
      )}

      {tab === 'models' && models.data?.status === 'OK' && (
        <>
          <Pager d={models.data} date={mDate} setDate={setMDate} scope={mScope} setScope={setMScope} page={mPage} setPage={setMPage} days={mDays} setDays={setMDays} smv={mSm} setSmv={setMSm} />
          {!models.data.rows.length && <div className="sm-none">No eligible upcoming event has been scanned yet.</div>}
          <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Event</th>{models.data.models.map((m: any) => <th key={m.id}>{m.label}</th>)}</tr></thead>
            <tbody>{models.data.rows.map((r: any) => (
              <Fragment key={r.id}>
                <tr className="sm-q-row" tabIndex={0} onClick={() => setOpen(open === 'm' + r.id ? '' : 'm' + r.id)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(open === 'm' + r.id ? '' : 'm' + r.id); }}>
                  <td><b>{r.symbol}</b><div className="sm-q-muted">{r.earnings_date} {r.session}</div></td>{models.data.models.map((m: any) => <td key={m.id}>{r.out_of_window ? <SignalCell c={{ label: 'OUT_OF_WINDOW', status: 'OUT_OF_WINDOW' }} /> : <SignalCell c={r.cells[m.id]} />}</td>)}</tr>
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
          <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>When</th><th>Stock</th><th>Change</th><th>Model</th><th>From (previous)</th><th>To (latest)</th><th>Why</th></tr></thead>
            <tbody>{changes.data.rows.map((r: any) => <tr key={r.id}><td>{fmtTime(r.detected_at)}</td><td><b>{r.symbol}</b></td><td>{nice(r.kind)}{r.detail?.subtype ? <div className="sm-q-muted">{nice(r.detail.subtype)}</div> : null}</td><td>{r.model || '—'}</td><td>{r.old_value ?? '—'}</td><td>{r.new_value ?? '—'}</td><td className="sm-q-muted">{r.detail?.reason || (r.detail?.title ? `${nice(r.detail.kind)}: ${r.detail.title}` : '—')}</td></tr>)}</tbody></table></div>
        </>
      )}

      {tab === 'smart' && smart.data?.status === 'OK' && <><Pager d={smart.data} date={mDate} setDate={setMDate} scope={mScope} setScope={setMScope} page={mPage} setPage={setMPage} days={mDays} setDays={setMDays} smv={mSm} setSmv={setMSm} /><SmartMovePanel d={smart.data} /></>}
      {tab === 'evidence' && evidence.data?.status === 'OK' && <EvidencePanel d={evidence.data} symbol={evSymbol} setSymbol={setEvSymbol} />}
      {tab === 'health' && health.data?.status === 'OK' && <HealthPanel d={health.data} />}

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
          {accuracy.data.rows.length > 0 && <div className="sm-table-wrap"><table className="sm-table sm-q-table"><thead><tr><th>Model</th><th>Scored BUY/SELL calls (n)</th><th>Correct</th><th>Incorrect</th><th>Accuracy</th><th>BUY accuracy</th><th>SELL accuracy</th><th>Avg real move</th><th>Verified outcomes</th><th>Flat reaction</th><th>No directional call</th></tr></thead>
            <tbody>{accuracy.data.rows.map((r: any) => <tr key={r.model}><td><b>{r.label}</b></td><td>{r.decisive}</td><td>{r.correct}</td><td>{r.incorrect}</td>
              <td data-testid="accuracy-cell">{r.accuracy_pct != null ? `${r.accuracy_pct}%` : <span className="sm-q-muted" title={r.sample_status}>needs {accuracy.data.min_sample}+ scored calls</span>}</td><td>{r.buy_accuracy_pct != null ? `${r.buy_accuracy_pct}%` : <span className="sm-q-muted">n={r.buy_calls}</span>}</td><td>{r.sell_accuracy_pct != null ? `${r.sell_accuracy_pct}%` : <span className="sm-q-muted">n={r.sell_calls}</span>}</td><td>{r.avg_abs_move_pct != null ? `${r.avg_abs_move_pct}%` : '—'}</td><td>{r.verified_outcomes}</td><td>{r.flat_outcome}</td><td>{r.abstained}</td></tr>)}</tbody></table></div>}
          {accuracy.data.agreement_vs_outcome && <div className="sm-q-muted" data-testid="agreement-outcome">Agreement vs real outcome: {accuracy.data.agreement_vs_outcome.status === 'OK' ? `${accuracy.data.agreement_vs_outcome.strong_agreement_accuracy_pct}% of ${accuracy.data.agreement_vs_outcome.strong_agreement_events} strong-agreement events moved the agreed way` : accuracy.data.agreement_vs_outcome.status} · events with comparable disagreement: {accuracy.data.agreement_vs_outcome.disagreement_events}. Outcomes seen but not verifiable: {accuracy.data.events_outcome_unverifiable}.</div>}
          <div className="sm-q-muted">{accuracy.data.note} Events with a verified outcome: {accuracy.data.events_with_verified_outcome}; still unverified: {accuracy.data.events_unverified}.</div>
          {outcomes.data?.status === 'OK' && (
            <div data-testid="outcomes"><b>Verified earnings reactions</b> (calls made before the reporting window opened; later calls are excluded)
              {!outcomes.data.rows.length && <div className="sm-q-muted">No reaction has been verified yet.</div>}
              {outcomes.data.rows.length > 0 && <table className="sm-mini"><thead><tr><th>Event</th><th>Real move</th><th>Calls used (cutoff)</th><th>Graded</th></tr></thead>
                <tbody>{outcomes.data.rows.map((r: any, i: number) => <tr key={i}><td><b>{r.symbol}</b> {r.earnings_date} {r.session}</td><td>{r.verified ? `${r.move_pct}%` : <span className="sm-q-muted">not verifiable</span>}</td>
                  <td className="sm-q-muted">{Object.entries(r.calls).map(([m, c]: any) => `${m} ${nice(c.signal)}`).join(' · ') || 'no call existed before the cutoff'} ({fmtTime(r.prediction_cutoff)})</td>
                  <td className="sm-q-muted">{Object.entries(r.results).map(([m, v]: any) => `${m} ${nice(v)}`).join(' · ') || '—'}</td></tr>)}</tbody></table>}
              {outcomes.data.waiting_for_outcome?.length > 0 && <div className="sm-q-muted" data-testid="outcomes-waiting">Waiting for a verified reaction: {outcomes.data.waiting_for_outcome.map((w: any) => `${w.symbol} ${w.earnings_date}`).join(', ')}</div>}
            </div>
          )}
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
