import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { Loader2, Target, CalendarDays, ChevronRight, Plus, AlertTriangle, TrendingUp } from 'lucide-react';
import type { PageContext } from '../App';
import { ScoreGauge } from '../components/common';
import { RiskAlertsCard } from '../components/EarningsShared';
import { api, api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { signalView, legacySignalView, bandsFor } from '../lib/earningsSignal';
import './tab-scan.css';
import './earnings-trade-screen.css';   // ets-* layout (same as Claude +-)

// Claude New (Direction v3) single-stock detail -- same card layout as the
// Claude +- (Earnings Trade) detail: a scored gauge, a confidence card, the
// News & Risk Alerts panel, sub-tabs and an Add-to-Watchlist button.

const num = (v: any, d = 2) => (v == null ? '--' : Number(v).toFixed(d));

export default function ClaudeLastDetail({ ctx, base = '/claude-last', pz = false, model }:
  { ctx: PageContext; base?: string; pz?: boolean;
    model?: { base: string; title: string; detail: (sym: string, s?: AbortSignal) => Promise<any> } }) {
  const { symbol } = useParams();
  const navigate = useNavigate();
  const [whyOpen, setWhyOpen] = useState(true);
  const sym = (symbol || ctx.symbol || '').toUpperCase();
  if (model) base = model.base;

  const resp = useApi<any>((s) => (model ? model.detail(sym, s) : api2.claudeLastDetail(sym, s)), [sym],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const quote = useApi<any>((s) => (ctx.demo ? Promise.resolve(null) : api.quote(sym, s)), [sym],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  // Expected move + high-impact alerts, same sources as the Claude +- detail.
  const trade = useApi<any>((s) => (ctx.demo ? Promise.resolve(null) : api2.earningsTrade(sym, 'default', s)), [sym],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const em = trade.data?.expected_move || {};
  const d = resp.data;
  const ok = d?.status === 'OK';
  const score: number | null = ok ? d.score : null;
  const params: any[] = ok ? (d.params || []) : [];
  // Earnings models show the backend's final signal/tier; the intraday models that
  // share this component keep their fixed 55/45 reading of the score.
  const legacy = !!model?.base?.includes('intraday');
  const sv = legacy ? legacySignalView(score) : signalView(ok ? d : null);

  useEffect(() => {
    if (ctx.demo || ok || resp.loading) return;
    const id = window.setTimeout(() => resp.refresh(), 5000);
    return () => window.clearTimeout(id);
  }, [ctx.demo, ok, resp.loading, resp.refresh]);

  const q = quote.data || {};
  const chg = q.change_percent ?? q.changePercent;
  const title = model ? model.title : pz ? 'Claude New +0' : 'Claude New';

  const leanOf = (p: any) => (p.bias == null ? 'none'
    : p.bias > 0.15 ? 'buy' : p.bias < -0.15 ? 'sell' : 'flat');

  return (
    <div className="page ets">
      <div className="ets-back-row">
        <Link to={`${base}${ctx.search}`} className="ets-back">
          <ChevronRight size={14} style={{ transform: 'rotate(180deg)' }} /> {title}
        </Link>
      </div>

      {(resp.initialLoading && !d) || (!ok && d?.status === 'NO_DATA') ? (
        <div className="ets-empty ets-loading"><Loader2 className="ets-spin" size={18} />
          Warming {sym}'s direction score — this fills in automatically…</div>
      ) : !ok ? (
        <div className="ets-empty">No direction score for {sym} — it isn't reporting in the window.</div>
      ) : (
        <>
          {/* Header */}
          <div className="ets-header">
            <div className="ets-id">
              <div className="ets-logo">{sym.slice(0, 2)}</div>
              <div>
                <div className="ets-name-row">
                  <span className="ets-name">{d.company || sym}</span>
                  <span className="ets-ticker">{sym}</span>
                </div>
                <div className="ets-meta">US · {d.sector || '—'}</div>
              </div>
            </div>
            <div className="ets-price">
              <div className="ets-price-v">{q.price != null ? `$${num(q.price)}` : '--'}</div>
              {chg != null && (
                <div className={`ets-price-c ${chg >= 0 ? 'pos' : 'neg'}`}>
                  {chg >= 0 ? '+' : ''}{num(chg)}%</div>
              )}
            </div>
            <div className="ets-earn">
              <div className="ets-earn-l">Earnings Date</div>
              <div className="ets-earn-v"><CalendarDays size={13} /> {d.date_label || d.date || '—'}</div>
              <div className="ets-earn-t">{d.report_timing || d.time_label || ''}</div>
            </div>
            {d.days_to != null && (
              <div className="ets-days"><div className="ets-days-n">{d.days_to}</div>
                <div className="ets-days-l">Days</div></div>
            )}
            <div className="ets-actions">
              <button className="btn-primary" onClick={() => api2.watchlistAdd(sym).catch(() => {})}>
                <Plus size={15} /> Add to Watchlist
              </button>
            </div>
          </div>

          {/* Sub-tabs */}
          <div className="ets-subtabs">
            <button type="button" className="ets-subtab active">Direction</button>
            <Link className="ets-subtab" to={`/earnings-equity/${sym}${ctx.search}`}>Stock detail</Link>
            <Link className="ets-subtab" to={`/news/${sym}${ctx.search}`}>News &amp; Sentiment</Link>
          </div>

          {/* Row: direction signal / expected move / news & risk alerts (same as Claude +-) */}
          <div className="ets-row ets-row-3">
            {/* Direction Signal */}
            <div className="ets-card">
              <div className="ets-card-h">
                <span><Target size={15} /> Direction Signal</span>
                <span className={`ets-badge ${sv.tone}`}>{sv.label}</span>
              </div>
              <div className="ets-gauges ets-gauges-one">
                <div className="ets-gauge-wrap">
                  <div className="ets-gauge-title">Direction</div>
                  <div className="ets-gauge">
                    <ScoreGauge value={score ?? 0} color={sv.color} size={118} />
                    <div className="ets-gauge-num">
                      <span style={{ color: sv.color }}>{score == null ? '--' : legacy ? Math.round(score) : score}</span>
                      <span className="ets-gauge-max">/100</span>
                    </div>
                  </div>
                  <div className="ets-setup" style={{ color: sv.color }}>{sv.setup}</div>
                  {sv.note && <div className="ets-conf-note">{sv.note}</div>}
                </div>
              </div>
              <button className="ets-why-toggle" onClick={() => setWhyOpen((v) => !v)} aria-expanded={whyOpen}>
                {whyOpen ? 'Hide' : 'Why these scores?'}
                <ChevronRight size={13} style={{ transform: whyOpen ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }} />
              </button>
              {whyOpen && (
                <div className="ets-why">
                  <div className="ets-why-side">
                    <div className="ets-why-h">Direction
                      <span className="ets-why-score">{score != null ? `${score}/100 · ${sv.label}` : '--'}</span>
                    </div>
                    {params.map((p: any, i: number) => (
                      <div key={i} className="ets-why-row">
                        <span className={`ets-why-dot ${leanOf(p)}`} />
                        <div>
                          <div className="ets-why-t">
                            <b>{p.label}</b>
                            <em>{p.bias == null ? 'no data' : `${p.points?.toFixed(1)} / ${p.max}`}</em>
                          </div>
                          {p.sub && (
                            <div className="ets-why-d">
                              {p.sub.map((s: any) => `${s.name} ${s.points?.toFixed(1)}/${s.max}`).join(' · ')}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                    {!params.length && <div className="ets-why-d">No parameter detail available.</div>}
                  </div>
                  <div className="ets-note">
                    5-step scale: strong bull = full Max · neutral = half · strong bear = 0; inputs with no
                    data are left out and the rest rescaled to 100.
                    {legacy ? ' BUY ≥ 55 · NEUTRAL 45–55 · SELL ≤ 45.' : ` ${bandsFor(base) ?? ''}`}
                  </div>
                </div>
              )}
            </div>

            {/* Expected Move */}
            <div className="ets-card">
              <div className="ets-card-h"><span><TrendingUp size={15} /> Expected Move</span></div>
              <div className="ets-em">
                <div className="ets-em-big">±{em.percent != null ? num(em.percent, 1) : '--'}%</div>
                <div className="ets-em-dollar">(~${em.dollars != null ? num(em.dollars, 2) : '--'})</div>
              </div>
              <div className="ets-em-range">
                <div><span>Lower</span><b>${em.lower != null ? num(em.lower, 2) : '--'}</b></div>
                <div><span>Upper</span><b>${em.upper != null ? num(em.upper, 2) : '--'}</b></div>
              </div>
              <div className="ets-em-bar"><div className="ets-em-dot" /></div>
              <div className="ets-em-cur">Current ${em.current != null ? num(em.current, 2) : '--'}</div>
              {/* Implied-Move gate (v2 spec §3): IM ratio decides size / no-trade. */}
              {d.im_ratio != null && (() => {
                const g = d.im_gate as string;
                const tone = g === 'no_trade' ? 'neg' : g === 'half' ? 'warn' : 'pos';
                const label = d.priced_in ? 'PRICED IN — NO TRADE'
                  : g === 'no_trade' ? 'NO TRADE — expectations too high'
                    : g === 'half' ? 'HALF SIZE' : 'Trade normal size';
                return (
                  <div className={`ets-im-gate ${tone}`}>
                    <div className="ets-im-row"><span>IM ratio</span>
                      <b>{num(d.im_ratio, 2)}×</b></div>
                    <div className="ets-im-row"><span>Implied vs hist. move</span>
                      <b>{d.im_pct != null ? num(d.im_pct, 1) : '--'}% / {d.hist_avg_move != null ? num(d.hist_avg_move, 1) : '--'}%</b></div>
                    <div className="ets-im-gate-tag">{label}</div>
                  </div>
                );
              })()}
            </div>

            {/* News & Risk Alerts (same compact card used on the list) */}
            <RiskAlertsCard symbol={sym} demo={ctx.demo} />
          </div>

          {/* Confidence -- below the cards (like Claude +-'s Confidence/Exit row). */}
          <div className="ets-row">
            <div className="ets-card">
              <div className="ets-card-h"><span>Confidence</span>
                {d.low_confidence && <span className="ets-badge flat">LOW</span>}</div>
              <div className="ets-conf">
                <div className="ets-conf-row"><span>Coverage</span>
                  <b>{d.coverage != null ? `${d.coverage}%` : '--'}</b></div>
                <div className="ets-conf-bar"><i style={{ width: `${d.coverage || 0}%` }} /></div>
                <div className="ets-conf-row"><span>Agreement</span>
                  <b>{d.agreement != null ? `${d.agreement}%` : '--'}</b></div>
                <div className="ets-conf-bar"><i style={{ width: `${d.agreement || 0}%` }} /></div>
                {d.low_confidence && (
                  <div className="ets-conf-note"><AlertTriangle size={13} /> Thin or conflicted signals — do not trade on this alone.</div>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
