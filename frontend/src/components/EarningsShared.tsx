import { useState } from 'react';
import { AlertTriangle, Info, ExternalLink } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { ScoreGauge } from './common';
import { safeHref } from '../lib/format';
import './earnings-trade.css';
import '../pages/earnings-trade-screen.css';   // ets-alerts-card styling

// Compact "News & Risk Alerts" card -- the exact card from the earnings / Claude
// New detail (high-impact red/amber only, "N alerts" badge). Used on the detail
// pages AND the Claude New list dropdown so they show the same thing.
export function RiskAlertsCard({ symbol, demo }: { symbol: string; demo?: boolean }) {
  const a = useApi<any>((s) => (demo ? Promise.resolve(null) : api2.earningsAlerts(symbol, s)),
    [symbol, demo], { refreshMs: demo ? undefined : 120_000 });
  const hi = (a.data?.alerts || []).filter((x: any) => x.severity !== 'GREY');
  const n = (a.data?.counts?.RED || 0) + (a.data?.counts?.AMBER || 0);
  return (
    <div className={`ets-card ets-alerts-card ${n > 0 ? 'has-alerts' : ''}`}>
      <div className="ets-card-h">
        <span><AlertTriangle size={15} /> News &amp; Risk Alerts</span>
        {a.data?.counts && <span className="ets-badge alert-count">{n} alerts</span>}
      </div>
      <div className="ets-alerts">
        {a.initialLoading ? <div className="ets-empty-sm">Loading…</div>
          : hi.map((x: any, i: number) => (
            <div key={i} className="ets-alert">
              <span className={`ets-alert-tag ${x.severity === 'RED' ? 'red' : 'amber'}`}>
                {x.severity === 'RED' ? 'High' : 'Medium'}</span>
              <div>
                <div className="ets-alert-t">{x.title}</div>
                <div className="ets-alert-d">{x.date || x.source || ''}</div>
              </div>
            </div>
          ))}
        {!a.initialLoading && !hi.length && <div className="ets-empty-sm">No high-impact alerts.</div>}
      </div>
    </div>
  );
}

const DECISION_TONE: Record<string, string> = {
  BUY: 'buy', 'STRONG BUY': 'buy', SELL: 'sell', 'STRONG SELL': 'sell',
  STRADDLE: 'buy', STRANGLE: 'buy', 'NO TRADE': 'flat',
};

export function ScoreHeader({ r }: { r: any }) {
  const score = r?.score as number | null;
  const tone = DECISION_TONE[r?.decision] || 'flat';
  const color = score == null ? 'var(--text-mute)'
    : score >= 58 ? 'var(--green)' : score <= 42 ? 'var(--red)' : 'var(--amber)';
  return (
    <div className="es-head">
      <div className="es-gauge">
        <ScoreGauge value={score ?? 0} color={color} size={108} />
        <div className="es-gauge-num" style={{ color }}>
          {score == null ? '--' : Math.round(score)}
          <span className="es-gauge-max">/100</span>
        </div>
      </div>
      <div className="es-head-mid">
        <div className={`es-decision ${tone}`}>{r?.decision || '--'}</div>
        <div className="es-chips">
          <span className="es-chip">Coverage {r?.coverage_pct ?? 0}%</span>
          <span className="es-chip">Confidence {r?.confidence ?? 0}</span>
          <span className="es-chip">
            Evidence {r?.evidence_present ?? 0}/{r?.evidence_possible ?? 100}
          </span>
        </div>
        {!!(r?.reasons || []).length && (
          <ul className="es-reasons">
            {r.reasons.map((x: string, i: number) => <li key={i}>{x}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

export function ParamList({ params, pos = 'BUY', neg = 'SELL' }:
  { params: any[]; pos?: string; neg?: string }) {
  return (
    <div className="es-params">
      <div className="es-legend">
        <span><i className="es-legend-dot pos" /> + pushes toward {pos}</span>
        <span><i className="es-legend-dot neg" /> − pushes toward {neg}</span>
        <span><i className="es-legend-dot neu" /> near 0 = neutral</span>
        <span><i className="es-legend-bar" /> bar length = strength</span>
        <span className="es-legend-na">“no data” = excluded from the score</span>
      </div>
      {(params || []).map((p) => {
        const pts = p.points || 0;
        const frac = p.weight ? Math.min(1, Math.abs(pts) / p.weight) : 0;
        const tone = !p.available ? 'na'
          : p.leaning === 'Bullish' ? 'pos'
            : p.leaning === 'Bearish' ? 'neg' : 'neu';
        return (
          <div key={p.name} className={`es-param ${tone}`}>
            <div className="es-param-top">
              <span className="es-param-label">{p.label}</span>
              <span className="es-param-pts">{p.points_label}</span>
            </div>
            <div className="es-param-bar">
              <div className="es-param-fill" style={{ width: `${frac * 100}%` }} />
            </div>
            <div className="es-param-detail">
              {p.available ? p.detail
                : <span className="es-na">{p.unavailable_reason || 'No data'}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const SEV_TONE: Record<string, string> = { RED: 'red', AMBER: 'amber', GREY: 'grey' };

export function CorporateAlerts({ symbol, demo, alertsOnly = false }:
  { symbol: string; demo?: boolean; alertsOnly?: boolean }) {
  const [open, setOpen] = useState(true);
  const a = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.earningsAlerts(symbol, s)),
    [symbol, demo],
    { refreshMs: demo ? undefined : 120_000 },
  );
  const all = a.data?.alerts || [];
  const counts = a.data?.counts || { RED: 0, AMBER: 0, GREY: 0 };
  // alertsOnly: show only the real risk alerts (red/amber); the grey general
  // news (dividends, routine filings) stays in the full detail view.
  const alerts = alertsOnly ? all.filter((x: any) => x.severity === 'RED' || x.severity === 'AMBER') : all;

  return (
    <div className="es-alerts">
      <button className="es-alerts-head" onClick={() => setOpen((v) => !v)}>
        <AlertTriangle size={15} />
        <b>{alertsOnly ? 'Risk Alerts' : 'Corporate News & Risk Alerts'}</b>
        <span className="es-sev red">{counts.RED} red</span>
        <span className="es-sev amber">{counts.AMBER} amber</span>
        {!alertsOnly && <span className="es-sev grey">{counts.GREY} grey</span>}
        <span className="es-alerts-toggle">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <div className="es-alerts-body">
          <div className="es-alerts-note">
            <Info size={12} /> Shared, unscored — these never change the score.
            Public info only; assess materiality and freshness.
          </div>
          {demo ? (
            <div className="es-empty">Alerts are disabled in demo mode.</div>
          ) : a.initialLoading ? (
            <div className="es-empty">Loading alerts…</div>
          ) : !alerts.length ? (
            <div className="es-empty">{alertsOnly
              ? 'No risk alerts (red/amber). See the stock detail for all corporate news.'
              : 'No notable corporate events or risk headlines.'}</div>
          ) : (
            alerts.map((al: any, i: number) => (
              <div key={i} className={`es-alert ${SEV_TONE[al.severity] || 'grey'}`}>
                <span className={`es-alert-dot ${SEV_TONE[al.severity] || 'grey'}`} />
                <div className="es-alert-body">
                  <div className="es-alert-title">
                    <span className="es-alert-cat">{al.category}</span>
                    {al.title}
                    {safeHref(al.url) && (
                      <a href={safeHref(al.url)} target="_blank" rel="noreferrer"
                        className="es-alert-link"><ExternalLink size={11} /></a>
                    )}
                  </div>
                  <div className="es-alert-detail">
                    {al.detail}
                    {al.date ? ` · ${al.date}` : ''}
                    {al.source ? ` · ${al.source}` : ''}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
