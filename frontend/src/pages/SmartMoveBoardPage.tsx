import { Fragment, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Clock, FlaskConical, RefreshCw, Search, ShieldAlert, X } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveBoard, smartMoveTracking } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './smart-move.css';

type Status = 'BUY' | 'SELL' | 'NO_TRADE' | 'DATA_INCOMPLETE' | 'PENDING';

const STATUS_LABEL: Record<Status, string> = { BUY: 'BUY', SELL: 'SELL', NO_TRADE: 'NEUTRAL / NO TRADE', DATA_INCOMPLETE: 'DATA INCOMPLETE', PENDING: 'PENDING' };
const STATUS_ORDER: Status[] = ['BUY', 'SELL', 'NO_TRADE', 'DATA_INCOMPLETE', 'PENDING'];
const STATE_LABEL: Record<string, string> = { AVAILABLE: 'available', MISSING: 'missing', PARTIAL: 'partly available', NOT_CAPTURED: 'not captured yet' };

function fmtET(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' });
}

const TZ_OPTS: Intl.DateTimeFormatOptions = { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false };
/** Both clocks come from the IANA zone database, so New York's daylight-saving change (Sun 1 Nov 2026, EDT to EST) is handled; Dubai has no DST. */
function fmtDubai(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : `${d.toLocaleString('en-GB', { ...TZ_OPTS, timeZone: 'Asia/Dubai' })} GST`;
}
function fmtNY(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString('en-US', { ...TZ_OPTS, timeZone: 'America/New_York', timeZoneName: 'short' });
}
function fmtExact(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const o: Intl.DateTimeFormatOptions = { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
  return `${d.toISOString().replace('T', ' ').slice(0, 19)} UTC · ${d.toLocaleString('en-US', { ...o, timeZone: 'America/New_York', timeZoneName: 'short' })} · ${d.toLocaleString('en-GB', { ...o, timeZone: 'Asia/Dubai' })} GST`;
}
function countdown(ms: number): string {
  const t = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(t / 86400), h = Math.floor((t % 86400) / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  return `${d ? `${d}d ` : ''}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}
function Countdown({ row, nowMs }: { row: any; nowMs: number }) {
  const a = row.window?.start_utc ? Date.parse(row.window.start_utc) : NaN;
  const b = row.window?.cutoff_utc ? Date.parse(row.window.cutoff_utc) : NaN;
  if (Number.isNaN(a) || Number.isNaN(b)) return <span className="sm-bd-cd unknown">no window (timing unknown)</span>;
  if (row.evaluated) return <span className="sm-bd-cd done">captured</span>;
  if (nowMs < a) return <span className="sm-bd-cd ahead">opens in {countdown(a - nowMs)}</span>;
  if (nowMs <= b) return <span className="sm-bd-cd open">OPEN · closes in {countdown(b - nowMs)}</span>;
  return <span className="sm-bd-cd closed">window closed</span>;
}

function fmtAge(h?: number | null): string {
  if (h == null) return '';
  if (h < 1) return 'under 1 h ago';
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

function StatusPill({ status, small }: { status: Status; small?: boolean }) {
  return <span className={`sm-bd-pill ${status.toLowerCase()} ${small ? 'small' : ''}`}><i />{STATUS_LABEL[status]}</span>;
}

function V2Panel({ v2, other, variant, coll, v1rows }: { v2: any; other: any; variant: string; coll: any; v1rows: any[] }) {
  if (!v2?.available) return null;
  const ev: any[] = v2.events || [];
  const cnt: Record<string, number> = {};
  ev.forEach((e) => { cnt[e.display_status] = (cnt[e.display_status] || 0) + 1; });
  const real = ev.filter((e) => e.signal === 'BUY' || e.signal === 'SELL');
  const v1by: Record<string, any> = {};
  v1rows.forEach((r) => { v1by[r.symbol] = r; });
  const dis = ev.filter((e) => { const r = v1by[e.symbol]; return r && (r.status === 'BUY' || r.status === 'SELL') && (e.signal === 'BUY' || e.signal === 'SELL') && r.status !== e.signal; }).map((e) => e.symbol);
  const maxDir = ev.reduce((m, e) => Math.max(m, e.directional_available_points ?? 0), 0);
  const elig = ev.filter((e) => e.eligibility && e.eligibility.liquidity_ok && e.eligibility.entry_ok && e.eligibility.capture_window_ok).length;
  return (
    <div className="sm-bd-summary" role="region" aria-label="V2 summary">
      <div className="sm-bd-sumhead"><b>V2 (100 points, EXPERIMENTAL)</b> · {variant} · BUY {cnt.BUY || 0} · SELL {cnt.SELL || 0} · NO TRADE {cnt.NO_TRADE || 0} · DATA INCOMPLETE {cnt.DATA_INCOMPLETE || 0} · of {ev.length} stocks</div>
      <div className="sm-bd-sumgrid">
        <span>Best directional evidence <b>{maxDir}/100</b> (60 needed)</span><span>Capture/liquidity eligible <b>{elig}</b></span><span>V1 vs V2 opposite BUY/SELL <b>{dis.length}</b>{dis.length ? ` (${dis.slice(0, 5).join(', ')})` : ''}</span>
        <span>Real V2 BUY/SELL (not validated) <b>{real.length}</b></span>
        {coll && <span>Collected <b>{(coll.collected_symbols || []).length}</b> stocks · attempts <b>{coll.attempts_total}</b>/{coll.cap} · errors <b>{(coll.failures || []).length}</b> · stop events <b>{(coll.stop_events || []).length}</b></span>}
        {coll && <span>Collection run <b>{coll.status_full || coll.status_probe}</b> · last {fmtExact(coll.phases?.full_end?.at || coll.phases?.full_start?.at)} · kill switch <b>{coll.kill_switch_final?.engaged ? 'engaged' : 'NOT engaged'}</b></span>}
      </div>
      <div className="sm-bd-next">{v2.banner} {other?.events ? `Other variant: BUY ${other.events.filter((e: any) => e.signal === 'BUY').length}, SELL ${other.events.filter((e: any) => e.signal === 'SELL').length}.` : ''}</div>
    </div>
  );
}

function CandPanel({ cand }: { cand: any }) {
  if (!cand?.available) return null;
  const c = cand.summary?.counts?.[cand.variant] || {};
  const p = cand.summary?.primary_causes?.[cand.variant] || {};
  const f = cand.frozen_v2_counts?.[cand.variant] || {};
  return (
    <div className="sm-bd-summary" role="region" aria-label="V2 candidate summary">
      <div className="sm-bd-sumhead"><b>V2 candidate {cand.version} (RESEARCH ONLY)</b> · {cand.variant} · BUY {c.BUY || 0} · SELL {c.SELL || 0} · NO TRADE {c.NO_TRADE || 0} · DATA INCOMPLETE {c.DATA_INCOMPLETE || 0} · accuracy <b>NOT VERIFIED</b></div>
      <div className="sm-bd-sumgrid">
        <span>Frozen V2 baseline: BUY {f.BUY || 0} · SELL {f.SELL || 0} · NO TRADE {f.NO_TRADE || 0} · DATA INCOMPLETE {f.DATA_INCOMPLETE || 0}</span>
        {Object.entries<number>(p).map(([k, n]) => <span key={k}>Incomplete because {k.replace(/_/g, ' ').toLowerCase()} <b>{n}</b></span>)}
      </div>
      <div className="sm-bd-next">{cand.banner} Weights, thresholds and factor rules are identical to the frozen V2; nothing is redistributed.</div>
    </div>
  );
}

function OpsPanel({ ops, error }: { ops: any; error?: string | null }) {
  if (error) return <div className="sm-fresh stale"><AlertTriangle size={14} /> Operations status unavailable: {error}</div>;
  if (!ops) return null;
  const bad = ops.health?.checks?.filter((c: any) => !c.ok) || [];
  return (
    <div className="sm-bd-summary" role="region" aria-label="Operations status">
      <div className="sm-bd-sumhead"><b>Operations</b> · scheduler: {ops.scheduler.mode}{ops.scheduler.recurring_enabled ? '' : ' · recurring collection OFF'} · kill switch: <b>{ops.kill_switch.engaged ? 'engaged' : ops.kill_switch.engaged === false ? 'NOT engaged' : 'unknown'}</b></div>
      <div className="sm-bd-sumgrid">
        <span>Provider attempts (last run) <b>{ops.provider.attempts_total ?? '—'}</b></span><span>Provider errors <b>{ops.provider.errors}</b></span><span>Stop events <b>{ops.provider.stop_events}</b></span>
        <span>Data health <b>{ops.health.ok ? 'OK' : `${bad.length} problem(s)`}</b></span><span>Registry events <b>{ops.event_registry.current}</b></span>
        <span>Calendar age <b>{ops.scheduler.calendar_age_hours ?? '—'} h</b></span><span>Est. requests/day <b>{ops.scheduler.estimated_daily_requests_for_universe}</b></span>
      </div>
      {ops.alerts.length > 0 && <div className="sm-bd-blocker">{ops.alerts.slice(0, 6).map((a: any, i: number) => <div key={i}>{a.type}: {a.symbol || a.why || ''}</div>)}</div>}
      {bad.map((c: any) => <div key={c.check} className="sm-bd-blocker">Health: {c.check} — {c.detail}</div>)}
      {ops.scheduler.next_tick_plan.actions.map((a: any, i: number) => <div key={i} className="sm-bd-next">Next tick would: {a.action}{a.symbols ? ` (${a.symbols.length} stocks)` : ''} — plan only, not authorized to run</div>)}
    </div>
  );
}

/* Smart Move earnings board: every eligible upcoming earnings event with an honest status.
   Read only, saved data only. The colour is a display of what the frozen engine returned; it never creates a signal. */
export default function SmartMoveBoardPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [variant, setVariant] = useState<'continuation' | 'contrarian'>('continuation');
  const [status, setStatus] = useState<'' | Status>('');
  const [kind, setKind] = useState('');
  const [q, setQ] = useState('');
  const [sector, setSector] = useState('');
  const [date, setDate] = useState('');
  const [evidenceF, setEvidenceF] = useState('');
  const [model, setModel] = useState<'V1' | 'V2' | 'V2C'>('V1');
  const [auditMsg, setAuditMsg] = useState('');
  const [open, setOpen] = useState('');
  const [, setTick] = useState(0);
  useEffect(() => { const id = setInterval(() => setTick((x) => x + 1), 1000); return () => clearInterval(id); }, []);

  const query = useMemo(() => {
    const p = new URLSearchParams({ variant });
    if (status) p.set('status', status);
    if (kind) p.set('kind', kind);
    if (q.trim()) p.set('q', q.trim());
    return p.toString();
  }, [variant, status, kind, q]);
  const board = useApi<any>((s) => smartMoveBoard.board(query, s), [query], { refreshMs: 30_000 });
  const d = board.data;
  const ops = useApi<any>((s) => smartMoveBoard.ops(s), [], { refreshMs: 30_000 });
  const v2 = useApi<any>((s) => smartMoveBoard.v2(variant, s), [variant], { refreshMs: 30_000 });
  const audits = useApi<any>((s) => smartMoveBoard.audits(s), [auditMsg], { refreshMs: 60_000 });
  const coll = useApi<any>((s) => smartMoveBoard.v2Collection(s), [], { refreshMs: 60_000 });
  const v2c = useApi<any>((s) => smartMoveBoard.v2(variant === 'continuation' ? 'contrarian' : 'continuation', s), [variant], { refreshMs: 60_000 });
  const cand = useApi<any>((s) => smartMoveBoard.v2c(variant, s), [variant], { refreshMs: 60_000 });
  const candBy: Record<string, any> = {};
  (cand.data?.events || []).forEach((e: any) => { candBy[`${e.symbol}|${e.earnings_date}`] = e; });
  const outcomes = useApi<any>((s) => smartMoveTracking.outcomes(s), [], { refreshMs: 60_000 });
  const v2By: Record<string, any> = {};
  (v2.data?.events || []).forEach((e: any) => { v2By[`${e.symbol}|${e.earnings_date}`] = e; });
  const outBy: Record<string, any> = {};
  (outcomes.data?.rows || []).forEach((o: any) => { outBy[`${o.symbol}|${o.earnings_date}`] = o; });
  const generate = async () => {
    setAuditMsg('Generating…');
    try { const r = await smartMoveBoard.generateAudit(); setAuditMsg(r.ok ? `Audit saved: ${r.verdict}, ${r.pages} pages, hash ${String(r.audit_hash).slice(0, 12)}` : 'Audit failed'); } catch (e: any) { setAuditMsg(String(e.message || e)); }
  };
  const allRows: any[] = d?.rows || [];
  const sectors = Array.from(new Set(allRows.map((r) => r.sector).filter(Boolean))).sort() as string[];
  const dates = Array.from(new Set(allRows.map((r) => r.earnings_date))).sort() as string[];
  const rows: any[] = allRows.filter((r) =>
    (!sector || r.sector === sector) && (!date || r.earnings_date === date) &&
    (!evidenceF || (evidenceF === 'complete' ? r.evaluated && r.points.available === r.points.max && !r.factors.some((f: any) => f.state === 'MISSING')
      : evidenceF === 'incomplete' ? r.evaluated && (r.points.available !== r.points.max || r.factors.some((f: any) => f.state === 'MISSING')) : !r.evaluated)));
  const simRows = rows.filter((r) => r.evidence_mode === 'SIMULATION').length;
  /* Countdown clock: the real clock, or (demo clock only) the server's demo time advanced by the seconds since it was loaded. */
  const [loadedAt, setLoadedAt] = useState(0);
  useEffect(() => { if (d?.as_of) setLoadedAt(Date.now()); }, [d]);
  const clockMs = d?.clock_override ? Date.parse(d.as_of) + (Date.now() - loadedAt) : Date.now();
  const sm = d?.summary;
  const st = d?.scheduled_test;
  const savedOnly = d && d.sources.run_reports === 0 && d.sources.live_rows === 0;

  return (
    <div className="page sm">
      <PageHead
        title="Smart Move: earnings board"
        subtitle={<>Every eligible upcoming earnings event, with the frozen 40-point engine's answer or the honest reason there is none. Saved data only; this page sends no request.</>}
        right={<button className="ghost-btn" onClick={() => board.refresh()}><RefreshCw size={13} /> Refresh</button>}
      />
      <div className="sm-banner" role="note">
        <ShieldAlert size={16} />
        <div>
          <b>Only a valid engine signal is coloured green or red.</b> Missing data is yellow, never neutral; grey NO TRADE means the engine evaluated the stock and decided not to trade. Shadow research, not trading advice, no accuracy claim.
          The board refreshes every 30 seconds, so a BUY (green) or SELL (red) saved by a collection run appears on its own. <Link to="/smart-move">Evidence and datasets</Link> · <Link to="/smart-move/tracking">Tracking</Link>
        </div>
      </div>
      {d?.clock_override && <div className="sm-fresh"><FlaskConical size={14} /> DEMO CLOCK: the board is judging windows as of {fmtET(d.as_of)}, not the real time.</div>}
      {simRows > 0 && <div className="sm-fresh"><FlaskConical size={14} /> {simRows} row(s) come from a SIMULATED run report: invented, not real signals.</div>}
      {savedOnly && <div className="sm-fresh"><Clock size={14} /> No collection run has saved a prediction yet, so every event is PENDING (or DATA INCOMPLETE once its window closed without a capture). Statuses change when a run report or the live store has an in-window capture.</div>}
      {board.error && <div className="sm-fresh stale"><AlertTriangle size={14} /> Could not load the board: {board.error}</div>}

      {sm && (
        <div className="sm-bd-summary" role="region" aria-label="Evaluation summary">
          <div className="sm-bd-sumhead"><b>{sm.evaluated} of {sm.total} stocks have actually been evaluated</b>{sm.evaluated_simulated > 0 && <span className="badge amber"> {sm.evaluated_simulated} simulated</span>} · {sm.evaluated_real} real · {sm.not_evaluated} not evaluated (no capture)</div>
          <div className="sm-bd-sumgrid">
            <span>Complete data <b>{sm.complete_data}</b></span><span>Incomplete data <b>{sm.incomplete_data}</b></span><span>No capture <b>{sm.no_capture}</b></span>
            <span>Window ahead <b>{sm.window_ahead}</b></span><span>Window open, no prediction <b>{sm.window_open_no_prediction}</b></span><span>Window closed, missed <b>{sm.window_closed_missed}</b></span>
          </div>
          {sm.next_window_opens_utc && <div className="sm-bd-next">Next capture window opens in <b>{countdown(Date.parse(sm.next_window_opens_utc) - clockMs)}</b> — {fmtDubai(sm.next_window_opens_utc)} · {fmtNY(sm.next_window_opens_utc)}</div>}
          {st && (
            <div className="sm-bd-sched">
              <b>Scheduled one-time test</b> ({st.task}): planned {fmtDubai(st.planned_start_utc)} · {fmtNY(st.planned_start_utc)}; at most {st.max_provider_attempts} provider attempts and {st.max_stocks} stocks; no recurring collection.
              {' '}State from its run folder: <b>{String(st.state).replace(/_/g, ' ').toLowerCase()}</b>{st.report_saved ? ' — audit report saved; stocks marked evaluated below come from it.' : '. Every other stock is not tested.'}
            </div>
          )}
        </div>
      )}
      <div className="sm-fresh" role="group" aria-label="Model and audit">
        <b>Model</b>
        <button className={`sm-tab ${model === 'V1' ? 'on' : ''}`} onClick={() => setModel('V1')}>V1 · 40 points (frozen)</button>
        <button className={`sm-tab ${model === 'V2' ? 'on' : ''}`} onClick={() => setModel('V2')}>V2 · 100 points (EXPERIMENTAL, unavailable for decisions)</button>
        <button className={`sm-tab ${model === 'V2C' ? 'on' : ''}`} onClick={() => setModel('V2C')}>V2 candidate v2c-1 (RESEARCH ONLY)</button>
        <span style={{ flex: 1 }} />
        <button className="ghost-btn" onClick={generate} title="Builds the audit record and the two-page PDF from saved data; sends no provider request">Generate audit</button>
        {audits.data?.audits?.length > 0 && <a className="ghost-btn" href={`/api/smart-move/audits/${audits.data.audits[audits.data.audits.length - 1].file}.pdf`} target="_blank" rel="noreferrer">Latest audit PDF</a>}
        {auditMsg && <span className="sm-bd-asof">{auditMsg}</span>}
      </div>
      {model === 'V2' && <div className="sm-banner" role="note"><AlertTriangle size={16} /><div>{v2.data?.banner || 'V2 results are not available.'} Config hash {String(v2.data?.config_hash || '').slice(0, 12)}. Missing factors are never turned into signals.</div></div>}
      {model === 'V2C' && <CandPanel cand={cand.data} />}
      <V2Panel v2={v2.data} other={v2c.data} variant={variant} coll={coll.data?.report} v1rows={allRows} />
      <OpsPanel ops={ops.data} error={ops.error} />
      {(d?.removed_events?.length > 0 || d?.rescheduled_events?.length > 0) && (
        <div className="sm-fresh" role="note">
          <AlertTriangle size={14} /> Not counted as current events:
          {d.removed_events.map((x: any) => <span key={x.symbol + x.earnings_date} className="badge amber" style={{ marginLeft: 6 }}>{x.symbol} {x.earnings_date} removed/rescheduled</span>)}
          {d.rescheduled_events.map((x: any) => <span key={x.symbol + x.from} className="badge amber" style={{ marginLeft: 6 }}>{x.symbol} moved from {x.from}</span>)}
          {' '}Predictions already saved for them stay in Tracking history.
        </div>
      )}
      <div className="sm-bd-counts" role="group" aria-label="Filter by status">
        {STATUS_ORDER.map((s) => (
          <button key={s} className={`sm-bd-count ${s.toLowerCase()} ${status === s ? 'on' : ''}`} onClick={() => setStatus(status === s ? '' : s)} aria-pressed={status === s}>
            <span className="n">{d?.counts?.[s] ?? '—'}</span><span className="l">{STATUS_LABEL[s]}</span>
          </button>
        ))}
        <div className="sm-bd-count total"><span className="n">{d?.total ?? '—'}</span><span className="l">ELIGIBLE UPCOMING</span></div>
      </div>

      <div className="sm-filters">
        <div className="sm-search"><Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search symbol or company…" aria-label="Search" />
          {q && <button onClick={() => setQ('')} aria-label="Clear search"><X size={12} /></button>}
        </div>
        <label>Timing
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All</option><option value="BMO">Before open (BMO)</option><option value="AMC">After close (AMC)</option>
          </select></label>
        <label>Colour follows
          <select value={variant} onChange={(e) => setVariant(e.target.value as any)}>
            <option value="continuation">Continuation variant</option><option value="contrarian">Contrarian variant</option>
          </select></label>
        <label>Earnings date
          <select value={date} onChange={(e) => setDate(e.target.value)}><option value="">All</option>{dates.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
        <label>Sector
          <select value={sector} onChange={(e) => setSector(e.target.value)}><option value="">All</option>{sectors.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
        <label>Evidence
          <select value={evidenceF} onChange={(e) => setEvidenceF(e.target.value)}><option value="">Any</option><option value="complete">Complete (40/40)</option><option value="incomplete">Incomplete</option><option value="none">Not evaluated</option></select></label>
        <button className="ghost-btn" onClick={() => { setStatus(''); setKind(''); setQ(''); setSector(''); setDate(''); setEvidenceF(''); }}>Clear</button>
        {d && <span className="sm-bd-asof">As of {fmtET(d.as_of)} · showing {d.shown} of {d.total}</span>}
      </div>

      {board.initialLoading && <div className="sm-none">Loading saved events…</div>}
      {d && !rows.length && <div className="sm-none">No events match these filters.</div>}
      {rows.length > 0 && (
        <div className="sm-table-wrap">
          <table className="sm-table sm-bd-table">
            <thead><tr><th>Stock</th><th>Earnings</th><th>Capture window (Dubai · New York)</th><th>Signal status ({variant})</th><th>Points</th><th>Scoring factors</th><th>Last data update</th><th>Event v.</th><th>V2 (exp.)</th><th>Outcome</th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const key = `${r.symbol}|${r.earnings_date}`;
                const other = variant === 'continuation' ? 'contrarian' : 'continuation';
                const ov = r.variants[other];
                return (
                  <Fragment key={key}>
                    <tr className={`sm-bd-row ${String(r.status).toLowerCase()}`} onClick={() => setOpen(open === key ? '' : key)}>
                      <td><b>{r.symbol}</b>{r.evidence_mode === 'SIMULATION' && <span className="badge amber sm-bd-sim">SIMULATED</span>}<small>{r.company || 'company name not saved'}</small></td>
                      <td>{r.earnings_date}<small>{r.release.label}{r.release.assumed_release_et ? ` · assumed ${fmtET(r.release.assumed_release_et)}` : ''}</small></td>
                      <td className="sm-bd-win">
                        <Countdown row={r} nowMs={clockMs} />
                        <small>opens {fmtDubai(r.window.start_utc)}<br />{fmtNY(r.window.start_utc)}</small>
                        <small>closes {fmtDubai(r.window.cutoff_utc)}<br />{fmtNY(r.window.cutoff_utc)}</small>
                      </td>
                      <td>
                        {model === 'V2C' && candBy[key]
                          ? <><span className={`sm-bd-pill ${candBy[key].display_status === 'DATA_INCOMPLETE' ? 'data_incomplete' : candBy[key].display_status.toLowerCase()}`}><i />{`V2C ${candBy[key].display_status === 'DATA_INCOMPLETE' ? 'DATA INCOMPLETE' : candBy[key].display_status === 'NO_TRADE' ? 'NO TRADE' : candBy[key].display_status} (RESEARCH)`}</span>
                              <small>{candBy[key].primary_cause.replace(/_/g, ' ').toLowerCase()}{candBy[key].secondary_causes.length ? ` + ${candBy[key].secondary_causes.map((c: string) => c.replace(/_/g, ' ').toLowerCase()).join(', ')}` : ''}. {candBy[key].explanation} Available {candBy[key].available_points}/100, directional {candBy[key].directional_available_points}/100. V1: {STATUS_LABEL[r.status as Status]}; frozen V2: {candBy[key].frozen_v2_signal}.</small></>
                          : model === 'V2' && v2By[key]
                          ? <><span className={`sm-bd-pill ${v2By[key].display_status === 'DATA_INCOMPLETE' ? 'data_incomplete' : v2By[key].signal.toLowerCase()}`}><i />{v2By[key].display_status === 'DATA_INCOMPLETE' ? 'V2 DATA INCOMPLETE' : v2By[key].signal === 'NO_TRADE' ? 'V2 NO TRADE' : `V2 ${v2By[key].signal} (EXPERIMENTAL)`}</span>
                              <small>{v2By[key].explanation} V1 says {STATUS_LABEL[r.status as Status]}.</small></>
                          : <><StatusPill status={r.status} /><small>{r.status_why}</small></>}
                        {r.blocker && <small className="sm-bd-blocker">{r.blocker}</small>}
                        <small className="sm-bd-other">{other}: <StatusPill status={ov.status} small /></small>
                      </td>
                      <td>
                        {r.points.available == null
                          ? <span className="sm-none">not scored yet</span>
                          : <>{r.points.available}<span className="sm-bd-of">/{r.points.max}</span>
                              <small>{r.points.bull}↑ bull · {r.points.bear}↓ bear{r.points.available < r.points.min_to_decide ? ` · needs ${r.points.min_to_decide}` : ''}</small></>}
                      </td>
                      <td>
                        <div className="sm-bd-factors">
                          {r.factors.map((f: any) => (
                            <span key={f.factor} className={`sm-bd-factor ${f.state.toLowerCase()}`} title={`${f.name}: ${STATE_LABEL[f.state]}${f.reason ? ` (${f.reason})` : ''}`}>{f.factor}</span>
                          ))}
                        </div>
                      </td>
                      <td>{r.evaluated ? fmtExact(r.last_update) : fmtET(r.last_update)}<small>{fmtAge(r.age_hours)} · {r.evaluation_source.replace(/_/g, ' ').toLowerCase()}</small></td>
                      <td>{(() => { const x = v2By[key]; if (!x) return <span className="sm-none">n/a</span>; return <span title={`V2 ${x.signal}; missing: ${x.missing.map((m: any) => m.factor).join(', ')}`}>{x.bull}↑ {x.bear}↓ <small>{x.available_points}/100 with data ({x.directional_available_points} directional, need 60) · {x.missing_points} missing · {model === 'V2' ? 'EXPERIMENTAL ' : ''}{x.display_status === 'DATA_INCOMPLETE' ? 'data incomplete' : x.signal}</small></span>; })()}</td>
                      <td>{(() => { const o = outBy[key]; return o ? `${o.move_pct > 0 ? '+' : ''}${o.move_pct}%` : <span className="sm-none">pending</span>; })()}</td>
                      <td title={r.event ? `first seen date ${r.event.original_date}; registry observed ${r.event.observed_ts}` : 'not in registry'}>{r.event ? `v${r.event.version}` : '—'}</td>
                    </tr>
                    {open === key && (
                      <tr className="sm-bd-detail"><td colSpan={10}>
                        <div className="sm-bd-grid">
                          <div><h4>Factors</h4>{r.factors.map((f: any) => <div key={f.factor}><b>{f.factor}</b> {f.name}: {STATE_LABEL[f.state]}{f.reason ? ` (${f.reason})` : ''}</div>)}</div>
                          <div><h4>Both variants (scored separately)</h4>
                            {(['continuation', 'contrarian'] as const).map((v) => (
                              <div key={v}>{v}: <StatusPill status={r.variants[v].status} small /> {r.variants[v].signal ? `${r.variants[v].bull}↑ ${r.variants[v].bear}↓` : ''}<small>{r.variants[v].why}</small></div>
                            ))}</div>
                          <div><h4>Capture window</h4>
                            <div>Opens {fmtDubai(r.window.start_utc)} · {fmtNY(r.window.start_utc)}</div><div>Closes {fmtDubai(r.window.cutoff_utc)} · {fmtNY(r.window.cutoff_utc)}</div>{r.evaluated && <div>Captured {fmtExact(r.last_update)}</div>}{r.blocker && <div>{r.blocker}</div>}
                            <div>Calendar: {r.calendar_status || '—'}{r.run ? ` · run ${r.run}` : ''}</div>
                            {r.event && <div>Event version v{r.event.version}, first seen on {r.event.original_date}{r.event.confirmed ? ', vendor confirmed' : ', not confirmed'}</div>}</div>
                          <div><h4>V2 factors (experimental, {variant})</h4>
                            {v2By[key] ? v2By[key].factors.map((f: any) => <div key={f.factor}><b>{f.factor}</b> {f.available ? `${f.bull}↑ ${f.bear}↓ of ${f.max}` : <span className="sm-none">missing, not a signal</span>}</div>) : <div>No V2 result saved for this event.</div>}</div>
                          <div><h4>Evidence (hash, source, retrieval time)</h4>
                            {r.evidence.length ? r.evidence.map((e: any, i: number) => <div key={i} className="sm-hash">{e.kind} {e.symbol} · {fmtExact(e.retrieved_at)} · {String(e.raw_sha256).slice(0, 12)} · {e.mode}</div>) : <div>No evidence saved for this event.</div>}</div>
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
