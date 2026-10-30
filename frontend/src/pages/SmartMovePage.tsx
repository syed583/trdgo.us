import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, Clock, FlaskConical, Info, RefreshCw, Search, ShieldAlert, X } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveApi } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './smart-move.css';

type Dataset = 'calendar' | 'replay' | 'synthetic';

const DATASETS: { id: Dataset; label: string; kind: string }[] = [
  { id: 'calendar', label: 'Current calendar', kind: 'CURRENT_CALENDAR' },
  { id: 'replay', label: 'Historical replay', kind: 'HISTORICAL_REPLAY' },
  { id: 'synthetic', label: 'Synthetic examples', kind: 'SYNTHETIC' },
];

const SIGNAL_LABEL: Record<string, string> = { BUY: 'BUY', SELL: 'SELL', NO_TRADE: 'NO TRADE' };
const FACTORS = ['F3', 'F5', 'F7', 'F8'];
const STATUS_TONE: Record<string, string> = {
  VENDOR_CONFIRMED: 'green', COMPANY_VERIFIED: 'green', ESTIMATED: 'amber', MOVED: 'amber',
  STALE: 'red', UNKNOWN: 'gray', HINDSIGHT_NOT_VERIFIABLE: 'gray',
};

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' });
}

function Pill({ signal }: { signal: string }) {
  return <span className={`sm-sig ${signal.toLowerCase()}`}>{SIGNAL_LABEL[signal] || signal}</span>;
}

function Status({ s }: { s?: string | null }) {
  if (!s) return <span className="badge gray">—</span>;
  return <span className={`badge ${STATUS_TONE[s] || 'gray'}`}>{s.replace(/_/g, ' ')}</span>;
}

function Variant({ v }: { v: any }) {
  return (
    <div className="sm-var" title={(v.reason_codes || []).join(', ')}>
      <Pill signal={v.signal} />
      <span className="sm-pts">{v.bull}↑ {v.bear}↓</span>
    </div>
  );
}

function Missing({ list }: { list: any[] }) {
  if (!list?.length) return <span className="sm-none">none</span>;
  return (
    <div className="sm-chips">
      {list.map((m, i) => (
        <span key={i} className={`sm-chip ${m.partial ? 'partial' : ''}`} title={`${m.name}: ${m.reason}`}>
          {m.factor}{m.partial ? '·sector' : ''}
        </span>
      ))}
    </div>
  );
}

function State({ icon, title, children, action }: { icon: React.ReactNode; title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="sm-state">
      <div className="sm-state-icon">{icon}</div>
      <div className="sm-state-title">{title}</div>
      {children && <div className="sm-state-body">{children}</div>}
      {action}
    </div>
  );
}

export default function SmartMovePage({ ctx }: { ctx: PageContext }) {
  const [sp, setSp] = useSearchParams();
  const dataset = (sp.get('set') as Dataset) || 'calendar';
  const selected = sp.get('symbol') || '';
  const experiment = sp.get('exp') || 'primary';
  const setExperiment = (v: string) => pick('exp', v === 'primary' ? '' : v);
  const [filters, setFilters] = useState({ date_from: '', date_to: '', signal: '', variant: 'any', status: '', missing: '', kind: '', q: '' });
  const set = (k: string, v: string) => setFilters((f) => ({ ...f, [k]: v }));
  const clear = () => setFilters({ date_from: '', date_to: '', signal: '', variant: 'any', status: '', missing: '', kind: '', q: '' });
  const pick = (name: string, value: string) => {
    const next = new URLSearchParams(sp);
    if (value) next.set(name, value); else next.delete(name);
    setSp(next, { replace: true });
  };

  const meta = useApi<any>((s) => smartMoveApi.meta(s), []);
  const query = useMemo(() => {
    const p = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => { if (v && !(k === 'variant' && v === 'any')) p.set(k, v); });
    if (filters.variant !== 'any') p.set('variant', filters.variant);
    p.set('limit', '1000');
    if (dataset === 'replay') p.set('experiment', experiment);
    return p.toString();
  }, [filters, dataset, experiment]);

  const list = useApi<any>(
    (s) => (dataset === 'calendar' ? smartMoveApi.calendar(query, s) : dataset === 'replay' ? smartMoveApi.replay(query, s) : smartMoveApi.synthetic(query, s)),
    [dataset, query],
  );
  const detail = useApi<any>((s) => (selected ? smartMoveApi.stock(selected, s) : Promise.resolve(null)), [selected]);

  const d = list.data;
  const rows: any[] = d?.rows || [];
  const m = meta.data;
  const stale = !!m?.current_calendar?.stale;
  const expMeta = m?.experiments?.[experiment];

  return (
    <div className="page sm">
      <PageHead
        title="Smart Move"
        subtitle={<>Shadow-only 40-point BUY / SELL / NO TRADE engine, pre-earnings. Continuation and contrarian variants are scored separately.
          Everything here is produced from <b>saved evidence</b>.</>}
        right={<button className="ghost-btn" onClick={() => { list.refresh(); meta.refresh(); }}><RefreshCw size={13} /> Refresh</button>}
      />

      <div className="sm-banner" role="note">
        <ShieldAlert size={16} />
        <div>
          <b>Not live signals.</b> This page shows what the frozen engine produced from saved evidence. It makes no prediction about the market and no accuracy claim.
          {m?.engine?.prereg_sha256 && <span className="sm-hash"> Engine pre-registration {m.engine.prereg_sha256.slice(0, 12)}…</span>}
        </div>
      </div>

      {meta.error && (
        <State icon={<AlertTriangle size={20} />} title="Smart Move API unreachable" action={<button className="ghost-btn" onClick={meta.refresh}>Retry</button>}>
          {meta.error}. Start the local server (see the instructions in docs/smart_move/app/README.md).
        </State>
      )}

      {m && (
        <div className={`sm-fresh ${stale ? 'stale' : ''}`}>
          <Clock size={14} />
          <span>Current-calendar evidence saved {fmtTime(m.current_calendar.last_retrieved_utc)} — <b>{m.current_calendar.evidence_age_hours} h old</b>
            {stale ? ' · STALE: older than the 24 h calendar limit, so these dates and statuses must be refreshed before any use.' : ' · inside the freshness limit.'}</span>
        </div>
      )}

      <div className="sm-tabs" role="tablist">
        {DATASETS.map((t) => (
          <button key={t.id} role="tab" aria-selected={dataset === t.id} className={`sm-tab ${dataset === t.id ? 'on' : ''}`}
            onClick={() => { pick('set', t.id === 'calendar' ? '' : t.id); pick('symbol', ''); }}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="sm-label"><Info size={13} /> {d?.label || m?.datasets?.[DATASETS.find((x) => x.id === dataset)!.kind]?.label}</div>

      {dataset === 'replay' && (
        <div className="sm-exp">
          <label htmlFor="sm-exp">Experiment</label>
          <select id="sm-exp" value={experiment} onChange={(e) => setExperiment(e.target.value)}>
            {Object.entries(m?.experiments || { primary: { label: 'Primary' } }).map(([id, x]: any) => (
              <option key={id} value={id}>{x.primary ? '★ ' : ''}{x.label}</option>
            ))}
          </select>
          {expMeta?.primary && <span className="badge green">PRIMARY · pre-registered source</span>}
          {expMeta?.exploratory && <span className="badge amber"><FlaskConical size={11} /> EXPLORATORY</span>}
          {expMeta?.f7 && <span className="badge red">F7 UNVERIFIED</span>}
        </div>
      )}

      <div className="sm-filters">
        <div className="sm-search"><Search size={13} />
          <input value={filters.q} onChange={(e) => set('q', e.target.value)} placeholder="Search symbol or company…" aria-label="Search" />
          {filters.q && <button onClick={() => set('q', '')} aria-label="Clear search"><X size={12} /></button>}
        </div>
        <label>From <input type="date" value={filters.date_from} onChange={(e) => set('date_from', e.target.value)} /></label>
        <label>To <input type="date" value={filters.date_to} onChange={(e) => set('date_to', e.target.value)} /></label>
        <label>Timing
          <select value={filters.kind} onChange={(e) => set('kind', e.target.value)}>
            <option value="">All</option><option value="BMO">BMO</option><option value="AMC">AMC</option><option value="UNKNOWN">Unknown</option>
          </select></label>
        <label>Signal
          <select value={filters.signal} onChange={(e) => set('signal', e.target.value)}>
            <option value="">All</option><option value="BUY">BUY</option><option value="SELL">SELL</option><option value="NO_TRADE">NO TRADE</option>
          </select></label>
        <label>Variant
          <select value={filters.variant} onChange={(e) => set('variant', e.target.value)}>
            <option value="any">Either</option><option value="continuation">Continuation</option><option value="contrarian">Contrarian</option>
          </select></label>
        {dataset === 'calendar' && (
          <label>Calendar status
            <select value={filters.status} onChange={(e) => set('status', e.target.value)}>
              <option value="">All</option><option value="VENDOR_CONFIRMED">Vendor confirmed</option><option value="ESTIMATED">Estimated</option>
              <option value="STALE">Stale now</option><option value="UNKNOWN">Unknown</option>
            </select></label>
        )}
        <label>Missing data
          <select value={filters.missing} onChange={(e) => set('missing', e.target.value)}>
            <option value="">Any</option>{FACTORS.map((f) => <option key={f} value={f}>{f} missing</option>)}
          </select></label>
        <button className="ghost-btn" onClick={clear}>Clear</button>
      </div>

      {list.initialLoading && <State icon={<RefreshCw size={20} className="spin" />} title="Loading saved evidence…" />}
      {list.error && !list.initialLoading && (
        <State icon={<AlertTriangle size={20} />} title="Could not load this dataset" action={<button className="ghost-btn" onClick={list.refresh}>Retry</button>}>{list.error}</State>
      )}

      {d && !list.error && (
        <>
          <div className="sm-summary">
            <div className="sm-card"><div className="sm-card-k">Events shown</div><div className="sm-card-v">{d.counts.filtered}<small> of {d.counts.total}</small></div></div>
            {(['continuation', 'contrarian'] as const).map((v) => (
              <div className="sm-card" key={v}>
                <div className="sm-card-k">{v === 'continuation' ? 'Continuation' : 'Contrarian'}</div>
                <div className="sm-card-split">
                  <span className="buy">{d.summary[v].BUY} BUY</span><span className="sell">{d.summary[v].SELL} SELL</span><span className="nt">{d.summary[v].NO_TRADE} NO TRADE</span>
                </div>
              </div>
            ))}
            <div className="sm-card"><div className="sm-card-k">Available points (of 40)</div>
              <div className="sm-card-small">{Object.entries(d.summary.available_points_distribution).map(([k, n]: any) => `${k}: ${n}`).join(' · ') || '—'}</div></div>
            <div className="sm-card"><div className="sm-card-k">Top reason codes</div>
              <div className="sm-card-small">{Object.entries(d.summary.reason_code_counts_per_variant).slice(0, 4).map(([k, n]: any) => `${k} ${n}`).join(' · ') || '—'}</div></div>
          </div>

          {rows.length === 0 ? (
            <State icon={<Search size={20} />} title="No events match these filters" action={<button className="ghost-btn" onClick={clear}>Clear filters</button>}>
              The saved dataset has {d.counts.total} events; none satisfy the current filters.
            </State>
          ) : (
            <div className="sm-table-wrap">
              <table className="sm-table">
                <thead>
                  <tr>
                    <th>Symbol</th><th>Company</th><th>Earnings</th><th>Time</th><th>Calendar status</th><th>Points /40</th>
                    <th>Continuation</th><th>Contrarian</th><th>Missing evidence</th><th>Source · observed</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={`${r.symbol}-${r.earnings_date}-${i}`} className={selected === r.symbol ? 'sel' : ''} onClick={() => pick('symbol', r.symbol)} tabIndex={0}
                      onKeyDown={(e) => { if (e.key === 'Enter') pick('symbol', r.symbol); }}>
                      <td className="sm-sym">{r.symbol}</td>
                      <td className="sm-co" title={r.sector_note}>{r.company || <span className="sm-none">—</span>}{r.sector && <small>{r.sector}</small>}</td>
                      <td>{r.earnings_date || '—'}</td>
                      <td><span className={`sm-kind ${r.kind}`}>{r.kind === 'UNKNOWN' ? '?' : r.kind}</span></td>
                      <td>
                        <Status s={r.calendar.status_at_observation} />
                        {r.calendar.status_now && r.calendar.status_now !== r.calendar.status_at_observation && <div className="sm-now">now: <Status s={r.calendar.status_now} /></div>}
                      </td>
                      <td className="sm-avail"><b>{r.available_points}</b>/40</td>
                      <td><Variant v={r.engine.continuation} /></td>
                      <td><Variant v={r.engine.contrarian} /></td>
                      <td><Missing list={r.missing_factors} /></td>
                      <td className="sm-src">
                        {r.dataset === 'CURRENT_CALENDAR' ? <>{r.calendar.vendors.map((x: string) => x.replace('_', ' ')).join(' + ')}<small>{fmtTime(r.calendar.calendar_observed_at)}</small></>
                          : r.dataset === 'HISTORICAL_REPLAY' ? <>saved candles<small>capture {fmtTime(r.observed_at)}</small></> : <>synthetic<small>invented</small></>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {selected && (
        <aside className="sm-detail" aria-label={`${selected} detail`}>
          <div className="sm-detail-head">
            <h2>{selected}</h2>
            <button className="ghost-btn" onClick={() => pick('symbol', '')} aria-label="Close detail"><X size={14} /></button>
          </div>
          {detail.initialLoading && <State icon={<RefreshCw size={18} className="spin" />} title="Loading detail…" />}
          {detail.error && <State icon={<AlertTriangle size={18} />} title="No saved data for this symbol">{detail.error}</State>}
          {detail.data && <Detail data={detail.data} />}
        </aside>
      )}
    </div>
  );
}

function Detail({ data }: { data: any }) {
  const cur = data.current_calendar?.[0];
  return (
    <div className="sm-detail-body">
      {cur ? (
        <section>
          <h3>Current calendar observation <span className="badge gray">CURRENT CALENDAR · not a prediction</span></h3>
          <div className="sm-kv">
            <div><span>Company</span><b>{cur.company || '—'}</b></div>
            <div><span>Sector</span><b>{cur.sector || '—'}</b><small>{cur.sector_note}</small></div>
            <div><span>Earnings</span><b>{cur.earnings_date || '—'} {cur.kind}</b></div>
            <div><span>Status at observation</span><Status s={cur.calendar.status_at_observation} /></div>
            <div><span>Status now</span><Status s={cur.calendar.status_now} /><small>evidence {cur.calendar.evidence_age_hours} h old</small></div>
            <div><span>Unusual Whales flag</span><b>{cur.calendar.uw_flag || '— (not seen by Unusual Whales)'}</b></div>
          </div>
          {cur.calendar.note && <p className="sm-note"><Info size={12} /> {cur.calendar.note}. Independence of the vendors is unproven.</p>}
          {!!cur.calendar.no_trade_reasons?.length && <p className="sm-note warn">Calendar reasons for NO TRADE: {cur.calendar.no_trade_reasons.join(', ')}</p>}
          <table className="sm-mini">
            <thead><tr><th>Vendor</th><th>Date</th><th>Label / clock</th><th>Flag (raw)</th><th>Retrieved</th><th>Raw SHA-256</th></tr></thead>
            <tbody>{cur.observations.map((o: any, i: number) => (
              <tr key={i}><td>{o.source}</td><td>{o.date}</td><td>{o.label || o.clock || '—'}</td><td>{o.vendor_flag || '—'}</td><td>{fmtTime(o.observed_at)}</td><td className="mono">{o.raw_sha256.slice(0, 10)}…</td></tr>
            ))}</tbody>
          </table>
          <h4>Engine output (frozen 40-point engine)</h4>
          <div className="sm-two">
            {(['continuation', 'contrarian'] as const).map((v) => (
              <div key={v} className="sm-engine">
                <div className="sm-engine-head"><b>{v}</b><Pill signal={cur.engine[v].signal} /></div>
                <div className="sm-engine-pts">bull {cur.engine[v].bull} · bear {cur.engine[v].bear} · available {cur.engine[v].available}/40</div>
                <div className="sm-codes">{cur.engine[v].reason_codes.map((c: string) => <span key={c} className="sm-code">{c}</span>)}</div>
                <table className="sm-mini"><tbody>{Object.entries(cur.subscores?.[v] || {}).map(([k, s]: any) => (
                  <tr key={k} className={s.available ? '' : 'miss'}><td>{k}</td><td>{s.available ? `${s.bull}↑ ${s.bear}↓ / ${s.weight}` : `missing${s.missing ? ': ' + s.missing : ''}`}</td></tr>
                ))}</tbody></table>
              </div>
            ))}
          </div>
          <h4>Missing evidence</h4>
          <ul className="sm-miss">{cur.missing_factors.map((m: any, i: number) => <li key={i}><b>{m.factor}</b> {m.name} — {m.reason}</li>)}</ul>
        </section>
      ) : <p className="sm-note">No current-calendar observation for this symbol.</p>}

      {data.historical_replay?.length > 0 && (
        <section>
          <h3>Historical replay <span className="badge gray">HISTORICAL REPLAY · not live</span></h3>
          <table className="sm-mini">
            <thead><tr><th>Event</th><th>Experiment</th><th>Points</th><th>Continuation</th><th>Contrarian</th><th>Missing</th></tr></thead>
            <tbody>{data.historical_replay.map((r: any, i: number) => (
              <tr key={i}>
                <td>{r.earnings_date} {r.kind}</td>
                <td>{r.primary ? <span className="badge green">PRIMARY</span> : <span className="badge amber">EXPLORATORY</span>}{r.f7_unverified && <span className="badge red">F7 unverified</span>}<small>{r.experiment}</small></td>
                <td>{r.available_points}/40</td><td><Variant v={r.engine.continuation} /></td><td><Variant v={r.engine.contrarian} /></td><td><Missing list={r.missing_factors} /></td>
              </tr>
            ))}</tbody>
          </table>
        </section>
      )}
      <section className="sm-lim"><h4>Limitations</h4><ul>{data.limitations.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul></section>
    </div>
  );
}
