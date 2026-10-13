import { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2, Scale, AlertTriangle } from 'lucide-react';
import type { PageContext } from '../App';
import { ScoreGauge } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Claude Last (Direction v3) single-stock detail -- score gauge + confidence
// meter + the 8-parameter breakdown, in the same spirit as the Earnings Trade
// detail page.

const sigColor = (s: number | null) =>
  s == null ? 'var(--text-mute)' : s >= 55 ? 'var(--green)' : s <= 45 ? 'var(--red)' : 'var(--amber)';
const sigLabel = (s: number | null) =>
  s == null ? '--' : s >= 55 ? 'BUY' : s <= 45 ? 'SELL' : 'NEUTRAL';
const sigTier = (s: number | null) =>
  s == null ? 'neu' : s >= 55 ? 'buy' : s <= 45 ? 'sell' : 'neu';

export default function ClaudeLastDetail({ ctx, base = '/claude-last', pz = false }:
  { ctx: PageContext; base?: string; pz?: boolean }) {
  const { symbol } = useParams();
  const navigate = useNavigate();
  const sym = (symbol || ctx.symbol || '').toUpperCase();

  const resp = useApi<any>((s) => api2.claudeLastDetail(sym, s), [sym],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const d = resp.data;
  const ok = d?.status === 'OK';
  const score: number | null = ok ? d.score : null;
  const params: any[] = ok ? (d.params || []) : [];

  useEffect(() => {
    if (ctx.demo || ok || resp.loading) return;
    const id = window.setTimeout(() => resp.refresh(), 5000);
    return () => window.clearTimeout(id);
  }, [ctx.demo, ok, resp.loading, resp.refresh]);

  const leanOf = (p: any) => p.points > p.max / 2 + 0.3 ? 'pos'
    : p.points < p.max / 2 - 0.3 ? 'neg' : 'neu';

  return (
    <div className="page">
      <div className="cnd-top">
        <button className="cnd-back" onClick={() => navigate(base)}>
          <ArrowLeft size={15} /> {pz ? 'Claude New +0' : 'Claude New'}
        </button>
      </div>

      {(resp.initialLoading && !d) || (!ok && d?.status === 'NO_DATA') ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Warming {sym}'s
          direction score — this fills in automatically…</div>
      ) : !ok ? (
        <div className="es-empty">No direction score for {sym} — it isn't reporting in the window.</div>
      ) : (
        <>
          <div className="cnd-head">
            <div className="cnd-gauge">
              <ScoreGauge value={score ?? 0} color={sigColor(score)} size={120} />
              <div className="cnd-gauge-num" style={{ color: sigColor(score) }}>
                {score}<span>/100</span>
              </div>
            </div>
            <div className="cnd-head-mid">
              <div className="cnd-sym">{sym} <span className="cnd-co">{d.company || ''}</span></div>
              <div className={`cnd-signal ${sigTier(score)}`}>{sigLabel(score)}</div>
              <div className="cnd-meta">
                {d.date_label || d.date || ''}{d.time_label ? ` · ${d.time_label}` : ''}
                {d.days_to != null ? ` · in ${d.days_to}d` : ''}
                {d.sector ? ` · ${d.sector}` : ''}
              </div>
              {(d.coverage != null) && (
                <div className={`cnd-conf ${d.low_confidence ? 'low' : 'ok'}`}>
                  {d.low_confidence && <AlertTriangle size={12} />}
                  {d.low_confidence ? 'LOW CONFIDENCE — do not trade' : 'Confidence OK'}
                  <span className="cnd-conf-nums">coverage {d.coverage}% · agreement {d.agreement}%</span>
                </div>
              )}
              <div className="cnd-formula">Two-sided v3: every parameter can score below 50.
                Points = weight × (0.5 + 0.5 × bias). BUY ≥ 55 · NEUTRAL · SELL ≤ 45.</div>
            </div>
          </div>

          {pz && (() => {
            // Signed contribution of each parameter = bias x weight = 2*(points - weight/2).
            // Summed, these give the net; score = 50 + (net / 100 available) x 50.
            const rows = params.map((p) => ({ ...p, c: 2 * ((p.points ?? p.max / 2) - p.max / 2) }));
            const ups = rows.filter((p) => p.c > 0.05).sort((a, b) => b.c - a.c);
            const downs = rows.filter((p) => p.c < -0.05).sort((a, b) => a.c - b.c);
            const neutral = rows.filter((p) => Math.abs(p.c) <= 0.05);
            const sumUp = ups.reduce((n, p) => n + p.c, 0);
            const sumDn = downs.reduce((n, p) => n + p.c, 0);   // negative
            const net = sumUp + sumDn;
            const present = 100;    // v3.1 weights always sum to 100 (missing data = half weight)
            const sgn = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(1);
            return (
              <div className="ets-card cnd-card">
                <div className="ets-card-h"><span><Scale size={15} />
                  Score breakdown (+ / 0 / −)</span></div>
                <div className="cnd-pm cnd-pm3">
                  <div className="cnd-pm-col up">
                    <div className="cnd-pm-h">Pushing UP (+)</div>
                    {ups.length ? ups.map((p) => (
                      <div key={p.name} className="cnd-pm-row">
                        <span className="cnd-pm-lbl">{p.label}</span>
                        <span className="cnd-pm-val up">+{p.c.toFixed(1)}</span>
                      </div>
                    )) : <div className="cnd-pm-none">Nothing bullish</div>}
                    <div className="cnd-pm-sub">+{sumUp.toFixed(1)}</div>
                  </div>
                  <div className="cnd-pm-col zero">
                    <div className="cnd-pm-h">Neutral (0)</div>
                    {neutral.length ? neutral.map((p) => (
                      <div key={p.name} className="cnd-pm-row">
                        <span className="cnd-pm-lbl">{p.label}</span>
                        <span className="cnd-pm-val zero">{p.bias == null ? 'no data' : '0.0'}</span>
                      </div>
                    )) : <div className="cnd-pm-none">None neutral</div>}
                    <div className="cnd-pm-sub">{neutral.length}</div>
                  </div>
                  <div className="cnd-pm-col down">
                    <div className="cnd-pm-h">Pulling DOWN (−)</div>
                    {downs.length ? downs.map((p) => (
                      <div key={p.name} className="cnd-pm-row">
                        <span className="cnd-pm-lbl">{p.label}</span>
                        <span className="cnd-pm-val down">{p.c.toFixed(1)}</span>
                      </div>
                    )) : <div className="cnd-pm-none">Nothing bearish</div>}
                    <div className="cnd-pm-sub">{sumDn.toFixed(1)}</div>
                  </div>
                </div>
                <div className="cnd-net">
                  <div className="cnd-net-h">Net</div>
                  <div className="cnd-net-big" style={{ color: sigColor(score) }}>{sgn(net)} pts</div>
                  <div className="cnd-net-calc">= +{sumUp.toFixed(1)} − {Math.abs(sumDn).toFixed(1)}
                    {' · '}{neutral.length} neutral</div>
                  <div className="cnd-net-formula">Score = 50 + ({sgn(net)} ÷ {present} available) × 50
                    = <b style={{ color: sigColor(score) }}>{score}</b> / 100</div>
                </div>
              </div>
            );
          })()}

          {!pz && (
          <div className="ets-card cnd-card">
            <div className="ets-card-h"><span><Scale size={15} /> Parameter breakdown</span></div>
            <div className="cnd-params">
              {params.map((p) => {
                const pct = Math.max(0, Math.min(100, (p.points / p.max) * 100));
                const lean = leanOf(p);
                return (
                  <div key={p.name} className={`cnd-prow ${lean}`}>
                    <span className="cnd-plbl">{p.label}</span>
                    <span className="cnd-pbar"><i style={{ width: `${pct}%` }} /></span>
                    <span className="cnd-ppts"><b>{p.points?.toFixed(1)}</b> / {p.max}</span>
                    <span className="cnd-praw">{p.bias != null
                      ? `${p.bias > 0 ? '+' : ''}${p.bias}` : 'no data'}</span>
                    {p.sub && (
                      <span className="cnd-psub">
                        {p.sub.map((s: any) => `${s.name} ${s.points?.toFixed(1)}/${s.max}`).join(' · ')}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="cnd-total">Total (sum of rows) = <b style={{ color: sigColor(score) }}>{score}</b> / 100</div>
          </div>
          )}
        </>
      )}
    </div>
  );
}
