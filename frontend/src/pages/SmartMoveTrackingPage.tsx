import { Fragment, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Download, FlaskConical, History, RefreshCw } from 'lucide-react';
import type { PageContext } from '../App';
import { smartMoveTracking } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './smart-move.css';

const SIGNAL_LABEL: Record<string, string> = { BUY: 'BUY', SELL: 'SELL', NO_TRADE: 'NO TRADE' };

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' });
}

function Pill({ signal }: { signal?: string | null }) {
  if (!signal) return <span className="badge gray">—</span>;
  return <span className={`sm-sig ${signal.toLowerCase()}`}>{SIGNAL_LABEL[signal] || signal}</span>;
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

/* Smart Move tracking: saved prediction history, outcome tracking and the report. Read only: nothing here can send a request to a data provider or change a prediction. */
export default function SmartMoveTrackingPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [mode, setMode] = useState<'LIVE' | 'SIMULATION'>('LIVE');
  const [signal, setSignal] = useState('');
  const [symbol, setSymbol] = useState('');
  const [open, setOpen] = useState<string>('');

  const status = useApi<any>((s) => smartMoveTracking.status(s), []);
  const query = new URLSearchParams({ mode, limit: '500', ...(signal ? { signal } : {}), ...(symbol.trim() ? { symbol: symbol.trim() } : {}) }).toString();
  const hist = useApi<any>((s) => smartMoveTracking.history(query, s), [query]);
  const acc = useApi<any>((s) => smartMoveTracking.accuracy(mode, s), [mode]);
  const ana = useApi<any>((s) => smartMoveTracking.analytics(mode, s), [mode], { refreshMs: 30_000 });
  const outc = useApi<any>((s) => smartMoveTracking.outcomes(s), [mode], { refreshMs: 30_000 });
  const [dim, setDim] = useState('sector');

  const st = status.data;
  const rows: any[] = hist.data?.rows || [];
  const variants = acc.data?.variants ? Object.entries<any>(acc.data.variants) : [];
  const downloadReport = async () => {
    const rep = await smartMoveTracking.report();
    download(`smart_move_tracking_${new Date().toISOString().slice(0, 10)}.md`, rep.markdown, 'text/markdown');
  };

  return (
    <div className="sm">
      <PageHead title="Smart Move Tracking" subtitle="Saved predictions, their version history and outcome tracking. Read only: nothing here contacts a data provider or edits a prediction." />
      <div className="sm-banner">
        <AlertTriangle size={16} />
        <div>Shadow research only; no trade is placed. <b>No accuracy claim is allowed</b> until the pre-registered minimum sample (100 decided events per variant over 8 earnings weeks) and an evidence audit are met. The numbers below are descriptive.</div>
      </div>
      {status.error && <div className="sm-fresh stale"><AlertTriangle size={14} /> Status unavailable: {status.error}</div>}
      {st?.demo && <div className="sm-fresh"><FlaskConical size={14} /> DEMO: simulated predictions and outcomes with no skill built in. This tests the screens only.</div>}
      {st && !st.prediction_versions && <div className="sm-fresh"><History size={14} /> {st.message}</div>}

      <div className="sm-tabs">
        {(['LIVE', 'SIMULATION'] as const).map((m) => (
          <button key={m} className={`sm-tab ${mode === m ? 'on' : ''}`} onClick={() => setMode(m)}>{m === 'LIVE' ? 'Real captures' : 'Simulation'}</button>
        ))}
        <span style={{ flex: 1 }} />
        <button className="ghost-btn" onClick={downloadReport} title="Download the full report as Markdown"><Download size={14} /> Report</button>
        <button className="ghost-btn" onClick={() => { status.refresh(); hist.refresh(); acc.refresh(); }}><RefreshCw size={14} /> Reload</button>
      </div>

      <div className="sm-summary">
        <div className="sm-card"><div className="sm-card-k">Events / versions (current universe)</div><div className="sm-card-v">{st ? (mode === 'LIVE' ? `${st.universe_events} / ${st.universe_versions}` : st.by_mode?.SIMULATION?.prediction_versions) : '—'}</div>{mode === 'LIVE' && st && <small>{st.prediction_versions} versions over {st.saved_events_total} saved events in all stores, incl. events outside the universe</small>}</div>
        <div className="sm-card"><div className="sm-card-k">Outcomes recorded</div><div className="sm-card-v">{st?.by_mode?.[mode]?.outcomes ?? '—'}</div></div>
        <div className="sm-card"><div className="sm-card-k">Accuracy claim</div><div className="sm-card-v">{acc.data ? (acc.data.claim_allowed ? 'allowed' : 'not allowed') : '—'}</div></div>
      </div>

      <h3 className="sm-h">Outcome tracking ({mode === 'LIVE' ? 'real captures' : 'simulation'})</h3>
      {acc.error && <div className="sm-fresh stale"><AlertTriangle size={14} /> {acc.error}</div>}
      {acc.data && <div className="sm-fresh"><Info text={acc.data.claim_text} /></div>}
      {acc.data && !variants.length && <div className="sm-none">No graded events yet. Outcomes are imported after the announcement, from saved data only.</div>}
      {variants.length > 0 && (
        <div className="sm-table-wrap"><table className="sm-table">
          <thead><tr><th>Variant</th><th>Graded</th><th>Decided (BUY/SELL)</th><th>Coverage</th><th>Strict hit rate</th><th>95% interval</th><th>Weeks</th><th>Minimum sample</th></tr></thead>
          <tbody>{variants.map(([v, x]) => (
            <tr key={v}><td>{v}</td><td>{x.graded_events}</td><td>{x.decided}</td><td>{x.coverage ?? '—'}</td><td>{x.strict_hit_rate ?? '—'}</td>
              <td>{x.strict_hit_ci95 ? `${x.strict_hit_ci95[0]} to ${x.strict_hit_ci95[1]}` : '—'}</td><td>{x.earnings_weeks}</td><td>{x.minimum_sample_met ? 'met' : 'not met'}</td></tr>
          ))}</tbody>
        </table></div>
      )}

      <h3 className="sm-h">Analytics by group ({mode === 'LIVE' ? 'real captures' : 'simulation'})</h3>
      {ana.error && <div className="sm-fresh stale"><AlertTriangle size={14} /> {ana.error}</div>}
      {ana.data && (
        <>
          <div className="sm-fresh"><Info text={ana.data.claim_text} /> Sample: {ana.data.graded_rows} graded of {ana.data.metadata_known.rows} variant-rows; liquidity known for {ana.data.metadata_known.with_liquidity}, sector for {ana.data.metadata_known.with_sector}. {ana.data.note}</div>
          <div className="sm-tabs">{['sector', 'kind', 'coverage', 'liquidity', 'score'].map((k) => <button key={k} className={`sm-tab ${dim === k ? 'on' : ''}`} onClick={() => setDim(k)}>{k === 'kind' ? 'timing' : k}</button>)}</div>
          <div className="sm-table-wrap"><table className="sm-table">
            <thead><tr><th>Variant</th><th>Group</th><th>Signals (BUY/SELL)</th><th>Graded decided</th><th>Hits</th><th>Hit rate</th><th>95% interval</th><th>Avg move %</th><th>Ungraded</th></tr></thead>
            <tbody>
              {Object.entries<any>(ana.data.variants).map(([v, x]) => (
                <Fragment key={v}>
                  <tr><td><b>{v}</b></td><td>all</td><td>{x.signal_counts.BUY} / {x.signal_counts.SELL} <small>({x.signal_counts.NO_TRADE} no trade, {Math.round((x.no_trade_frequency || 0) * 100)}%)</small></td><td>{x.overall.decided}</td><td>{x.overall.hits}</td><td>{x.overall.hit_rate ?? '—'}</td><td>{x.overall.ci95 ? `${x.overall.ci95[0]} to ${x.overall.ci95[1]}` : '—'}</td><td>{x.overall.mean_move_pct ?? '—'}</td><td>{x.overall.ungraded}</td></tr>
                  {Object.entries<any>(x.by[dim] || {}).map(([g, y]) => (
                    <tr key={v + g}><td /><td>{g}</td><td>{y.events}</td><td>{y.decided}</td><td>{y.hits}</td><td>{y.hit_rate ?? '—'}</td><td>{y.ci95 ? `${y.ci95[0]} to ${y.ci95[1]}` : '—'}</td><td>{y.mean_move_pct ?? '—'}</td><td>{y.ungraded}</td></tr>
                  ))}
                </Fragment>
              ))}
            </tbody></table></div>
        </>
      )}
      {mode === 'LIVE' && outc.data && <div className="sm-none">Outcomes saved with price detail: {outc.data.total}. Outcomes are collected only after the reaction session closes plus a 30-minute safety delay.</div>}

      <h3 className="sm-h">Prediction history</h3>
      <div className="sm-filters">
        <input placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Symbol" />
        <select value={signal} onChange={(e) => setSignal(e.target.value)} aria-label="Signal">
          <option value="">Any signal</option><option value="BUY">BUY</option><option value="SELL">SELL</option><option value="NO_TRADE">NO TRADE</option>
        </select>
      </div>
      {hist.error && <div className="sm-fresh stale"><AlertTriangle size={14} /> {hist.error}</div>}
      {hist.data && !rows.length && <div className="sm-none">No saved predictions for this view.</div>}
      {rows.length > 0 && (
        <div className="sm-table-wrap"><table className="sm-table">
          <thead><tr><th /><th>Symbol</th><th>Earnings</th><th>Continuation</th><th>Contrarian</th><th>Versions</th><th>Captured (ET)</th><th>Outcome</th><th>Flags</th></tr></thead>
          <tbody>{rows.map((r) => {
            const key = `${r.symbol}|${r.earnings_date}`;
            const isOpen = open === key;
            return (
              <Fragment key={key}>
                <tr onClick={() => setOpen(isOpen ? '' : key)}>
                  <td>{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                  <td><b>{r.symbol}</b></td><td>{r.earnings_date}</td>
                  <td><Pill signal={r.latest.continuation.signal} /> <small>{r.latest.continuation.bull}↑ {r.latest.continuation.bear}↓ of {r.latest.continuation.available}/40</small></td>
                  <td><Pill signal={r.latest.contrarian.signal} /> <small>{r.latest.contrarian.bull}↑ {r.latest.contrarian.bear}↓</small></td>
                  <td>{r.versions}</td><td>{fmtTime(r.last_captured_at)}</td>
                  <td>{r.outcome ? `${r.outcome.move_pct > 0 ? '+' : ''}${r.outcome.move_pct}% (${r.outcome.class})` : 'pending'}</td>
                  <td>{r.late_capture ? <span className="badge red">LATE, not graded</span> : r.blocked_by.length ? <span className="badge amber">{r.blocked_by.length} gate</span> : ''}</td>
                </tr>
                {isOpen && (
                  <tr><td /><td colSpan={8}>
                    <table className="sm-mini"><thead><tr><th>Version</th><th>Captured</th><th>Continuation</th><th>Contrarian</th><th>Evidence items</th><th>Digest</th></tr></thead>
                      <tbody>{r.chain.map((c: any) => (
                        <tr key={c.version}><td>v{c.version}</td><td>{fmtTime(c.captured_at)}</td><td><Pill signal={c.signals.continuation} /></td><td><Pill signal={c.signals.contrarian} /></td><td>{c.evidence_items}</td><td className="sm-hash">{c.digest}</td></tr>
                      ))}</tbody></table>
                    {r.blocked_by.length > 0 && <small>Blocked by: {r.blocked_by.join(', ')}</small>}
                  </td></tr>
                )}
              </Fragment>
            );
          })}</tbody>
        </table></div>
      )}
      {hist.data && hist.data.total > rows.length && <div className="sm-none">Showing {rows.length} of {hist.data.total}. Narrow the filters to see the rest.</div>}
    </div>
  );
}

function Info({ text }: { text: string }) {
  return <span>{text}</span>;
}
