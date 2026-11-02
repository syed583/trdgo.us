import { Fragment, useState } from 'react';
import { AlertTriangle, FlaskConical, Info, RefreshCw } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveSignals } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './smart-move.css';

type SigFilter = '' | 'BUY' | 'SELL' | 'NO_TRADE';

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' });
}
const nice = (s?: string | null) => (s ? s.replace(/_/g, ' ').toLowerCase() : '—');
const SIG_LABEL: Record<string, string> = { BUY: 'BUY', SELL: 'SELL', NO_TRADE: 'NO TRADE' };
const STATUS_TONE: Record<string, string> = { LIVE: 'green', STALE: 'amber', HISTORICAL: 'gray', UNVERIFIED: 'red', SYNTHETIC: 'red' };

export function SignalBadge({ row }: { row: any }) {
  return <span className={`sm-sig ${String(row.signal).toLowerCase()}`} data-testid="signal">{SIG_LABEL[row.signal] || row.signal}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`sm-q-badge ${STATUS_TONE[status] || 'gray'}`} data-testid="data-status">{status}</span>;
}

/** The latest validated earnings signals. Every figure comes from the API: this page computes no signal, no score and no confidence. Only rows whose data status is LIVE are current; the others are labelled. */
export default function SmartMoveSignalsPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [q, setQ] = useState('');
  const [sig, setSig] = useState<SigFilter>('');
  const [date, setDate] = useState('');
  const [status, setStatus] = useState('');
  const [open, setOpen] = useState('');
  const [limit, setLimit] = useState(100);

  const sum = useApi<any>((s) => smartMoveSignals.summary(s), [], { refreshMs: 60_000 });
  const query = new URLSearchParams({ limit: String(limit), ...(q.trim() ? { q: q.trim() } : {}), ...(sig ? { signal: sig } : {}), ...(date ? { event_date: date } : {}), ...(status ? { data_status: status } : {}) }).toString();
  const rows = useApi<any>((s) => smartMoveSignals.rows(query, s), [query], { refreshMs: 60_000 });
  const reload = () => { sum.refresh(); rows.refresh(); };
  const s = sum.data;
  const list: any[] = rows.data?.rows || [];
  const cov = s?.coverage?.TOTAL;

  return (
    <div className="sm">
      <PageHead title="Live Earnings Signals" subtitle="BUY / SELL / NO TRADE from the existing earnings models, with the evidence behind each row. NO TRADE says whether data was missing, the model was neutral, or models disagreed." />
      <div className="sm-q-banner" role="note">
        <Info size={16} />
        <div><b>Only rows marked LIVE are current.</b> STALE, HISTORICAL, UNVERIFIED and SYNTHETIC rows are shown with their label and are not live signals. No confidence figure is shown because no model has a calibrated probability. This page does not place orders.</div>
      </div>

      {sum.initialLoading && <div className="sm-none" role="status">Loading signals…</div>}
      {sum.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> Signals could not be loaded: {sum.error} <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Retry</button></div>}
      {s && s.status !== 'OK' && (
        <div className={`sm-q-state ${s.status === 'UNREADABLE' ? 'bad' : ''}`} role={s.status === 'UNREADABLE' ? 'alert' : 'status'}>
          <b>{s.status === 'UNREADABLE' ? 'Signal store unreadable' : 'No signals yet'}</b>
          <div>{s.detail}</div>
          <div className="sm-q-hint">Nothing is shown rather than guessing. Signals appear only after a validated, sealed collection batch has been processed.</div>
        </div>
      )}

      {s?.status === 'OK' && (
        <>
          {s.marker_valid === false && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> The batch completion marker could not be verified, so batch-based rows are UNVERIFIED.</div>}
          <div className="sm-summary" data-testid="coverage">
            <div className="sm-card"><div className="sm-card-k">Eligible companies</div><div className="sm-card-v">{cov.eligible_companies}</div></div>
            <div className="sm-card"><div className="sm-card-k">Complete data</div><div className="sm-card-v">{cov.complete_data}</div></div>
            <div className="sm-card"><div className="sm-card-k">Partial data</div><div className="sm-card-v">{cov.partial_data}</div></div>
            <div className="sm-card"><div className="sm-card-k">BUY</div><div className="sm-card-v">{cov.buy}</div></div>
            <div className="sm-card"><div className="sm-card-k">SELL</div><div className="sm-card-v">{cov.sell}</div></div>
            <div className="sm-card"><div className="sm-card-k">NO TRADE</div><div className="sm-card-v">{cov.no_trade}</div></div>
            <div className="sm-card"><div className="sm-card-k">No valid prediction</div><div className="sm-card-v">{cov.no_valid_prediction}</div></div>
            <div className="sm-card"><div className="sm-card-k">Live rows</div><div className="sm-card-v">{cov.live}</div></div>
          </div>
          {Object.keys(s.by_data_status || {}).length > 0 && <div className="sm-q-chips">{Object.entries<number>(s.by_data_status).map(([k, n]) => <span key={k} className={`sm-q-badge ${STATUS_TONE[k] || 'gray'}`}>{k} {n}</span>)}</div>}
          {s.notes?.map((n: string) => <div key={n} className="sm-q-muted">{n}</div>)}

          <div className="sm-filters">
            <input placeholder="Search symbol or company" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search symbol or company" />
            <div className="sm-tabs" role="group" aria-label="Signal filter">
              {(['', 'BUY', 'SELL', 'NO_TRADE'] as SigFilter[]).map((k) => (
                <button key={k || 'all'} className={`sm-tab ${sig === k ? 'on' : ''}`} aria-pressed={sig === k} onClick={() => setSig(k)}>{k ? SIG_LABEL[k] : 'All'}</button>
              ))}
            </div>
            <select value={date} onChange={(e) => setDate(e.target.value)} aria-label="Earnings date"><option value="">Any date</option>{(s.dates || []).map((d: string) => <option key={d} value={d}>{d}</option>)}</select>
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Data status"><option value="">Any status</option>{['LIVE', 'STALE', 'HISTORICAL', 'UNVERIFIED', 'SYNTHETIC'].map((d) => <option key={d} value={d}>{d}</option>)}</select>
            <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Reload</button>
          </div>
          {rows.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {rows.error}</div>}
          {rows.data && !list.length && <div className="sm-none">No signals match these filters.</div>}
          {list.length > 0 && (
            <div className="sm-table-wrap"><table className="sm-table sm-q-table">
              <thead><tr><th>Symbol</th><th>Earnings date</th><th>Session</th><th>Signal</th><th>Confidence</th><th>Model</th><th>Updated</th><th>Data status</th></tr></thead>
              <tbody>{list.map((r) => {
                const k = r.symbol + r.event_date;
                return (
                  <Fragment key={k}>
                    <tr className="sm-q-row" tabIndex={0} onClick={() => setOpen(open === k ? '' : k)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(open === k ? '' : k); }}>
                      <td><b>{r.symbol}</b><div className="sm-q-muted">{r.company || '—'}</div></td>
                      <td>{r.event_date}</td><td>{r.session_label}</td>
                      <td><SignalBadge row={r} />{r.signal === 'NO_TRADE' && <div className="sm-q-muted" data-testid="signal-kind">{nice(r.signal_kind)}</div>}</td>
                      <td data-testid="confidence">{r.confidence == null ? <span className="sm-q-muted" title={r.confidence_note}>not calibrated</span> : r.confidence}</td>
                      <td>{r.models.filter((m: any) => m.primary).map((m: any) => m.name).join(', ') || '—'}<div className="sm-q-muted">{r.consensus.models_valid} of {r.consensus.models_total} valid · {r.consensus.buy} buy · {r.consensus.sell} sell</div></td>
                      <td>{fmtTime(r.freshness.newest_evidence || r.generated_at)}<div className="sm-q-muted">{r.freshness.age_hours != null ? `${r.freshness.age_hours} h old` : 'age unknown'}</div></td>
                      <td><StatusBadge status={r.data_status} />{r.warnings.length > 0 && <div className="sm-q-muted">{r.warnings.length} warning{r.warnings.length > 1 ? 's' : ''}</div>}</td>
                    </tr>
                    {open === k && (
                      <tr><td colSpan={8}>
                        <div data-testid="signal-detail">
                          <div><b>Reason:</b> {r.reason}</div>
                          <div className="sm-q-muted">Generated {fmtTime(r.generated_at)} · batch {r.batch_id || 'none'}{r.synthetic ? ' · SYNTHETIC EXAMPLE, not market data' : ''}</div>
                          {r.warnings.map((w: string) => <div key={w} className="sm-q-muted">⚠ {w}</div>)}
                          <table className="sm-mini"><thead><tr><th>Model</th><th>Output</th><th>Meaning</th><th>Score / points</th><th>Source</th></tr></thead>
                            <tbody>{r.models.map((m: any) => (
                              <tr key={m.name}><td>{m.name}{m.primary ? '' : ' (secondary variant)'}</td><td>{SIG_LABEL[m.signal]}</td><td>{m.kind ? nice(m.kind) : 'signal'}</td>
                                <td>{m.score != null ? m.score : m.points ? `${m.points.bull ?? 0} bull / ${m.points.bear ?? 0} bear of ${m.points.available ?? '—'} available` : '—'}</td><td>{nice(m.source)}</td></tr>
                            ))}</tbody></table>
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}</tbody>
            </table></div>
          )}
          {rows.data && rows.data.total > list.length && <div className="sm-none">Showing {list.length} of {rows.data.total}. <button className="ghost-btn" onClick={() => setLimit(limit + 100)}>Show 100 more</button></div>}
          <div className="sm-fresh"><FlaskConical size={14} /> A 50-attempt collection batch covers only part of the earnings calendar; companies without a row have no signal from this page, not a NO TRADE view.</div>
        </>
      )}
    </div>
  );
}
