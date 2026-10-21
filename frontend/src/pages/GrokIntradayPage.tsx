import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Zap, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import '../components/intraday-v2.css';

// Grok Intraday -- a LIVE board over the liquid universe, same UI as Trdgo Stock
// (Intraday Model v2 board) but scored with the Grok 10-signal short-term model.

const money = (v: any) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);
const callClass = (c: string) => (c === 'BUY' ? 'im2-buy' : c === 'SELL' ? 'im2-sell' : 'im2-noedge');

function SignalTable({ rows, side, base }: { rows: any[]; side: 'BUY' | 'SELL'; base: string }) {
  const nav = useNavigate();
  if (!rows.length) {
    return <p className="im2-note">No {side === 'BUY' ? 'buy' : 'sell'} signals right now.</p>;
  }
  // List tickers A->Z (not by score) so the same name lines up across every board.
  const sorted = [...rows].sort((a, b) =>
    String(a.symbol).localeCompare(String(b.symbol)));
  return (
    <div className="im2-wrap">
      <table className="im2-tbl">
        <thead>
          <tr>
            <th>Symbol</th><th>Call</th><th className="n">Score</th><th className="n">Entry</th>
            <th className="n">Now</th><th className="n">TP</th><th className="n">SL</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.symbol} className="im2-rowlink" onClick={() => nav(`${base}/${r.symbol}`)}>
              <td className="sym">{r.symbol}</td>
              <td><span className={`im2-call ${callClass(r.signal)}`}>{r.signal}</span></td>
              <td className="n">{r.score ?? '—'}</td>
              <td className="n">{money(r.entry)}</td>
              <td className="n">{money(r.now)}</td>
              <td className="n">{money(r.tp)}</td>
              <td className="n">{money(r.sl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export type IntradayCfg = {
  title: string; subtitle: string; base: string;
  board: (s?: AbortSignal) => Promise<any>;
};
const GROK_CFG: IntradayCfg = {
  title: 'Grok Intraday', subtitle: 'Live Board · Grok 10-signal model',
  base: '/grok-intraday', board: (s) => api2.grokIntradayBoard(s),
};
export const META_INTRADAY_CFG: IntradayCfg = {
  title: 'Meta Intraday', subtitle: 'Live Board · Meta 8-signal model',
  base: '/meta-intraday', board: (s) => api2.metaIntradayBoard(s),
};
export const CLAUDE_PRO_INTRADAY_CFG: IntradayCfg = {
  title: 'Claude Pro Intraday', subtitle: 'Live Board · real 5-min engine · V2 8-factor model',
  base: '/claude-pro-intraday', board: (s) => api2.claudeProIntradayBoard(s),
};
export const KIMI_INTRADAY_CFG: IntradayCfg = {
  title: 'Kimi Intraday', subtitle: 'Live Board · Kimi 10-signal model',
  base: '/kimi-intraday', board: (s) => api2.kimiIntradayBoard(s),
};
export const GEMINI_INTRADAY_CFG: IntradayCfg = {
  title: 'Gemini Intraday', subtitle: 'Live Board · 80/20 execution & flow model',
  base: '/gemini-intraday', board: (s) => api2.geminiIntradayBoard(s),
};
export const CHATGPT_INTRADAY_CFG: IntradayCfg = {
  title: 'ChatGPT Intraday', subtitle: 'Live Board · 20-param institutional model · coverage %',
  base: '/chatgpt-intraday', board: (s) => api2.chatgptIntradayBoard(s),
};

export default function GrokIntradayPage({ ctx, cfg = GROK_CFG }:
  { ctx: PageContext; cfg?: IntradayCfg }) {
  void ctx;
  const [tab, setTab] = useState<'board'>('board');
  void tab; void setTab;
  const b = useApi<any>((s) => cfg.board(s), [cfg], { refreshMs: 60_000 });
  const d = b.data;
  const counts = d?.counts || {};
  const warming = d?.status === 'WARMING' || (b.initialLoading && !d);
  const neutral = [...(d?.neutral || [])].sort((a: any, b: any) =>
    String(a.symbol).localeCompare(String(b.symbol)));

  const Stat = ({ icon, label, n, cls }: any) => (
    <div className={`im2-stat ${cls}`}>{icon}<div><div className="im2-stat-n">{n ?? '—'}</div>
      <div className="im2-stat-l">{label}</div></div></div>
  );

  return (
    <div className="page">
      <div className="at-head">
        <div>
          <h1><Zap size={20} /> {cfg.title}</h1>
          <p className="at-basis at-basis-sm"><b>{cfg.subtitle}</b></p>
        </div>
      </div>

      <div className="im2-board">
        <div className="im2-health">
          <span>Scored <b>{d?.scored ?? '—'}/{d?.universe ?? '—'}</b> this scan</span>
          <span className="ok">Liquid universe · same names as Trdgo Stock</span>
        </div>

        <div className="im2-stats">
          <Stat icon={<TrendingUp size={16} />} label="BUY-rated" n={counts.buy} cls="buy" />
          <Stat icon={<TrendingDown size={16} />} label="SELL-rated" n={counts.sell} cls="sell" />
          <Stat icon={<Minus size={16} />} label="NEUTRAL" n={counts.neutral} cls="noedge" />
        </div>

        {warming ? (
          <p className="im2-note"><Loader2 size={12} className="spin" /> Scoring the universe…</p>
        ) : (
          <>
            <h3 className="im2-h">Buyers</h3>
            <SignalTable rows={d?.buyers || []} side="BUY" base={cfg.base} />
            <h3 className="im2-h">Sellers</h3>
            <SignalTable rows={d?.sellers || []} side="SELL" base={cfg.base} />
            <h3 className="im2-h">NEUTRAL</h3>
            <div className="im2-chips">
              {neutral.length ? neutral.map((r: any) =>
                <span key={r.symbol} className="im2-chip">{r.symbol} <em>{r.score}</em></span>)
                : <span className="im2-note">—</span>}
            </div>
            <p className="im2-foot">Short-term lean from options flow + technicals (10-signal Grok model).
              Entry = price, TP/SL from daily ATR (2:1). Read-only — not advice.</p>
          </>
        )}
      </div>
    </div>
  );
}
