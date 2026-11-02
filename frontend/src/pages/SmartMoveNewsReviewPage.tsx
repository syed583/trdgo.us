import { useState } from 'react';
import { AlertTriangle, Info, RefreshCw, ShieldCheck } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveNewsReview } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import { AUDIT_CLASSES, HUMAN_LABELS, canSubmit, classTone, gateText, humanStateText, isDisputed, labelsGiven, myLabel, progressText, reviewability } from './newsReviewLogic';
import './smart-move.css';

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' });
}
const nice = (s?: string | null) => (s ? s.replace(/_/g, ' ').toLowerCase() : '—');

function storedReviewer(): string {
  try { return window.localStorage.getItem('sm-news-reviewer') || ''; } catch { return ''; }
}

/* News review: saved headlines with their AUTOMATED classification on one side and HUMAN labels on the other. Labels are research data only: this screen cannot enable news guidance, change a score,
   or turn a headline into a recommendation. A label is appended to a review file; nothing is edited or deleted. */
export default function SmartMoveNewsReviewPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [cls, setCls] = useState('');
  const [state, setState] = useState('');
  const [q, setQ] = useState('');
  const [reviewer, setReviewer] = useState(storedReviewer());
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [limit, setLimit] = useState(50);

  const query = new URLSearchParams({ limit: String(limit), ...(cls ? { audit_class: cls } : {}), ...(state ? { state } : {}), ...(q.trim() ? { q: q.trim() } : {}) }).toString();
  const data = useApi<any>((s) => smartMoveNewsReview.queue(query, s), [query]);
  const d = data.data;
  const rows: any[] = d?.rows || [];

  const setName = (v: string) => {
    setReviewer(v);
    try { window.localStorage.setItem('sm-news-reviewer', v); } catch { /* storage unavailable: the name simply is not remembered */ }
  };
  const submit = async (item: any, label: string) => {
    const ok = canSubmit(reviewer, label);
    if (!ok.ok) { setErr(ok.reason || 'Cannot submit.'); return; }
    setErr(''); setBusy(item.item_id);
    try {
      const r = await smartMoveNewsReview.label({ item_id: item.item_id, label, reviewer: reviewer.trim() });
      if (r?.status !== 'RECORDED') setErr(`The label was not saved: ${r?.message || r?.status || 'unknown error'}`);
      else data.refresh();
    } catch (e: any) {
      setErr(`The label was not saved: ${e?.message || e}`);
    } finally { setBusy(''); }
  };

  return (
    <div className="sm">
      <PageHead title="News Review" subtitle="Saved headlines for human labelling. Automated classes and human labels are kept apart; nothing here is a trading recommendation." />
      <div className="sm-q-banner" role="note">
        <Info size={16} />
        <div><b>News guidance is DISABLED.</b> Labels are research data for a future validation. An unreviewed headline is never converted into a signal, and a label does not change any score or prediction.</div>
      </div>

      {data.initialLoading && <div className="sm-none" role="status">Loading the review queue…</div>}
      {data.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> The review queue could not be loaded: {data.error} <button className="ghost-btn" onClick={data.refresh}><RefreshCw size={14} /> Retry</button></div>}
      {d && d.status !== 'OK' && (
        <div className={`sm-q-state ${d.status === 'UNREADABLE' ? 'bad' : ''}`} role={d.status === 'UNREADABLE' ? 'alert' : 'status'}>
          <b>{d.status === 'UNREADABLE' ? 'Review queue unreadable' : 'No review queue yet'}</b>
          <div>{d.detail}</div>
          <div className="sm-q-hint">Build the queue offline from saved news, then reload.</div>
        </div>
      )}

      {d?.status === 'OK' && (
        <>
          <div className="sm-summary">
            <div className="sm-card"><div className="sm-card-k">Review progress</div><div className="sm-card-v" data-testid="progress">{d.progress.percent_reviewed ?? 0}%</div><div className="sm-q-muted">{progressText(d.progress)}</div></div>
            <div className="sm-card"><div className="sm-card-k">Validation gate</div><div className="sm-card-v">not met</div><div className="sm-q-muted" data-testid="gate">{gateText(d.gate)}</div></div>
            <div className="sm-card"><div className="sm-card-k">Automated classes (all saved headlines)</div>
              <div className="sm-q-chips">{AUDIT_CLASSES.map((c) => <span key={c} className={`sm-q-badge ${classTone(c)}`}>{c} {d.by_audit_class?.[c] ?? 0}</span>)}</div></div>
          </div>

          <div className="sm-filters">
            <input placeholder="Search headline, ticker or company" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search headlines" />
            <select value={cls} onChange={(e) => setCls(e.target.value)} aria-label="Automated class"><option value="">Any automated class</option>{AUDIT_CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}</select>
            <select value={state} onChange={(e) => setState(e.target.value)} aria-label="Human review state">
              <option value="">Any review state</option><option value="UNLABELLED">Not yet reviewed</option><option value="LABELLED">Labelled</option><option value="DISPUTED">Disputed</option><option value="UNSURE">Unsure</option>
            </select>
            <input placeholder="Your reviewer name" value={reviewer} onChange={(e) => setName(e.target.value)} aria-label="Reviewer name" />
          </div>
          {err && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {err}</div>}
          {!rows.length && <div className="sm-none">No headlines match these filters.</div>}
          {rows.length > 0 && (
            <div className="sm-table-wrap"><table className="sm-table sm-q-table">
              <thead><tr><th>Event</th><th>Headline</th><th>Source &amp; timing</th><th>Automated classification</th><th>Human review</th></tr></thead>
              <tbody>{rows.map((r) => {
                const rv = reviewability(r);
                return (
                  <tr key={r.item_id} className={isDisputed(r) ? 'sm-q-disputed' : ''}>
                    <td><b>{r.symbol}</b><div className="sm-q-muted">{r.company || '—'}</div><div className="sm-q-muted">{r.event_date} · {r.ending_fiscal_quarter && r.ending_fiscal_quarter !== 'QUARTER_UNKNOWN' ? `quarter ${r.ending_fiscal_quarter}` : 'quarter unknown'}</div></td>
                    <td className="sm-q-headline">{r.headline}</td>
                    <td>{r.source || '—'} <span className={`sm-q-badge ${r.source_credibility === 'TRUSTED_WIRE' ? 'green' : r.source_credibility === 'LOW_CREDIBILITY' ? 'red' : 'amber'}`}>{nice(r.source_credibility)}</span>
                      <div className="sm-q-muted">published {fmtTime(r.published)}</div><div className="sm-q-muted">retrieved {fmtTime(r.retrieved_at)}</div>
                      <div className="sm-q-muted">{r.age_hours_at_cutoff != null ? `${r.age_hours_at_cutoff} h before the cutoff` : 'age unknown'}</div></td>
                    <td><span className={`sm-q-badge ${classTone(r.audit_class)}`}>{r.audit_class}</span><div className="sm-q-muted">{nice(r.audit_reason)}</div></td>
                    <td data-testid="human-cell">
                      {!rv.open && <span className="sm-q-muted">{rv.reason}</span>}
                      {rv.open && (
                        <>
                          <div>{humanStateText(r)}{isDisputed(r) && <span className="sm-q-badge red">dispute</span>}</div>
                          {labelsGiven(r).map(([who, lab]) => <div key={who} className="sm-q-muted">{who}: {nice(lab)}</div>)}
                          <div className="sm-q-labelrow">
                            {HUMAN_LABELS.map((l) => (
                              <button key={l} className={`ghost-btn sm-q-lab ${myLabel(r, reviewer) === l ? 'on' : ''}`} disabled={busy === r.item_id} onClick={() => submit(r, l)} aria-pressed={myLabel(r, reviewer) === l}>{nice(l)}</button>
                            ))}
                          </div>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}</tbody>
            </table></div>
          )}
          {d.total > rows.length && <div className="sm-none">Showing {rows.length} of {d.total}. <button className="ghost-btn" onClick={() => setLimit(limit + 50)}>Show 50 more</button></div>}
          <div className="sm-fresh"><ShieldCheck size={14} /> {d.news_guidance.note}</div>
        </>
      )}
    </div>
  );
}
