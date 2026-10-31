import { Fragment, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Clock, FlaskConical, RefreshCw, Search, ShieldAlert, X } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveBoard } from '../api/client';
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

/* Smart Move earnings board: every eligible upcoming earnings event with an honest status.
   Read only, saved data only. The colour is a display of what the frozen engine returned; it never creates a signal. */
export default function SmartMoveBoardPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [variant, setVariant] = useState<'continuation' | 'contrarian'>('continuation');
  const [status, setStatus] = useState<'' | Status>('');
  const [kind, setKind] = useState('');
  const [q, setQ] = useState('');
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
  const rows: any[] = d?.rows || [];
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
        <button className="ghost-btn" onClick={() => { setStatus(''); setKind(''); setQ(''); }}>Clear</button>
        {d && <span className="sm-bd-asof">As of {fmtET(d.as_of)} · showing {d.shown} of {d.total}</span>}
      </div>

      {board.initialLoading && <div className="sm-none">Loading saved events…</div>}
      {d && !rows.length && <div className="sm-none">No events match these filters.</div>}
      {rows.length > 0 && (
        <div className="sm-table-wrap">
          <table className="sm-table sm-bd-table">
            <thead><tr><th>Stock</th><th>Earnings</th><th>Capture window (Dubai · New York)</th><th>Signal status ({variant})</th><th>Points</th><th>Scoring factors</th><th>Last data update</th></tr></thead>
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
                        <StatusPill status={r.status} />
                        <small>{r.status_why}</small>
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
                    </tr>
                    {open === key && (
                      <tr className="sm-bd-detail"><td colSpan={7}>
                        <div className="sm-bd-grid">
                          <div><h4>Factors</h4>{r.factors.map((f: any) => <div key={f.factor}><b>{f.factor}</b> {f.name}: {STATE_LABEL[f.state]}{f.reason ? ` (${f.reason})` : ''}</div>)}</div>
                          <div><h4>Both variants (scored separately)</h4>
                            {(['continuation', 'contrarian'] as const).map((v) => (
                              <div key={v}>{v}: <StatusPill status={r.variants[v].status} small /> {r.variants[v].signal ? `${r.variants[v].bull}↑ ${r.variants[v].bear}↓` : ''}<small>{r.variants[v].why}</small></div>
                            ))}</div>
                          <div><h4>Capture window</h4>
                            <div>Opens {fmtDubai(r.window.start_utc)} · {fmtNY(r.window.start_utc)}</div><div>Closes {fmtDubai(r.window.cutoff_utc)} · {fmtNY(r.window.cutoff_utc)}</div>{r.evaluated && <div>Captured {fmtExact(r.last_update)}</div>}{r.blocker && <div>{r.blocker}</div>}
                            <div>Calendar: {r.calendar_status || '—'}{r.run ? ` · run ${r.run}` : ''}</div></div>
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
