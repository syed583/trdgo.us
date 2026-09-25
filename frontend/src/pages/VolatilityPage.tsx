import React from 'react';
import {
  CartesianGrid, ComposedChart, Line, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState, Unavailable } from './shared';
import { Panel } from '../components/common';

const IV = '#a855f7';       // implied — purple, matching the provider
const RV = '#e0b341';       // realized — amber
const RANK = '#21d07a';     // IV rank — green
const MOVE = '#3b82f6';     // implied move — blue

function pct(v: number | null | undefined): string {
  return typeof v === 'number' ? `${v.toFixed(1)}%` : '--';
}

function Stat({ label, value, tone }: {
  label: string; value: string; tone?: 'up' | 'down';
}) {
  return (
    <div className="vol-stat">
      <span className="vol-stat-k">{label}</span>
      <span className={`vol-stat-v ${tone || ''}`}>{value}</span>
    </div>
  );
}

export default function VolatilityPage({ ctx }: { ctx: PageContext }) {
  const symbol = ctx.symbol;
  const vol = useApi<any>((s) => api2.volatility(symbol, s), [symbol]);
  const d = vol.data;

  return (
    <div className="page">
      <PageHead
        title={`Volatility · ${symbol}`}
        subtitle="Implied vs realized, the IV rank, and the term structure across every expiry — from Unusual Whales."
      />

      {vol.error ? <ErrorState error={vol.error} />
        : vol.initialLoading || !d ? <Loading />
          : d.status !== 'OK' ? <Unavailable status={d.status} detail={d.detail} />
            : (
              <>
                <Panel title="Stats" noBody>
                  <div className="vol-stats">
                    <Stat label="IV Rank" value={d.stats.iv_rank != null ? d.stats.iv_rank.toFixed(1) : '--'} />
                    <Stat label="Implied Volatility" value={pct(d.stats.iv)} />
                    <Stat label="Realized Volatility" value={pct(d.stats.rv)} />
                    <Stat label="Variance Risk Premium"
                      value={pct(d.stats.vrp)}
                      tone={d.stats.vrp > 0 ? 'up' : d.stats.vrp < 0 ? 'down' : undefined} />
                    <Stat label="Implied Move (front)"
                      value={d.stats.implied_move_pct != null
                        ? `±${d.stats.implied_move_pct.toFixed(2)}%${
                          d.stats.implied_move_dollars != null
                            ? ` ($${d.stats.implied_move_dollars.toFixed(2)})` : ''}`
                        : '--'} />
                    <Stat label="IV 52w High" value={pct(d.stats.iv_high)} />
                    <Stat label="IV 52w Low" value={pct(d.stats.iv_low)} />
                    <Stat label="RV 52w High" value={pct(d.stats.rv_high)} />
                    <Stat label="RV 52w Low" value={pct(d.stats.rv_low)} />
                  </div>
                </Panel>

                <div className="two-col">
                  <Panel title="IV vs Realized Vol & IV Rank">
                    {d.iv_rv_series?.length ? (
                      <ResponsiveContainer width="100%" height={300}>
                        <ComposedChart data={d.iv_rv_series}
                          margin={{ top: 8, right: 8, bottom: 4, left: -8 }}>
                          <CartesianGrid stroke="var(--border-2)" vertical={false} />
                          <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--text-mute)' }}
                            minTickGap={48} />
                          <YAxis yAxisId="v" tick={{ fontSize: 10, fill: 'var(--text-mute)' }}
                            width={40} tickFormatter={(v) => `${v}%`} />
                          <YAxis yAxisId="r" orientation="right" domain={[0, 100]}
                            tick={{ fontSize: 10, fill: 'var(--text-mute)' }} width={30} />
                          <Tooltip
                            contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', fontSize: 12 }}
                            formatter={(v: any, n: any) => [n === 'IV Rank' ? Number(v).toFixed(1) : `${Number(v).toFixed(1)}%`, n]} />
                          <Line yAxisId="v" dataKey="iv" name="Implied Vol" stroke={IV}
                            dot={false} strokeWidth={1.6} isAnimationActive={false} connectNulls />
                          <Line yAxisId="v" dataKey="rv" name="Realized Vol" stroke={RV}
                            dot={false} strokeWidth={1.6} isAnimationActive={false} connectNulls />
                          <Line yAxisId="r" dataKey="iv_rank" name="IV Rank" stroke={RANK}
                            dot={false} strokeWidth={1.2} strokeDasharray="3 3"
                            isAnimationActive={false} connectNulls />
                        </ComposedChart>
                      </ResponsiveContainer>
                    ) : <Unavailable status="NO_DATA" compact />}
                    <div className="vol-legend">
                      <span><i style={{ background: IV }} /> Implied Vol</span>
                      <span><i style={{ background: RV }} /> Realized Vol</span>
                      <span><i style={{ background: RANK }} /> IV Rank (right)</span>
                    </div>
                  </Panel>

                  <Panel title="Volatility Term Structure">
                    {d.term_structure?.length ? (
                      <ResponsiveContainer width="100%" height={300}>
                        <ComposedChart data={d.term_structure}
                          margin={{ top: 8, right: 8, bottom: 4, left: -8 }}>
                          <CartesianGrid stroke="var(--border-2)" vertical={false} />
                          <XAxis dataKey="dte" tick={{ fontSize: 10, fill: 'var(--text-mute)' }}
                            tickFormatter={(v) => `${v}d`} minTickGap={28} />
                          <YAxis yAxisId="v" tick={{ fontSize: 10, fill: 'var(--text-mute)' }}
                            width={40} tickFormatter={(v) => `${v}%`} />
                          <YAxis yAxisId="m" orientation="right"
                            tick={{ fontSize: 10, fill: 'var(--text-mute)' }} width={36}
                            tickFormatter={(v) => `${v}%`} />
                          <Tooltip
                            contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', fontSize: 12 }}
                            labelFormatter={(v) => `${v} days out`}
                            formatter={(v: any, n: any) => [`${Number(v).toFixed(2)}%`, n]} />
                          <Line yAxisId="v" dataKey="iv" name="Implied Vol" stroke={IV}
                            dot={{ r: 2 }} strokeWidth={1.6} isAnimationActive={false} connectNulls />
                          <Line yAxisId="m" dataKey="implied_move_pct" name="Implied Move" stroke={MOVE}
                            dot={{ r: 2 }} strokeWidth={1.4} strokeDasharray="4 3"
                            isAnimationActive={false} connectNulls />
                        </ComposedChart>
                      </ResponsiveContainer>
                    ) : <Unavailable status="NO_DATA" compact />}
                    <div className="vol-legend">
                      <span><i style={{ background: IV }} /> Implied Vol by expiry</span>
                      <span><i style={{ background: MOVE }} /> Implied Move %</span>
                    </div>
                  </Panel>
                </div>

                {d.detail && <div className="hint">{d.detail}</div>}
              </>
            )}
    </div>
  );
}
