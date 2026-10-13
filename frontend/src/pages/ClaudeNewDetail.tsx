import { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2, Scale } from 'lucide-react';
import type { PageContext } from '../App';
import { ScoreGauge } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Tab A single-stock detail -- score gauge + the 9-parameter breakdown, in the
// same spirit as the Earnings Trade detail page.

const sigColor = (s: number | null) =>
  s == null ? 'var(--text-mute)' : s >= 65 ? 'var(--green)' : s <= 35 ? 'var(--red)' : 'var(--amber)';
const sigLabel = (s: number | null) =>
  s == null ? '--' : s >= 65 ? 'BUY' : s <= 35 ? 'SELL' : 'NEUTRAL';
const sigTier = (s: number | null) =>
  s == null ? 'neu' : s >= 65 ? 'buy' : s <= 35 ? 'sell' : 'neu';

export default function ClaudeNewDetail({ ctx }: { ctx: PageContext }) {
  const { symbol } = useParams();
  const navigate = useNavigate();
  const sym = (symbol || ctx.symbol || '').toUpperCase();

  const resp = useApi<any>((s) => api2.tabADetail(sym, s), [sym],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const d = resp.data;
  const ok = d?.status === 'OK';

  // While the equity is still warming the endpoint returns NO_DATA; retry on a
  // short cadence so the score fills in on its own instead of needing a reload.
  useEffect(() => {
    if (ctx.demo || ok || resp.loading) return;
    const id = window.setTimeout(() => resp.refresh(), 5000);
    return () => window.clearTimeout(id);
  }, [ctx.demo, ok, resp.loading, resp.refresh]);
  const score: number | null = ok ? d.score : null;
  const params: any[] = ok ? (d.params || []) : [];

  const leanOf = (p: any) => p.points > p.max / 2 + 0.3 ? 'pos'
    : p.points < p.max / 2 - 0.3 ? 'neg' : 'neu';

  return (
    <div className="page">
      <div className="cnd-top">
        <button className="cnd-back" onClick={() => navigate('/claude-new')}>
          <ArrowLeft size={15} /> Claude New
        </button>
      </div>

      {(resp.initialLoading && !d) || (!ok && d?.status === 'NO_DATA') ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Warming {sym}'s
          pre-earnings score — this fills in automatically…</div>
      ) : !ok ? (
        <div className="es-empty">No pre-earnings score for {sym} — it isn't reporting in the
          window.</div>
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
              <div className="cnd-formula">Score = 50 + bullish − bearish across 9 parameters
                (neutral = half weight). BUY ≥ 65 · NEUTRAL · SELL ≤ 35.</div>
            </div>
          </div>

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
        </>
      )}
    </div>
  );
}
