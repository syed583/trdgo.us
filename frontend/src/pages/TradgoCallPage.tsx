import { useNavigate } from 'react-router-dom';
import { AlertTriangle } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
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

export default function TradgoCallPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const navigate = useNavigate();
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

      {demo ? (
        <div className="es-empty">Disabled in demo mode.</div>
      ) : q.initialLoading ? (
        <div className="es-empty">Computing the Tradgo Call…</div>
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

            {r.earnings_gate && (
              <div className="tc-gate">
                <AlertTriangle size={15} />
                <span>Earnings in <b>{r.earnings_gate.days_to}d</b> ({r.earnings_gate.label
                  || r.earnings_gate.date}). {r.earnings_gate.note}{' '}
                  <button className="tc-gate-link" onClick={() => navigate(`/earnings-trade/${symbol}${ctx.search}`)}>
                    Open pre-earnings model →
                  </button></span>
              </div>
            )}

            <h3 className="es-sub">Market &amp; Price (80 pts)</h3>
            <div className="es-params">{(r.market_params || []).map((p: any) => <Param key={p.name} p={p} />)}</div>

            <h3 className="es-sub">Company &amp; Ownership (20 pts)</h3>
            <div className="es-params">{(r.company_params || []).map((p: any) => <Param key={p.name} p={p} />)}</div>

            {(r.volatility?.implied_volatility || r.volatility?.expected_move) && (
              <>
                <h3 className="es-sub">Volatility — sizing only (no directional vote)</h3>
                <div className="tc-vol">
                  {r.volatility.implied_volatility && <div>{r.volatility.implied_volatility}</div>}
                  {r.volatility.expected_move && <div>{r.volatility.expected_move}</div>}
                </div>
              </>
            )}

            <div className="es-foot">{r.note}</div>
          </div>
        </>
      )}
    </div>
  );
}
