import { useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, FlaskConical, Info, RefreshCw, ShieldCheck, X } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveQuality } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './smart-move.css';

type Tab = 'events' | 'news' | 'outcomes';

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' });
}

const nice = (s?: string | null) => (s ? s.replace(/_/g, ' ').toLowerCase() : '—');

function Badge({ text, tone }: { text: string; tone?: 'green' | 'red' | 'amber' | 'gray' | 'blue' }) {
  return <span className={`sm-q-badge ${tone || 'gray'}`}>{text}</span>;
}

const EVIDENCE_KEYS: [string, string][] = [['calendar', 'Cal'], ['quarter', 'Qtr'], ['snapshot_pair', 'A/B'], ['news', 'News'], ['price_bars', 'Px'], ['prediction', 'Pred'], ['outcome', 'Out']];
const CLASS_TONE: Record<string, 'green' | 'amber' | 'gray' | 'red'> = { VERIFIED: 'green', UNVERIFIED: 'amber', MISSING: 'gray', INELIGIBLE: 'red' };

/** Four-state evidence matrix for one event: verified / unverified / missing / ineligible. Missing is shown as missing, never as a positive or negative. */
function EvidenceMatrix({ ev }: { ev: Record<string, string> | undefined }) {
  if (!ev) return <span className="sm-q-muted">—</span>;
  return (
    <span className="sm-q-matrix" aria-label="Evidence status">
      {EVIDENCE_KEYS.map(([k, label]) => <span key={k} className={`sm-q-cell ${CLASS_TONE[ev[k]] || 'gray'}`} title={`${nice(k)}: ${ev[k].toLowerCase()}`}>{label}</span>)}
    </span>
  );
}

function ageText(iso?: string | null): string {
  if (!iso) return 'unknown age';
  const h = (Date.now() - new Date(iso).getTime()) / 3600_000;
  if (!Number.isFinite(h)) return 'unknown age';
  return h < 1 ? 'under an hour old' : h < 48 ? `${Math.round(h)} h old` : `${Math.round(h / 24)} days old`;
}

function vendorTone(s: string): 'green' | 'amber' | 'red' | 'gray' {
  return s === 'VENDOR_CONFIRMED' || s === 'COMPANY_VERIFIED' ? 'green' : s === 'ESTIMATED' || s === 'MOVED' ? 'amber' : s === 'STALE' ? 'red' : 'gray';
}

/* Smart Move evidence quality: calendar status and eligibility, fiscal-quarter source, Snapshot A/B pairing, news quality, outcome state and the verified-only accuracy report.
   Read only: it shows ONE saved report. A synthetic report is labelled as such and is never presented as live market data. */
export default function SmartMoveQualityPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [tab, setTab] = useState<Tab>('events');
  const [q, setQ] = useState('');
  const [quarter, setQuarter] = useState('');
  const [elig, setElig] = useState('');
  const [pairing, setPairing] = useState('');
  const [missing, setMissing] = useState('');
  const [limit, setLimit] = useState(100);
  const [open, setOpen] = useState<any | null>(null);

  const sum = useApi<any>((s) => smartMoveQuality.summary(s), []);
  const query = new URLSearchParams({ limit: String(limit), ...(q.trim() ? { q: q.trim() } : {}), ...(quarter ? { quarter_state: quarter } : {}), ...(elig ? { eligible: elig } : {}),
    ...(pairing ? { pairing } : {}), ...(missing ? { missing } : {}) }).toString();
  const evs = useApi<any>((s) => smartMoveQuality.events(query, s), [query]);

  const reload = () => { sum.refresh(); evs.refresh(); };
  const s = sum.data;
  const synthetic = !!s?.synthetic;
  const rows: any[] = evs.data?.rows || [];

  return (
    <div className="sm">
      <PageHead title="Smart Move Evidence Quality" subtitle="How complete and how trustworthy the saved evidence is for each earnings event. Predictions and verified outcomes are kept apart." />

      {sum.initialLoading && <div className="sm-none" role="status">Loading the evidence-quality report…</div>}
      {sum.error && (
        <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> The report could not be loaded: {sum.error} <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Retry</button></div>
      )}
      {s && s.status !== 'OK' && (
        <div className={`sm-q-state ${s.status === 'UNREADABLE' ? 'bad' : ''}`} role={s.status === 'UNREADABLE' ? 'alert' : 'status'}>
          <b>{s.status === 'UNREADABLE' ? 'Report unreadable' : 'No evidence-quality report yet'}</b>
          <div>{s.detail}</div>
          <div className="sm-q-hint">Nothing is shown rather than guessing. Build the report offline from saved evidence, then reload.</div>
        </div>
      )}

      {s?.status === 'OK' && (
        <>
          <div className={`sm-q-banner ${synthetic ? 'synthetic' : ''}`} role="note">
            {synthetic ? <FlaskConical size={16} /> : <Info size={16} />}
            <div>
              <b>{synthetic ? 'SYNTHETIC EXAMPLE DATA, not market data.' : 'Saved evidence, not live signals.'}</b>{' '}
              {synthetic ? 'Every symbol, price and headline here is invented to test the screens.' : 'This page describes what was saved; it makes no prediction and no accuracy claim.'}
              {' '}<span className="sm-q-muted">Report as of {fmtTime(s.as_of)} · built {fmtTime(s.generated_at)} · source {nice(s.source)}</span>
            </div>
          </div>

          {!synthetic && s.generated_at && (Date.now() - new Date(s.generated_at).getTime()) > 24 * 3600_000 && (
            <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> This report was built {ageText(s.generated_at)}; evidence saved since then is not included. Rebuild it offline before relying on it.</div>
          )}
          <div className="sm-q-legend" role="note">
            <b>Evidence states:</b> <Badge text="verified" tone="green" /> present and passes its point-in-time test · <Badge text="unverified" tone="amber" /> present but not provable · <Badge text="missing" tone="gray" /> not collected or not yet available ·{' '}
            <Badge text="ineligible" tone="red" /> collected but unusable. <b>Missing or unverified evidence is never a positive or negative signal.</b>
          </div>
          <div className="sm-summary">
            <div className="sm-card"><div className="sm-card-k">Events</div><div className="sm-card-v">{s.summary.events}</div></div>
            <div className="sm-card"><div className="sm-card-k">Prediction-eligible</div><div className="sm-card-v">{s.summary.prediction_ready}</div></div>
            <div className="sm-card"><div className="sm-card-k">Pairing-eligible (quarter known)</div><div className="sm-card-v">{s.summary.pairing_ready}</div></div>
            <div className="sm-card"><div className="sm-card-k">Evidence complete</div><div className="sm-card-v">{s.summary.complete}<small> of {s.summary.events}</small></div></div>
            <div className="sm-card"><div className="sm-card-k">Admissible predictions</div><div className="sm-card-v">{s.summary.predictions.admissible}<small> of {s.summary.predictions.total}</small></div></div>
            <div className="sm-card"><div className="sm-card-k">Verified outcomes</div><div className="sm-card-v">{s.accuracy.outcomes_by_verification?.VERIFIED ?? 0}</div></div>
            <div className="sm-card"><div className="sm-card-k">Accuracy claim</div><div className="sm-card-v">{s.accuracy.claim_allowed ? 'allowed' : 'not allowed'}</div></div>
          </div>

          <div className="sm-tabs" role="tablist">
            {([['events', 'Events & eligibility'], ['news', 'News quality'], ['outcomes', 'Outcomes & accuracy']] as [Tab, string][]).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={`sm-tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>{label}</button>
            ))}
            <span style={{ flex: 1 }} />
            <button className="ghost-btn" onClick={reload}><RefreshCw size={14} /> Reload</button>
          </div>

          {tab === 'events' && (
            <>
              <div className="sm-filters">
                <input placeholder="Search symbol or company" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search symbol or company" />
                <select value={quarter} onChange={(e) => setQuarter(e.target.value)} aria-label="Fiscal quarter state">
                  <option value="">Any quarter state</option><option value="KNOWN">Quarter known</option><option value="QUARTER_UNKNOWN">Quarter unknown</option><option value="AMBIGUOUS">Quarter ambiguous</option>
                </select>
                <select value={elig} onChange={(e) => setElig(e.target.value)} aria-label="Prediction eligibility">
                  <option value="">Any eligibility</option><option value="true">Prediction-eligible</option><option value="false">Not eligible</option>
                </select>
                <select value={pairing} onChange={(e) => setPairing(e.target.value)} aria-label="Snapshot pairing">
                  <option value="">Any pairing</option><option value="PAIRED">Paired</option><option value="UNPAIRED">Unpaired</option><option value="NOT_PAIRABLE">Not pairable</option>
                </select>
                <select value={missing} onChange={(e) => setMissing(e.target.value)} aria-label="Missing evidence code">
                  <option value="">Any missing data</option>
                  {['QUARTER_UNKNOWN', 'SNAPSHOT_PAIR_MISSING', 'NEWS_NOT_COLLECTED', 'NEWS_EMPTY_RESPONSE', 'NEWS_NO_VALID_HEADLINE', 'PRICE_BARS_NOT_SAVED', 'FLOW_NOT_COLLECTED', 'CALENDAR_NOT_CONFIRMED', 'TIMING_UNKNOWN'].map((c) => <option key={c} value={c}>{nice(c)}</option>)}
                </select>
                <button className="ghost-btn" onClick={() => { setQ(''); setQuarter(''); setElig(''); setPairing(''); setMissing(''); }}>Clear</button>
              </div>
              {evs.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {evs.error}</div>}
              {evs.initialLoading && <div className="sm-none" role="status">Loading events…</div>}
              {evs.data && !rows.length && <div className="sm-none">No events match these filters.</div>}
              {rows.length > 0 && (
                <div className="sm-table-wrap"><table className="sm-table sm-q-table">
                  <thead><tr><th>Symbol</th><th>Earnings</th><th>Calendar</th><th>Eligible</th><th>Fiscal quarter</th><th>Pairing</th><th>News</th><th>Outcome</th><th>Evidence</th></tr></thead>
                  <tbody>{rows.map((e) => (
                    <tr key={e.event_id + e.version} onClick={() => setOpen(e)} className="sm-q-row" tabIndex={0} onKeyDown={(k) => { if (k.key === 'Enter') setOpen(e); }}>
                      <td><b>{e.symbol}</b><div className="sm-q-muted">{e.company || '—'}</div></td>
                      <td>{e.event_date || '—'} <Badge text={e.session} tone={e.session === 'UNKNOWN' ? 'gray' : 'blue'} />
                        <div className="sm-q-muted">release {e.release_time_verified ? 'verified' : 'nominal'}</div></td>
                      <td><Badge text={nice(e.vendor_status)} tone={vendorTone(e.vendor_status)} /><div className="sm-q-muted">seen {fmtTime(e.observed_at)}</div></td>
                      <td>{e.prediction_eligible ? <Badge text="eligible" tone="green" /> : <Badge text="not eligible" tone="red" />}</td>
                      <td>{e.quarter_state === 'KNOWN' ? <>{e.ending_fiscal_quarter}<div className="sm-q-muted">source saved {fmtTime(e.quarter_retrieved_at)}</div></> : <Badge text={nice(e.quarter_state)} tone={e.quarter_state === 'AMBIGUOUS' ? 'red' : 'amber'} />}</td>
                      <td><Badge text={nice(e.pairing_state)} tone={e.pairing_state === 'PAIRED' ? 'green' : e.pairing_state === 'UNPAIRED' ? 'amber' : 'gray'} /></td>
                      <td><Badge text={nice(e.news_state)} tone={e.news_state === 'HAS_VALID_HEADLINES' ? 'green' : 'amber'} /></td>
                      <td>{e.outcome_state === 'OUTCOME_COMPUTABLE' ? <><Badge text={`${e.outcome_class} · ${e.outcome_timing_basis === 'EVENT_RECORD' ? 'verified' : 'unverified'}`} tone={e.outcome_timing_basis === 'EVENT_RECORD' ? 'green' : 'amber'} /></> : <Badge text={nice(e.outcome_state)} tone="gray" />}</td>
                      <td><EvidenceMatrix ev={e.evidence} /></td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
              {evs.data && evs.data.total > rows.length && (
                <div className="sm-none">Showing {rows.length} of {evs.data.total}. <button className="ghost-btn" onClick={() => setLimit(limit + 100)}>Show 100 more</button></div>
              )}
            </>
          )}

          {tab === 'news' && <NewsPanel s={s} />}

          {tab === 'outcomes' && <OutcomesPanel s={s} />}
        </>
      )}

      {open && <EventDetail open={open} synthetic={synthetic} onClose={() => setOpen(null)} />}
    </div>
  );
}


/** The news tab: automated headline classes, per-event news states and the (disabled) guidance notice. */
export function NewsPanel({ s }: { s: any }) {
  return (
<>
              <h3 className="sm-h">Headline classes (all saved news)</h3>
              <div className="sm-summary">
                {['VALID', 'STALE', 'DUPLICATE', 'IRRELEVANT', 'UNVERIFIED', 'INVALID'].map((k) => (
                  <div className="sm-card" key={k}><div className="sm-card-k">{k}</div><div className="sm-card-v">{s.summary.news.by_class?.[k] ?? 0}</div></div>
                ))}
              </div>
              <h3 className="sm-h">Events by news state</h3>
              <div className="sm-q-chips">
                {Object.entries<number>(s.summary.news.by_news_state || {}).map(([k, n]) => <Badge key={k} text={`${nice(k)}: ${n}`} tone={k === 'HAS_VALID_HEADLINES' ? 'green' : 'amber'} />)}
              </div>
              <div className="sm-fresh"><Link to="/smart-move/news-review">Open the headline review screen</Link> to label saved headlines. Automated classes and human labels are shown separately.</div>
              <div className="sm-fresh"><ShieldCheck size={14} /> News guidance scoring stays <b>disabled</b>: no independently labelled sample exists. The provider's own sentiment label is not used.</div>
              {!Object.keys(s.summary.news.by_news_state || {}).length && <div className="sm-none">No news was audited.</div>}
            </>
  );
}

/** The outcomes tab: verified vs unverified outcomes, the accuracy table with sample sizes, and why predictions did not count. Every number comes from the report; nothing is computed or defaulted here. */
export function OutcomesPanel({ s }: { s: any }) {
  return (
<>
              <div className="sm-fresh"><Info size={14} /> {s.accuracy.claim_text} Flat band ±{s.accuracy.flat_band_pct}% (inclusive).</div>
              <h3 className="sm-h">Outcomes: verified and unverified are kept apart</h3>
              <div className="sm-q-chips">
                {Object.entries<number>(s.accuracy.outcomes_by_verification || {}).map(([k, n]) => <Badge key={k} text={`${nice(k)}: ${n}`} tone={k === 'VERIFIED' ? 'green' : 'amber'} />)}
                {!Object.keys(s.accuracy.outcomes_by_verification || {}).length && <span className="sm-none">No outcome rows.</span>}
              </div>
              <h3 className="sm-h">Prediction versus verified outcome (sample sizes shown)</h3>
              {!Object.keys(s.accuracy.variants || {}).length ? (
                <div className="sm-none">No comparable pairs yet: a prediction made before its cutoff and a verified outcome are both required.</div>
              ) : (
                <div className="sm-table-wrap"><table className="sm-table">
                  <thead><tr><th>Variant</th><th>Compared events</th><th>Decided (BUY/SELL)</th><th>No trade</th><th>Strict hits</th><th>Hit rate</th><th>95% interval</th><th>Minimum sample</th></tr></thead>
                  <tbody>{Object.entries<any>(s.accuracy.variants).map(([v, x]) => (
                    <tr key={v}><td>{v}</td><td>{x.compared_events}</td><td>{x.decided_n}</td><td>{x.abstained_no_trade_n}</td><td>{x.strict_hits}</td><td>{x.strict_hit_rate ?? '—'}</td>
                      <td>{x.strict_hit_ci95 ? `${x.strict_hit_ci95[0]} to ${x.strict_hit_ci95[1]}` : '—'}</td><td>{x.minimum_sample_met ? 'met' : `not met (${x.decided_n} of ${x.minimum_decided_required}, ${x.earnings_weeks} of ${x.minimum_weeks_required} weeks)`}</td></tr>
                  ))}</tbody>
                </table></div>
              )}
              <h3 className="sm-h">Why predictions did not count</h3>
              {Object.keys(s.accuracy.exclusions || {}).length ? (
                <ul className="sm-q-list">{Object.entries<number>(s.accuracy.exclusions).map(([k, n]) => <li key={k}><b>{n}</b> {nice(k)}</li>)}</ul>
              ) : <div className="sm-none">Nothing excluded.</div>}
            </>
  );
}

/** The event detail drawer. */
export function EventDetail({ open, synthetic, onClose }: { open: any; synthetic: boolean; onClose: () => void }) {
  return (
<aside className="sm-q-drawer" role="dialog" aria-label={`${open.symbol} evidence detail`}>
          <button className="ghost-btn sm-q-close" onClick={onClose} aria-label="Close"><X size={14} /></button>
          <h3>{open.symbol} <small>{open.company}</small></h3>
          <div className="sm-q-muted">{synthetic ? 'SYNTHETIC EXAMPLE, not market data' : 'Saved evidence, not a prediction'} · event {open.event_id} · version {open.version}</div>
          <dl className="sm-q-dl">
            <dt>Earnings</dt><dd>{open.event_date} {open.session} · cutoff {fmtTime(open.cutoff_utc)} · release time {open.release_time_verified ? 'verified' : 'NOMINAL (not verified)'} ({nice(open.release_basis)})</dd>
            <dt>Calendar</dt><dd>{nice(open.vendor_status)} · observed {fmtTime(open.observed_at)}<br />{(open.vendor_reasons || []).map(nice).join(', ') || '—'}</dd>
            <dt>Eligibility</dt><dd>{open.prediction_eligible ? 'prediction-eligible' : `not eligible: ${(open.ineligible_reasons || []).map(nice).join(', ')}`}<br />pairing {open.analyst_pairing_eligible ? 'eligible' : 'not eligible'}</dd>
            <dt>Fiscal quarter</dt><dd>{open.quarter_state === 'KNOWN' ? `period ending ${open.ending_fiscal_quarter}, from provider field ending_fiscal_quarter, payload ${open.quarter_payload_sha256}, saved ${fmtTime(open.quarter_retrieved_at)}, ${open.quarter_known_before_cutoff ? 'known before the cutoff' : 'learned AFTER the cutoff'}` : `${nice(open.quarter_state)} (${nice(open.quarter_reason)})`}</dd>
            <dt>Capture status</dt><dd>{nice(open.capture_status)}{open.capture_window?.opens ? ` · window ${fmtTime(open.capture_window.opens)} to ${fmtTime(open.capture_window.cutoff)}` : ''}</dd>
            <dt>Snapshot A/B</dt><dd>{nice(open.pairing_state)}{open.pairing_reasons?.length ? `: ${open.pairing_reasons.map(nice).join(', ')}` : ''}
              {open.snapshot_a && open.snapshot_b ? (
                <div className="sm-q-revision" data-testid="revision-evidence">
                  A {fmtTime(open.snapshot_a.retrieved_at)}: EPS {open.snapshot_a.eps ?? '—'} · revenue {open.snapshot_a.revenue ?? '—'}<br />
                  B {fmtTime(open.snapshot_b.retrieved_at)}: EPS {open.snapshot_b.eps ?? '—'} · revenue {open.snapshot_b.revenue ?? '—'}<br />
                  {open.snapshot_gap_days} days apart · EPS revision {open.eps_revision_pct ?? '—'}% · revenue revision {open.revenue_revision_pct ?? '—'}% (provider values as saved)
                </div>
              ) : <div className="sm-q-muted" data-testid="revision-unavailable">No A/B revision evidence: Snapshot A and B are not both available for this event's quarter.</div>}</dd>
            <dt>News</dt><dd>{nice(open.news_state)} · {open.news_valid} valid headline(s){open.news_freshest_valid_age_hours != null ? ` · freshest valid ${open.news_freshest_valid_age_hours} h before the cutoff` : ''}
              {open.news_warnings?.length ? ` · warnings: ${open.news_warnings.map(nice).join(', ')}` : ''}
              {Object.keys(open.news_counts || {}).length > 0 && <div className="sm-q-muted">{Object.entries<number>(open.news_counts).map(([k, n]) => `${k.toLowerCase()} ${n}`).join(' · ')}</div>}</dd>
            <dt>Human review</dt><dd>{open.news_review ? `${open.news_review.labelled} labelled · ${open.news_review.disputed} disputed · ${open.news_review.unlabelled} not yet reviewed of ${open.news_review.reviewable}` : 'no reviewable headlines for this event'}</dd>
            <dt>Prediction</dt><dd>{open.has_admissible_prediction ? 'an admissible prediction exists (captured inside the window, before the cutoff)' : nice(open.prediction_validity)}</dd>
            <dt>Outcome</dt><dd>{open.outcome_state === 'OUTCOME_COMPUTABLE' ? `${open.outcome_class}, timing from ${open.outcome_timing_basis === 'EVENT_RECORD' ? 'the event record (verified)' : 'a vendor label only (UNVERIFIED)'}` : nice(open.outcome_state)}</dd>
            <dt>Evidence</dt><dd><EvidenceMatrix ev={open.evidence} /><div className="sm-q-muted">{open.signal_use}</div></dd>
            <dt>Missing</dt><dd>{open.missing.length ? open.missing.map(nice).join(', ') : 'nothing'}{open.warnings.length ? ` · warnings: ${open.warnings.map(nice).join(', ')}` : ''}</dd>
          </dl>
        </aside>
  );
}
