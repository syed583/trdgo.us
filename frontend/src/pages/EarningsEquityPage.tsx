import { Link, useParams } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import { num } from '../lib/format';
import { ScoreHeader, ParamList, CorporateAlerts } from '../components/EarningsShared';
import UpcomingEarnings from '../components/UpcomingEarnings';
import '../components/earnings-trade.css';

export default function EarningsEquityPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const { symbol: pathSym } = useParams();

  // No ticker in the URL -> show the upcoming-earnings list to pick from.
  if (!pathSym) {
    return (
      <div className="page es">
        <PageHead title="Earnings — Equity"
          subtitle={<>Engine 1: a 100-point directional earnings score. Pick a
            stock reporting soon. <b>Analysis, not advice.</b></>} />
        <UpcomingEarnings base="/earnings-equity" title="Upcoming earnings"
          demo={demo} search={ctx.search} />
      </div>
    );
  }
  const q = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.earningsEquity(symbol, s)),
    [symbol, demo],
    { refreshMs: demo ? undefined : 60_000 },
  );
  const r = q.data;
  const t = r?.trade || {};

  return (
    <div className="page es">
      <Link to={`/earnings-equity${ctx.search}`} className="es-back">
        <ChevronLeft size={14} /> Upcoming earnings
      </Link>
      <PageHead
        title={`Earnings — Equity · ${symbol}`}
        subtitle={<>Engine 1: a 100-point evidence score for a directional stock
          trade into earnings. <b>Analysis, not advice.</b></>}
      />

      {demo ? (
        <div className="es-empty">Disabled in demo mode.</div>
      ) : q.initialLoading ? (
        <div className="es-empty">Scoring the earnings setup…</div>
      ) : !r || r.status !== 'OK' ? (
        <div className="es-empty">{r?.detail || 'No earnings analysis available.'}</div>
      ) : (
        <>
          <div className="es-card">
            <ScoreHeader r={r} />
            <div className="es-trade">
              <div><span>Entry reference</span><b>{t.entry_reference != null ? num(t.entry_reference, 2) : '--'}</b></div>
              <div><span>Expected move</span><b>{t.expected_move_percent != null ? `±${num(t.expected_move_percent, 2)}%` : '--'}</b></div>
              <div><span>Max planned risk</span><b>{t.max_planned_risk_percent != null ? `~${num(t.max_planned_risk_percent, 2)}%` : '--'}</b></div>
              <div className="es-trade-wide"><span>Planned exit</span><b>{t.planned_exit || '--'}</b></div>
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
