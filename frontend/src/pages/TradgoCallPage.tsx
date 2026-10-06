import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Search } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import TradgoScan from '../components/TradgoScan';
import '../components/earnings-trade.css';
import './tradgo-call.css';

function Param({ p }: { p: any }) {
  const tone = !p.available ? 'na'
    : p.leaning === 'Bullish' ? 'pos' : p.leaning === 'Bearish' ? 'neg' : 'neu';
  const frac = p.weight ? Math.min(1, Math.abs(p.points || 0) / p.weight) : 0;
  return (
    <div className={`es-param ${tone}`}>
      <div className="es-param-top">
        <span className="es-param-label">{p.label}</span>
        <span className="es-param-pts">{p.points_label}</span>
      </div>
      <div className="es-param-bar"><div className="es-param-fill" style={{ width: `${frac * 100}%` }} /></div>
      <div className="es-param-detail">
        {p.available ? p.detail : <span className="es-na">{p.detail || 'No data'}</span>}
      </div>
    </div>
  );
}

const TRY = ['AAPL', 'TSLA', 'MSFT', 'AMZN', 'META', 'NVDA'];

export default function TradgoCallPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const { symbol: pathSym } = useParams();
  const navigate = useNavigate();
  const [entry, setEntry] = useState('');
  const go = (sym: string) => {
    const t = (sym || '').trim().toUpperCase();
    if (t) navigate(`/tradgo-call/${encodeURIComponent(t)}${ctx.search}`);
  };

  // No ticker yet -> the hero landing to pick one (same UI as Analysis).
  if (!pathSym) {
    return (
      <div className="an-hero">
        <h1>Tradgo Call<br />Short-Term Direction</h1>
        <p className="an-tag">Buy / Sell / Neutral in one score, −100 to +100.</p>
        <form className="an-search" onSubmit={(e) => { e.preventDefault(); go(entry); }}>
          <Search size={15} />
          <input value={entry} onChange={(e) => setEntry(e.target.value.toUpperCase())}
            placeholder="Search ticker (e.g. NVDA)" aria-label="Ticker" spellCheck={false} autoFocus />
          <button type="submit" className="an-go" disabled={!entry.trim()}>Analyze</button>
        </form>
        <div className="an-try">Try:
          {TRY.map((t) => <button key={t} onClick={() => go(t)}>{t}</button>)}
        </div>
        <div className="an-stats">
          <div><b>18</b><span>Scored Parameters</span></div>
          <div><b>±100</b><span>Direction Score</span></div>
          <div><b>Days–4w</b><span>Swing Horizon</span></div>
          <div><b>Live</b><span>Prices &amp; Flow</span></div>
        </div>
      </div>
    );
  }
  const q = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.tradgoCall(symbol, s)),
    [symbol, demo],
    { refreshMs: demo ? undefined : 60_000 },
  );
  const r = q.data;
  const score: number | null = r?.score ?? null;
  const dec = r?.decision || '--';
  const tone = dec === 'BUY' ? 'buy' : dec === 'SELL' ? 'sell' : 'flat';
  // -100..+100 -> 0..100% for the needle position.
  const pct = score == null ? 50 : Math.max(0, Math.min(100, (score + 100) / 2));

  return (
    <div className="page es">
      <PageHead title={`Tradgo Call · ${symbol}`}
        subtitle={<>Short-term direction model (days to ~3–4 weeks), scored −100 to
          +100. Volatility is sizing only. <b>Research, not advice.</b></>} />

      <form className="tc-search" onSubmit={(e) => { e.preventDefault(); go(entry); }}>
        <Search size={15} />
        <input value={entry} onChange={(e) => setEntry(e.target.value)}
          placeholder="Enter a ticker (e.g. AAPL) to get its Buy / Sell call…"
          autoCapitalize="characters" spellCheck={false} />
        <button type="submit" className="tc-search-btn">Get call</button>
      </form>

      {demo ? (
        <div className="es-empty">Disabled in demo mode.</div>
      ) : q.initialLoading ? (
        <TradgoScan symbol={symbol} />
      ) : !r || r.status !== 'OK' ? (
        <div className="es-empty">{r?.detail || 'No call available for this stock.'}</div>
      ) : (
        <>
          <div className="es-card">
            <div className="tc-head">
              <div className="tc-score-wrap">
                <div className={`tc-score ${score != null && score >= 0 ? 'pos' : 'neg'}`}>
                  {score != null ? `${score > 0 ? '+' : ''}${score}` : '--'}
                </div>
                <div className="tc-scale">
                  <div className="tc-scale-bar"><span className="tc-needle" style={{ left: `${pct}%` }} /></div>
                  <div className="tc-scale-ends"><em>−100 Sell</em><em>0</em><em>+100 Buy</em></div>
                </div>
              </div>
              <div className="tc-head-mid">
                <div className={`es-decision ${tone}`}>{dec}</div>
                <div className="es-chips">
                  <span className="es-chip">Coverage {Math.round(r.coverage_pct)}%</span>
                  <span className="es-chip">{r.present}/{r.possible} pts live</span>
                </div>
              </div>
            </div>

            <h3 className="es-sub">Scored Parameters (100 pts)</h3>
            <div className="es-params">
              {(r.params || r.market_params || []).map((p: any) => <Param key={p.name} p={p} />)}
            </div>

            <div className="es-foot">{r.note}</div>
          </div>
        </>
      )}
    </div>
  );
}
