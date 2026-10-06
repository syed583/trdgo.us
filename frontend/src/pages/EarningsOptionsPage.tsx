import { Link, useParams, useNavigate } from 'react-router-dom';
import { ChevronLeft, ArrowLeft } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import { num } from '../lib/format';
import { ScoreHeader, ParamList, CorporateAlerts } from '../components/EarningsShared';
import UpcomingEarnings from '../components/UpcomingEarnings';
import '../components/earnings-trade.css';

export default function EarningsOptionsPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const { symbol: pathSym } = useParams();
  const navigate = useNavigate();
  const on = !!pathSym && !demo;

  const q = useApi<any>(
    (s) => (on ? api2.earningsOptions(symbol, s) : Promise.resolve(null)),
    [symbol, demo, pathSym],
    { refreshMs: on ? 60_000 : undefined, enabled: on },
  );
  const r = q.data;
  const c = r?.construction || {};
  const range = c.expected_range || {};

  if (!pathSym) {
    return (
      <div className="page es">
        <PageHead title="Earnings — Options"
          subtitle={<>Engine 2: a 100-point straddle/strangle earnings score. Pick
            a stock reporting soon. <b>Analysis, not advice.</b></>} />
        <UpcomingEarnings base="/earnings-options" title="Upcoming earnings"
          demo={demo} search={ctx.search} />
      </div>
    );
  }

  return (
    <div className="page es">
      <div className="es-backrow">
        <button type="button" className="es-back es-back-btn" onClick={() => navigate(-1)}>
          <ArrowLeft size={14} /> Back
        </button>
        <Link to={`/earnings-options${ctx.search}`} className="es-back">
          <ChevronLeft size={14} /> Upcoming earnings
        </Link>
      </div>
      <PageHead
        title={`Earnings — Options · ${symbol}`}
        subtitle={<>Engine 2: a 100-point evidence score for a long straddle /
          strangle into earnings — will the move beat the premium? <b>Analysis,
          not advice.</b></>}
      />

      {demo ? (
        <div className="es-empty">Disabled in demo mode.</div>
      ) : q.initialLoading ? (
        <div className="es-empty">Scoring the options setup…</div>
      ) : !r || r.status !== 'OK' ? (
        <div className="es-empty">{r?.detail || 'No options analysis available.'}</div>
      ) : (
        <>
          <div className="es-card">
            <ScoreHeader r={r} />
            <div className="es-trade">
              <div><span>Spot</span><b>{c.spot != null ? num(c.spot, 2) : '--'}</b></div>
              <div><span>Expected move</span><b>{c.expected_move_percent != null ? `±${num(c.expected_move_percent, 2)}%` : '--'}</b></div>
              <div><span>IV rank</span><b>{c.iv_rank != null ? `${num(c.iv_rank, 0)}%` : '--'}</b></div>
              <div><span>Realized vol</span><b>{c.realized_vol != null ? num(c.realized_vol, 1) : '--'}</b></div>
              <div><span>Var. risk premium</span><b>{c.variance_risk_premium != null ? num(c.variance_risk_premium, 2) : '--'}</b></div>
              <div><span>Expected range</span><b>{range.lower != null ? `${num(range.lower, 1)} – ${num(range.upper, 1)}` : '--'}</b></div>
              <div className="es-trade-wide"><span>Expiry</span><b>{c.expiry || '--'}</b></div>
              <div className="es-trade-wide"><span>Exit</span><b>{c.exit || '--'}</b></div>
              <div className="es-trade-wide"><span>Max premium at risk</span><b>{c.max_premium_at_risk || '--'}</b></div>
            </div>
            <h3 className="es-sub">Evidence breakdown (100 pts)</h3>
            <ParamList params={r.params} />
            <div className="es-foot">{r.note}</div>
          </div>

          <CorporateAlerts symbol={symbol} demo={demo} />
        </>
      )}
    </div>
  );
}
