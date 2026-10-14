import { Loader2, Gauge } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './intraday-v2.css';

// Intraday Model v2 -- single-ticker panel for the Analysis page. Shown only when
// the backend has INTRADAY_MODEL=v2 (the endpoint returns DISABLED otherwise, and
// this renders nothing, leaving the existing page untouched). Reads the SAME
// score function as the Trdgo Stock Today view, so the two screens match.

const GROUPS: Array<[string, string[]]> = [
  ['Market context', ['market', 'sector']],
  ['Price & trend', ['vwap_pos', 'ema_trend', 'orb', 'rsi', 'rvol', 'rel_strength']],
  ['Options tape', ['flow', 'unusual', 'pc_ratio', 'levels', 'disparity']],
  ['Catalyst', ['gap', 'news']],
];
const LABEL: Record<string, string> = {
  market: 'Market (SPY+QQQ)', sector: 'Sector ETF', vwap_pos: 'VWAP position',
  ema_trend: 'EMA 9/21', orb: 'Opening range', rsi: 'RSI(14)', rvol: 'Rel. volume',
  rel_strength: 'Rel. strength', flow: 'Options flow', unusual: 'Unusual', pc_ratio: 'Put/Call',
  levels: 'Key levels', disparity: 'Disparity', gap: 'Gap/pre-market', news: 'News today',
};
const callClass = (c: string) =>
  c === 'STRONG BUY' || c === 'BUY' ? 'im2-buy'
    : c === 'STRONG SELL' || c === 'SELL' ? 'im2-sell'
      : c === 'WITHHELD' ? 'im2-withheld' : c === 'EXCLUDED' ? 'im2-excluded' : 'im2-noedge';

export default function IntradayV2Panel({ symbol }: { symbol: string }) {
  const resp = useApi<any>((s) => api2.intradayScore(symbol, s), [symbol],
    { refreshMs: 20_000 });
  const d = resp.data;
  if (d && d.status === 'DISABLED') return null;      // flag off -> render nothing
  if (resp.initialLoading && !d) {
    return (
      <div className="im2-panel">
        <div className="im2-head"><Gauge size={16} /> Intraday Model v2</div>
        <p className="im2-note"><Loader2 size={12} className="spin" /> Scoring…</p>
      </div>
    );
  }
  if (!d || d.score == null) return null;
  const params: any[] = d.params || [];
  const byName = Object.fromEntries(params.map((p) => [p.name, p]));
  const gates = d.gates || {};

  return (
    <div className="im2-panel">
      <div className="im2-head">
        <Gauge size={16} /> Intraday Model v2
        <span className={`im2-call ${callClass(d.call)}`}>{d.call}</span>
      </div>
      {d.market_stale && (
        <div className="im2-stale-flag" style={{ margin: '8px 0', display: 'inline-block' }}>
          ⚠ market data stale (SPY bars {d.spy_status}) — gates can't be trusted
        </div>
      )}
      <div className="im2-top">
        <div className="im2-score">
          <div className="im2-score-n">{d.score}</div>
          <div className="im2-score-l">score / 100 · 50 = neutral</div>
        </div>
        <div className="im2-meta">
          <div><b>Net</b> {d.net > 0 ? '+' : ''}{d.net}</div>
          <div><b>Coverage</b> {d.coverage_pct}%</div>
          {d.spot != null && <div><b>Price</b> ${Number(d.spot).toFixed(2)}</div>}
          {d.vwap != null && <div><b>VWAP</b> ${Number(d.vwap).toFixed(2)}</div>}
          <div><b>SPY</b> {d.spy_above_vwap == null ? '—' : d.spy_above_vwap ? 'above VWAP' : 'below VWAP'}</div>
        </div>
      </div>

      <div className="im2-gates">
        {Object.entries(gates).map(([k, v]) => (
          <span key={k} className={`im2-gate ${v === true ? 'ok' : v === false ? 'no' : 'na'}`}>
            {k.replace(/^G\d_/, '').replace(/_/g, ' ')}
          </span>
        ))}
      </div>

      {GROUPS.map(([title, names]) => (
        <div className="im2-group" key={title}>
          <div className="im2-group-h">{title}</div>
          <div className="im2-rows">
            {names.map((n) => {
              const p = byName[n] || {};
              const nd = p.has_data === false;
              const v = p.value || 0;
              return (
                <div className="im2-row" key={n}>
                  <span className="im2-row-l">{LABEL[n] || n}</span>
                  <span className={`im2-row-v ${v > 0 ? 'pos' : v < 0 ? 'neg' : ''} ${nd ? 'nd' : ''}`}>
                    {nd ? 'no data' : `${v > 0 ? '+' : ''}${v} / ${p.max}`}
                  </span>
                  {p.detail && <span className="im2-row-d">{p.detail}</span>}
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <div className="im2-foot">
        Signed −100…+100 net → score = 50 + net/2. A parameter with no data counts
        against coverage (G1 ≥ 70), never faked. Read-only.
      </div>
    </div>
  );
}
