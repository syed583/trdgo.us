import React, { useState } from 'react';
import {
  CartesianGrid, ComposedChart, Line, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { ChevronRight, Info } from 'lucide-react';
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

const TERMS: { term: string; body: string }[] = [
  { term: 'Volatility',
    body: 'How much a stock moves, not which way. High volatility means big swings up or down; low means it drifts.' },
  { term: 'Implied Volatility (IV)',
    body: 'The movement the options market is pricing in for the future. It is baked into option prices — higher IV means options are more expensive because bigger moves are expected.' },
  { term: 'Realized Volatility (RV)',
    body: 'How much the stock has actually moved recently. This is history, measured from real price changes.' },
  { term: 'IV vs RV',
    body: 'The heart of it. When IV sits above RV, options are “rich” — the market is charging for more movement than the stock has delivered, which tends to favour option sellers. When IV is below RV, options look cheap.' },
  { term: 'IV Rank',
    body: 'Where today’s IV sits against its own past year, 0 to 100. IV Rank 80 means IV is near its yearly high; 20 means near its low. It answers “is this stock’s IV high for it?”, which a raw IV number cannot.' },
  { term: 'Variance Risk Premium (VRP)',
    body: 'IV minus RV. A positive VRP is the cushion option sellers are paid for taking on risk; a negative one means realized movement is outrunning what options priced.' },
  { term: 'Implied Move',
    body: 'The ± dollar and percent move the options market expects by a given expiry. ±0.91% ($3.05) means options are pricing roughly a three-dollar swing by that date, in either direction.' },
  { term: 'Term Structure',
    body: 'How implied volatility and the implied move change across expiries — near-dated vs further out. A rising curve means the market expects more movement the further ahead you look (often around an event).' },
];

function VolatilityExplainer() {
  const [open, setOpen] = useState(false);
  return (
    <div className={`vol-explain ${open ? 'open' : ''}`}>
      <button className="vol-explain-head" onClick={() => setOpen((v) => !v)}>
        <Info size={13} />
        What is volatility?
        <ChevronRight size={14} className="vol-explain-caret" />
      </button>
      {open && (
        <div className="vol-explain-body">
          {TERMS.map((t) => (
            <div className="vol-term" key={t.term}>
              <b>{t.term}</b>
              <span>{t.body}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const RANGES: { key: string; label: string; days: number }[] = [
  { key: '1M', label: '1 Month', days: 31 },
  { key: '3M', label: '3 Months', days: 93 },
  { key: '6M', label: '6 Months', days: 186 },
  { key: '1Y', label: '1 Year', days: 366 },
  { key: 'ALL', label: 'All', days: 100000 },
];

function sliceByRange(series: any[] | undefined, days: number): any[] {
  if (!series?.length) return [];
  if (days >= 100000) return series;
  // The series is daily and date-sorted ascending; keep rows on or after the
  // cutoff measured back from the most recent point (not "today", so a stale
  // feed still shows a full window).
  const last = series[series.length - 1]?.date;
  const end = last ? new Date(last) : new Date();
  const cutoff = new Date(end);
  cutoff.setDate(cutoff.getDate() - days);
  return series.filter((r) => r.date && new Date(r.date) >= cutoff);
}

export default function VolatilityPage({ ctx }: { ctx: PageContext }) {
  const symbol = ctx.symbol;
  const vol = useApi<any>((s) => api2.volatility(symbol, s), [symbol]);
  const d = vol.data;
  const [range, setRange] = useState('1Y');
  const ivrv = sliceByRange(d?.iv_rv_series, RANGES.find((r) => r.key === range)?.days ?? 366);

  return (
    <div className="page">
      <PageHead
        title={`Volatility · ${symbol}`}
        subtitle="Implied vs realized, the IV rank, and the term structure across every expiry — from Unusual Whales."
      />

      <VolatilityExplainer />

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
                    <div className="vol-range">
                      {RANGES.map((r) => (
                        <button key={r.key}
                          className={`vol-range-btn ${range === r.key ? 'active' : ''}`}
                          onClick={() => setRange(r.key)}>
                          {r.key === 'ALL' ? 'All' : r.key}
                        </button>
                      ))}
                    </div>
                    {ivrv.length ? (
                      <ResponsiveContainer width="100%" height={300}>
                        <ComposedChart data={ivrv}
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
