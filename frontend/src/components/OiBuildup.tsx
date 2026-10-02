import { useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, RefreshCw, Info } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api2 } from '../api/client';
import { compact, compactMoney, int, signedPct, strike as fmtStrike } from '../lib/format';
import './oi-buildup.css';

type Scope = 'market' | 'symbol';
type Side = 'ALL' | 'GAINERS' | 'LOSERS';

interface OiRow {
  option_symbol: string;
  symbol: string;
  expiry: string | null;
  strike: number | null;
  right: 'C' | 'P' | null;
  type: string | null;
  curr_oi: number | null;
  prev_oi: number | null;
  oi_diff: number | null;
  oi_change_pct: number | null;
  volume: number | null;
  vol_oi_ratio: number | null;
  premium: number | null;
  days_building: number | null;
  days_vol_over_oi: number | null;
}

/**
 * OI Build-Up: contracts whose open interest moved most since the prior session
 * — positions opened and held overnight, not intraday churn. Market-wide or for
 * the symbol in context. Open interest updates once per trading day (~6:45am ET).
 */
export default function OiBuildup({
  symbol, demo, onContract,
}: {
  symbol: string;
  demo?: boolean;
  onContract?: (occ: string) => void;
}) {
  const [scope, setScope] = useState<Scope>('market');
  const [side, setSide] = useState<Side>('GAINERS');

  const sym = (symbol || '').toUpperCase();
  const q = scope === 'symbol' ? sym : '';

  const data = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.oiChange(q, 100, s)),
    [q, demo],
  );

  const rows: OiRow[] = useMemo(() => {
    const all: OiRow[] = data.data?.rows || [];
    const picked = side === 'GAINERS'
      ? all.filter((r) => (r.oi_diff || 0) > 0)
      : side === 'LOSERS'
        ? all.filter((r) => (r.oi_diff || 0) < 0)
        : all;
    return picked.slice(0, 60);
  }, [data.data, side]);

  const summary = data.data?.summary;
  const status = data.data?.status;

  return (
    <div className="page oib">
      <div className="mf-head">
        <div>
          <h1>OI Build-Up</h1>
          <p>
            Contracts whose open interest changed most since the prior session —
            positions opened and <b>held overnight</b>, which separates real
            accumulation from intraday churn.
          </p>
        </div>
        <div className="mf-head-right">
          {data.data?.as_of && (
            <span className="mf-head-stamp">As of {data.data.as_of}</span>
          )}
          <button className="btn-icon" onClick={() => data.refresh()}
            aria-label="Refresh" title="Refresh">
            <RefreshCw size={15} className={data.loading ? 'oib-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="oib-note">
        <Info size={13} />
        Open interest updates <b>once per trading day</b> (~6:45am ET) — this is
        not an intraday metric.
      </div>

      <div className="oib-controls">
        <div className="mf-mode" role="tablist">
          <button className={`mf-mode-btn ${scope === 'market' ? 'active' : ''}`}
            onClick={() => setScope('market')}>Market-wide</button>
          <button className={`mf-mode-btn ${scope === 'symbol' ? 'active' : ''}`}
            onClick={() => setScope('symbol')}>{sym}</button>
        </div>
        <div className="oib-side">
          {(['GAINERS', 'LOSERS', 'ALL'] as Side[]).map((sName) => (
            <button key={sName}
              className={`oib-side-btn ${side === sName ? 'active' : ''}`}
              onClick={() => setSide(sName)}>
              {sName === 'GAINERS' ? 'Biggest increases'
                : sName === 'LOSERS' ? 'Biggest decreases' : 'All'}
            </button>
          ))}
        </div>
      </div>

      {summary && (
        <div className="oib-cards">
          <div className="oib-card">
            <span className="oib-card-l">Contracts moved</span>
            <span className="oib-card-v">{int(summary.count)}</span>
          </div>
          <div className="oib-card">
            <span className="oib-card-l">Gaining OI</span>
            <span className="oib-card-v pos">{int(summary.gainers)}</span>
          </div>
          <div className="oib-card">
            <span className="oib-card-l">Losing OI</span>
            <span className="oib-card-v neg">{int(summary.losers)}</span>
          </div>
          <div className="oib-card">
            <span className="oib-card-l">Side being built</span>
            <span className={`oib-card-v ${summary.building === 'CALLS' ? 'pos'
              : summary.building === 'PUTS' ? 'neg' : ''}`}>
              {summary.building}
            </span>
          </div>
          <div className="oib-card">
            <span className="oib-card-l">Call OI added</span>
            <span className="oib-card-v">{compact(summary.call_oi_added)}</span>
          </div>
          <div className="oib-card">
            <span className="oib-card-l">Put OI added</span>
            <span className="oib-card-v">{compact(summary.put_oi_added)}</span>
          </div>
        </div>
      )}

      {demo ? (
        <div className="oib-empty">OI build-up is disabled in demo mode.</div>
      ) : data.initialLoading ? (
        <div className="oib-empty">Loading overnight OI changes…</div>
      ) : status !== 'OK' || !rows.length ? (
        <div className="oib-empty">
          {data.data?.detail || 'No OI change data for this scope yet. '
            + 'The feed refreshes in the premarket (~6:45am ET).'}
        </div>
      ) : (
        <div className="oib-table-wrap">
          <table className="oib-table">
            <thead>
              <tr>
                <th>Contract</th>
                <th className="r">Prev OI</th>
                <th className="r">Curr OI</th>
                <th className="r">Δ OI</th>
                <th className="r">% Chg</th>
                <th className="r">Volume</th>
                <th className="r">Vol/OI</th>
                <th className="r">Premium</th>
                <th className="r">Streak</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const up = (r.oi_diff || 0) >= 0;
                return (
                  <tr key={r.option_symbol}
                    className="oib-row"
                    onClick={() => onContract?.(r.option_symbol)}>
                    <td>
                      <span className="oib-sym">{r.symbol}</span>
                      <span className={`oib-cp ${r.right === 'C' ? 'call' : 'put'}`}>
                        {fmtStrike(r.strike)}{r.right}
                      </span>
                      <span className="oib-exp">{r.expiry}</span>
                    </td>
                    <td className="r">{compact(r.prev_oi)}</td>
                    <td className="r">{compact(r.curr_oi)}</td>
                    <td className={`r ${up ? 'pos' : 'neg'}`}>
                      {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                      {' '}{compact(Math.abs(r.oi_diff || 0))}
                    </td>
                    <td className={`r ${up ? 'pos' : 'neg'}`}>
                      {signedPct(r.oi_change_pct, 0)}
                    </td>
                    <td className="r">{compact(r.volume)}</td>
                    <td className={`r ${(r.vol_oi_ratio || 0) >= 1 ? 'hot' : ''}`}>
                      {r.vol_oi_ratio != null ? r.vol_oi_ratio.toFixed(2) : '--'}
                    </td>
                    <td className="r">{compactMoney(r.premium)}</td>
                    <td className="r">
                      {r.days_building ? `${r.days_building}d` : '--'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="oib-foot">
            Vol/OI above 1 means more traded today than were already open — fresh
            positioning. “Streak” is consecutive sessions of rising OI (market
            scope). Click a row for the contract's detail.
          </p>
        </div>
      )}
    </div>
  );
}
