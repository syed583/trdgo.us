import React, { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ChevronRight, Info, Target } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState } from './shared';
import { Panel } from '../components/common';

/**
 * Model Performance: does the score actually predict?
 *
 * Everything here is measured from the app's own stored calls and parameter
 * snapshots -- no new data source. The one question that matters for an
 * analysis tool: do higher scores win more often (calibration), and which
 * parameters carry real edge over the base rate.
 */

const HORIZONS = ['ALL', 'TODAY', 'TOMORROW', 'SWING'] as const;

const TERMS: { term: string; body: string }[] = [
  { term: 'What this page is',
    body: 'A track record for the app’s own model. Every call it makes is stored, then graded once its horizon has passed, so you can see whether the score actually predicts — not just what it claims.' },
  { term: 'Hit rate',
    body: 'How often a call was right — meaning the stock beat SPY in the called direction over the call’s horizon, measured from the price at the moment of the call. 50% is a coin flip; above 50% is real skill.' },
  { term: 'Avg excess vs SPY',
    body: 'The average out- (or under-) performance versus SPY per call. Hit rate says how often; this says by how much.' },
  { term: 'Calibration',
    body: 'The key test: do higher scores actually win more? A trustworthy score climbs down the table — the 70–100 band should beat the 40–60 band. If they hit the same, the number is not measuring anything and the model needs re-tuning.' },
  { term: 'Edge vs base rate',
    body: 'For each parameter: its hit rate minus how often the stock rose at all (the base rate). A parameter that matches the base rate has no edge, however good its raw hit rate looks. Positive edge = it carries real predictive weight.' },
  { term: 'Why some cells are empty',
    body: 'Grading needs time to pass after a call. Parameter edge is measured a few sessions after each snapshot, so it fills in as the app runs — a young install shows thin numbers until enough calls have been judged.' },
];

function Explainer() {
  const [open, setOpen] = useState(false);
  return (
    <div className={`mp-explain ${open ? 'open' : ''}`}>
      <button className="mp-explain-head" onClick={() => setOpen((v) => !v)}>
        <Info size={13} /> What is this page?
        <ChevronRight size={14} className="mp-explain-caret" />
      </button>
      {open && (
        <div className="mp-explain-body">
          {TERMS.map((t) => (
            <div className="mp-term" key={t.term}><b>{t.term}</b><span>{t.body}</span></div>
          ))}
        </div>
      )}
    </div>
  );
}

function pct(v: number | null | undefined): string {
  return typeof v === 'number' ? `${v.toFixed(1)}%` : '—';
}

function hitColor(v: number | null | undefined): string {
  if (typeof v !== 'number') return 'var(--text-mute)';
  if (v >= 55) return 'var(--green)';
  if (v <= 45) return 'var(--red)';
  return 'var(--amber)';
}

/** A horizontal hit-rate bar, 0-100%, with the 50% coin-flip line marked. */
function HitBar({ value }: { value: number | null | undefined }) {
  const v = typeof value === 'number' ? Math.max(0, Math.min(100, value)) : 0;
  return (
    <div className="mp-bar">
      <div className="mp-bar-fill" style={{ width: `${v}%`, background: hitColor(value) }} />
      <div className="mp-bar-mid" />
    </div>
  );
}

export default function ModelPerformancePage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const { symbol: routeSymbol } = useParams();
  // A ticker in the URL scopes the track record to that one stock; without it,
  // the whole book. Searching a ticker lands here as /model-performance/SYM.
  const scopeSym = (routeSymbol || '').toUpperCase() || null;
  const [horizon, setHorizon] = useState<typeof HORIZONS[number]>('ALL');
  const card = useApi<any>((s) => api2.callScorecard(
    horizon === 'ALL' ? undefined : horizon, 90, scopeSym || undefined, s),
    [horizon, scopeSym]);
  const edge = useApi<any>((s) => api2.paramEdge(5, s), []);

  const d = card.data;
  const scoreBands: [string, any][] = d?.by_score ? Object.entries(d.by_score) : [];
  const decisions: [string, any][] = d?.by_decision ? Object.entries(d.by_decision) : [];
  const params: any[] = edge.data?.parameters || [];

  return (
    <div className="page">
      <PageHead title={scopeSym ? `Model Performance · ${scopeSym}` : 'Model Performance'}
        subtitle={scopeSym
          ? `${scopeSym}'s own track record — how the model's calls on this stock have played out.`
          : "Does the score actually predict? Hit rate, calibration and per-parameter edge — all measured from the app's own past calls."} />

      <div className="mp-scope">
        <button className={`mp-range-btn ${!scopeSym ? 'active' : ''}`}
          onClick={() => navigate(`/model-performance${ctx.search}`)}>All stocks</button>
        {scopeSym && <button className="mp-range-btn active">{scopeSym}</button>}
        {!scopeSym && (
          <span className="mp-scope-hint">Search a ticker to see just that stock's record.</span>
        )}
      </div>

      <Explainer />

      <div className="mp-range">
        <span className="mp-range-lbl">Horizon</span>
        {HORIZONS.map((h) => (
          <button key={h} className={`mp-range-btn ${horizon === h ? 'active' : ''}`}
            onClick={() => setHorizon(h)}>{h === 'ALL' ? 'All' : h.charAt(0) + h.slice(1).toLowerCase()}</button>
        ))}
      </div>

      {card.error ? <ErrorState error={card.error} />
        : card.initialLoading || !d ? <Loading />
          : (
            <>
              {/* Headline */}
              <Panel title="Track record" icon={<Target size={13} />} noBody>
                <div className="mp-head">
                  <div className="mp-head-cell">
                    <span className="mp-k">Hit rate</span>
                    <span className="mp-v" style={{ color: hitColor(d.overall?.hit_rate) }}>
                      {pct(d.overall?.hit_rate)}
                    </span>
                    <span className="mp-sub">beat SPY in the called direction</span>
                  </div>
                  <div className="mp-head-cell">
                    <span className="mp-k">Graded calls</span>
                    <span className="mp-v">{d.overall?.calls ?? 0}</span>
                    <span className="mp-sub">{d.pending ?? 0} awaiting their session</span>
                  </div>
                  <div className="mp-head-cell">
                    <span className="mp-k">Avg excess vs SPY</span>
                    <span className="mp-v" style={{
                      color: (d.overall?.avg_excess_pct ?? 0) > 0 ? 'var(--green)' : 'var(--red)',
                    }}>
                      {d.overall?.avg_excess_pct != null
                        ? `${d.overall.avg_excess_pct > 0 ? '+' : ''}${d.overall.avg_excess_pct.toFixed(2)}%`
                        : '—'}
                    </span>
                    <span className="mp-sub">per call, over its horizon</span>
                  </div>
                </div>
                {!d.enough_data && (
                  <div className="mp-warn">
                    <Info size={13} /> Small sample so far — a few weeks of graded
                    calls are needed before these rates are meaningful.
                  </div>
                )}
              </Panel>

              <div className="two-col">
                {/* Calibration: do higher scores win more? */}
                <Panel title="Calibration — do higher scores win more?" noBody>
                  <div className="table-wrap">
                    <table className="tbl">
                      <thead>
                        <tr><th>Score band</th><th className="r">Calls</th>
                          <th>Hit rate</th><th className="r">Avg excess</th></tr>
                      </thead>
                      <tbody>
                        {scoreBands.map(([band, s]) => (
                          <tr key={band}>
                            <td><b>{band}</b></td>
                            <td className="num r">{s.calls}</td>
                            <td>
                              <div className="mp-cell">
                                <HitBar value={s.hit_rate} />
                                <span style={{ color: hitColor(s.hit_rate), minWidth: 44 }}>
                                  {pct(s.hit_rate)}
                                </span>
                              </div>
                            </td>
                            <td className="num r">{s.avg_excess_pct != null
                              ? `${s.avg_excess_pct > 0 ? '+' : ''}${s.avg_excess_pct.toFixed(2)}%` : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="hint">
                    A working score climbs down this table: the 70–100 band should
                    beat the 40–60 band. If they hit the same, the number is not
                    measuring anything.
                  </div>
                </Panel>

                {/* By decision */}
                <Panel title="By call type" noBody>
                  <div className="table-wrap">
                    <table className="tbl">
                      <thead>
                        <tr><th>Decision</th><th className="r">Calls</th>
                          <th>Hit rate</th><th className="r">Avg excess</th></tr>
                      </thead>
                      <tbody>
                        {decisions.map(([dec, s]) => (
                          <tr key={dec}>
                            <td><b>{dec.charAt(0) + dec.slice(1).toLowerCase()}</b></td>
                            <td className="num r">{s.calls}</td>
                            <td>
                              <div className="mp-cell">
                                <HitBar value={s.hit_rate} />
                                <span style={{ color: hitColor(s.hit_rate), minWidth: 44 }}>
                                  {pct(s.hit_rate)}
                                </span>
                              </div>
                            </td>
                            <td className="num r">{s.avg_excess_pct != null
                              ? `${s.avg_excess_pct > 0 ? '+' : ''}${s.avg_excess_pct.toFixed(2)}%` : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Panel>
              </div>

              {/* Parameter edge */}
              <Panel title={`Which parameters carry edge${edge.data?.horizon_days ? ` — over ${edge.data.horizon_days} sessions` : ''}`} noBody>
                {edge.initialLoading ? <Loading />
                  : edge.data?.status !== 'OK' ? (
                    <div className="mp-empty">
                      {edge.data?.detail || 'Not enough parameter snapshots yet to measure edge.'}
                    </div>
                  ) : (
                    <>
                      <div className="table-wrap">
                        <table className="tbl">
                          <thead>
                            <tr><th>Parameter</th><th>Hit rate</th>
                              <th className="r">Edge vs base</th>
                              <th className="r">Avg return</th>
                              <th className="r">Obs.</th></tr>
                          </thead>
                          <tbody>
                            {params.map((p) => (
                              <tr key={p.parameter} className={p.sufficient_sample ? '' : 'mf-dim'}>
                                <td><b>{p.parameter}</b>
                                  {!p.sufficient_sample && <em className="mp-thin"> thin</em>}</td>
                                <td>
                                  <div className="mp-cell">
                                    <HitBar value={p.hit_rate_pct} />
                                    <span style={{ minWidth: 44 }}>{pct(p.hit_rate_pct)}</span>
                                  </div>
                                </td>
                                <td className="num r" style={{
                                  color: p.edge_vs_base_pct > 0 ? 'var(--green)'
                                    : p.edge_vs_base_pct < 0 ? 'var(--red)' : 'var(--text-mute)',
                                }}>
                                  {p.edge_vs_base_pct > 0 ? '+' : ''}{p.edge_vs_base_pct}%
                                </td>
                                <td className="num r">{p.avg_return_when_followed_pct != null
                                  ? `${p.avg_return_when_followed_pct > 0 ? '+' : ''}${p.avg_return_when_followed_pct}%` : '—'}</td>
                                <td className="num r">{p.calls ?? '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="hint">
                        Edge is hit rate minus the {edge.data?.base_rate_pct}% base
                        rate (how often the stock rose at all). A parameter matching
                        the base rate has no edge, however good its raw hit rate looks.
                      </div>
                    </>
                  )}
              </Panel>

              <div className="hint" style={{ padding: '2px 4px' }}>{d.note}</div>
            </>
          )}
    </div>
  );
}
