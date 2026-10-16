import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Zap, TrendingUp, TrendingDown, ChevronRight } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import '../components/intraday-v2.css';
import './tab-scan.css';

// Grok Intraday -- a LIVE board over the liquid universe (like Trdgo Stock),
// scored with the Grok 10-signal short-term model. Top Buyers / Top Sellers,
// each expandable to the 10-signal breakdown.

const PARAMS: { key: string; label: string }[] = [
  { key: 'options_flow', label: 'Flow' },
  { key: 'unusual_activity', label: 'Unusual' },
  { key: 'volume_pcr', label: 'Vol/PCR' },
  { key: 'trend_price', label: 'Trend' },
  { key: 'key_levels', label: 'Levels' },
  { key: 'oi_change_expiry', label: 'OI Chg' },
  { key: 'volatility_em', label: 'Vol/EM' },
  { key: 'rsi_momentum', label: 'RSI' },
  { key: 'disparity', label: 'C/P' },
  { key: 'event_radar', label: 'Event' },
];

const tier = (sig: string) => (sig === 'BUY' ? 'buy' : sig === 'SELL' ? 'sell' : 'neutral');
const leanOf = (d: any) => (d.points > d.max / 2 + 0.3 ? 'pos'
  : d.points < d.max / 2 - 0.3 ? 'neg' : 'neu');

function Row({ r }: { r: any }) {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  return (
    <div className="ue-cardwrap">
      <button className={`ue-row sig-${tier(r.signal)}`} onClick={() => setOpen((v) => !v)}>
        <div className="ue-row-l">
          <span className="ue-sym">{r.symbol}</span>
          <span className="ue-co">{r.company || ''}</span>
        </div>
        <div className="ue-row-r">
          <span className={`cn-status-score ${tier(r.signal)}`}>{r.signal} · {r.score}</span>
          <ChevronRight size={15} className="ue-chev"
            style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }} />
        </div>
      </button>
      {open && (
        <div className={`ue-risk cn-status ${tier(r.signal)}`}>
          <div className="cn-params">
            {PARAMS.map((p) => {
              const d = (r.params || []).find((x: any) => x.name === p.key);
              if (!d) return null;
              return (
                <span key={p.key} className={`cn-chip ${leanOf(d)}`}
                  title={`${p.label}: ${d.points?.toFixed(1)} of ${d.max}`}>
                  {p.label} <b>{d.points?.toFixed(1)}</b>
                </span>
              );
            })}
          </div>
          <button className="cn-news-toggle" style={{ marginTop: 6 }}
            onClick={() => nav(`/grok-intraday/${r.symbol}`)}>Open full detail</button>
        </div>
      )}
    </div>
  );
}

export default function GrokIntradayPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const b = useApi<any>((s) => api2.grokIntradayBoard(s), [], { refreshMs: 60_000 });
  const d = b.data;
  const warming = !d || d.status === 'WARMING';
  const buyers = d?.buyers || [];
  const sellers = d?.sellers || [];
  const c = d?.counts || {};

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Zap size={20} /> Grok Intraday · Live Board</h1>
        <p>Short-term direction over the liquid universe (same names as Trdgo Stock), scored by the
          Grok 10-signal model: options flow, unusual activity, volume/PCR, trend, key levels, OI
          change, volatility, RSI, C/P and event radar. BUY ≥ 55 · SELL ≤ 45. Click a name for its
          signal breakdown.</p>
      </div>

      {warming ? (
        <div className="im2-board"><p className="im2-note">
          <Loader2 size={15} className="spin" /> Scoring the universe…</p></div>
      ) : (
        <div className="im2-board">
          <div className="im2-stats">
            <div className="im2-stat buy"><TrendingUp size={16} /><div>
              <div className="im2-stat-n">{c.buy ?? 0}</div><div className="im2-stat-l">BUY</div></div></div>
            <div className="im2-stat sell"><TrendingDown size={16} /><div>
              <div className="im2-stat-n">{c.sell ?? 0}</div><div className="im2-stat-l">SELL</div></div></div>
            <div className="im2-stat"><div>
              <div className="im2-stat-n">{c.neutral ?? 0}</div><div className="im2-stat-l">NEUTRAL</div></div></div>
            <div className="im2-health"><span className="ok">Scored {d.scored}/{d.universe}</span></div>
          </div>

          <h3 className="im2-h">Top Buyers</h3>
          <div className="ue-grid">
            {buyers.length ? buyers.map((r: any) => <Row key={r.symbol} r={r} />)
              : <p className="im2-note">No buy signals right now.</p>}
          </div>
          <h3 className="im2-h">Top Sellers</h3>
          <div className="ue-grid">
            {sellers.length ? sellers.map((r: any) => <Row key={r.symbol} r={r} />)
              : <p className="im2-note">No sell signals right now.</p>}
          </div>
          <p className="im2-foot">Live short-term lean from options flow + technicals. Not advice.</p>
        </div>
      )}
    </div>
  );
}
