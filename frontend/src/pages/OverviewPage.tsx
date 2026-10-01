import React from 'react';
import { TrendingDown, TrendingUp } from 'lucide-react';
import type { PageContext } from '../App';
import { api } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState, Unavailable } from './shared';
import { Panel } from '../components/common';
import { compact, compactMoney, money, num, signedPct } from '../lib/format';

/**
 * Ticker Overview: one page of everything on a stock.
 *
 * Phase 1 -- key stats, performance vs the index ETFs, analyst actions and
 * insider activity -- all from one aggregated request. The intraday options-
 * volume chart, the historical table and the daily GEX chart follow.
 */

const GREEN = 'var(--green)';
const RED = 'var(--red)';

function tone(v: number | null | undefined): string {
  if (typeof v !== 'number') return 'var(--text-mute)';
  return v > 0 ? GREEN : v < 0 ? RED : 'var(--text-mute)';
}

function Stat({ label, value, sub, valueColor }: {
  label: string; value: React.ReactNode; sub?: string; valueColor?: string;
}) {
  return (
    <div className="ov-stat">
      <span className="ov-stat-k">{label}</span>
      <span className="ov-stat-v" style={valueColor ? { color: valueColor } : undefined}>{value}</span>
      {sub && <span className="ov-stat-sub">{sub}</span>}
    </div>
  );
}

function KeyStats({ s }: { s: any }) {
  if (!s || s.status !== 'OK') return <Unavailable status={s?.status} compact />;
  return (
    <div className="ov-stats-grid">
      <Stat label="Price" value={money(s.price)}
        sub={s.change_percent != null ? signedPct(s.change_percent) : undefined}
        valueColor={tone(s.change_percent)} />
      <Stat label="Market cap" value={compactMoney(s.market_cap)} />
      <Stat label="Prev close" value={money(s.previous_close)} />
      <Stat label="Open" value={money(s.open)} />
      <Stat label="Day range" value={`${money(s.day_low)} – ${money(s.day_high)}`} />
      <Stat label="52-week range" value={`${money(s.week52_low)} – ${money(s.week52_high)}`} />
      <Stat label="Volume" value={compact(s.volume)} />
      <Stat label="Avg volume" value={compact(s.avg_volume)} />
      <Stat label="Put/Call ratio" value={s.put_call_volume != null ? num(s.put_call_volume) : '--'}
        valueColor={s.put_call_volume != null ? (s.put_call_volume < 1 ? GREEN : RED) : undefined} />
      <Stat label="Call volume" value={compact(s.call_volume)} />
      <Stat label="Put volume" value={compact(s.put_volume)} />
      <Stat label="Net premium" value={compactMoney(s.net_premium)}
        valueColor={tone(s.net_premium)} />
      <Stat label="Call premium" value={compactMoney(s.call_premium)} />
      <Stat label="Put premium" value={compactMoney(s.put_premium)} />
      <Stat label="Total OI" value={compact(s.total_oi)} />
    </div>
  );
}

function Performance({ p, symbol }: { p: any; symbol: string }) {
  if (!p || p.status !== 'OK') return <Unavailable status={p?.status} compact />;
  const periods: string[] = p.periods || [];
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Ticker</th>
            {periods.map((pd) => <th key={pd} className="r">{pd}</th>)}
          </tr>
        </thead>
        <tbody>
          {(p.rows || []).map((row: any) => (
            <tr key={row.symbol} className={row.symbol === symbol ? 'pr-subject' : ''}>
              <td><b>{row.symbol}</b></td>
              {periods.map((pd) => {
                const v = row.returns?.[pd];
                return (
                  <td key={pd} className="num r" style={{ color: tone(v) }}>
                    {v != null ? `${v > 0 ? '+' : ''}${v}%` : '--'}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Analysts({ a }: { a: any }) {
  const rows: any[] = a?.rows || [];
  if (!rows.length) return <Unavailable status={a?.status} detail="No recent analyst actions." compact />;
  const recTone = (rec: string) => {
    const r = (rec || '').toLowerCase();
    if (/(buy|outperform|overweight|positive)/.test(r)) return GREEN;
    if (/(sell|underperform|underweight|negative)/.test(r)) return RED;
    return 'var(--amber)';
  };
  return (
    <div className="table-wrap" style={{ maxHeight: 360, overflowY: 'auto' }}>
      <table className="tbl">
        <thead>
          <tr><th>Date</th><th>Firm</th><th className="r">Target</th><th>Rating</th></tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.firm}-${r.date}-${i}`}>
              <td className="num">{r.date || '--'}</td>
              <td>{r.firm || r.analyst || '--'}</td>
              <td className="num r">{r.target != null ? money(r.target) : '--'}</td>
              <td style={{ color: recTone(r.recommendation) }}>
                {r.recommendation || r.action || '--'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Insiders({ ins }: { ins: any }) {
  const rows: any[] = ins?.transactions || [];
  if (!rows.length) return <Unavailable status={ins?.status} detail="No recent insider filings." compact />;
  return (
    <>
      <div className="ov-ins-sum">
        <span className="pos">{ins.buy_count ?? 0} buys</span>
        <span className="neg">{ins.sell_count ?? 0} sells</span>
        {ins.net_value != null && (
          <span style={{ color: tone(ins.net_value) }}>
            net {compactMoney(ins.net_value)}
          </span>
        )}
      </div>
      <div className="table-wrap" style={{ maxHeight: 360, overflowY: 'auto' }}>
        <table className="tbl">
          <thead>
            <tr><th>Filed</th><th>Owner</th><th>Role</th><th>Action</th><th className="r">Shares</th></tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const buy = (r.acquired_disposed || r.direction || '').toString().toUpperCase().startsWith('A');
              return (
                <tr key={`${r.owner}-${r.filed}-${i}`}>
                  <td className="num">{r.filed || r.date || '--'}</td>
                  <td>{r.owner || '--'}</td>
                  <td className="mf-dim">{r.role || (r.is_officer ? 'Officer' : r.is_director ? 'Director' : '--')}</td>
                  <td style={{ color: buy ? GREEN : RED }}>{r.code_label || (buy ? 'Acquired' : 'Disposed')}</td>
                  <td className="num r">{r.shares != null ? compact(r.shares) : '--'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function OverviewPage({ ctx }: { ctx: PageContext }) {
  const symbol = ctx.symbol;
  const ov = useApi<any>((s) => api.tickerOverview(symbol, s), [symbol]);
  // Only trust data for the symbol on screen (avoid stale render on nav).
  const d = (ov.data && String(ov.data.symbol || '').toUpperCase()
    === String(symbol || '').toUpperCase()) ? ov.data : null;
  const ks = d?.key_stats;

  return (
    <div className="page">
      <PageHead
        title={`${symbol}${ks?.name && ks.name !== symbol ? ` · ${ks.name}` : ''}`}
        subtitle={ks?.sector
          ? `${ks.sector} — key stats, performance, analyst actions and insider activity.`
          : 'Key stats, performance, analyst actions and insider activity.'}
        right={ks?.price != null ? (
          <span className="an-price">
            <b>{money(ks.price)}</b>
            <em className={(ks.change_percent ?? 0) >= 0 ? 'pos' : 'neg'}>
              {(ks.change_percent ?? 0) >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {signedPct(ks.change_percent)}
            </em>
          </span>
        ) : undefined} />

      {ov.error ? <ErrorState error={ov.error} />
        : ov.initialLoading || !d ? <Loading />
          : (
            <>
              <Panel title="Key Stats" noBody><KeyStats s={ks} /></Panel>
              <Panel title="Performance vs Index ETFs" noBody>
                <Performance p={d.performance} symbol={symbol} />
              </Panel>
              <div className="two-col">
                <Panel title="Analyst Actions" noBody><Analysts a={d.analysts} /></Panel>
                <Panel title="Insider Activity" noBody><Insiders ins={d.insiders} /></Panel>
              </div>
              <div className="hint" style={{ padding: '2px 4px' }}>
                More panels coming: intraday options-volume chart, historical table
                and daily GEX. Data from Unusual Whales and SEC filings.
              </div>
            </>
          )}
    </div>
  );
}
