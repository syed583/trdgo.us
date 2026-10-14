import { useEffect, useState } from 'react';
import { Loader2, Zap } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Table 2 — Unusual Activity (Part A) & Call/Put Disparity (Part B).
// Universe = the IV Crush universe (next session + next ~10 trading days).
// Read-only. Thresholds live in the backend settings, not here.

const mdd = (iso: any) => {
  if (!iso) return '—';
  try {
    return new Date(String(iso).slice(0, 10) + 'T12:00:00')
      .toLocaleDateString(undefined, { day: '2-digit', month: 'short' });
  } catch { return String(iso); }
};
const clock = (tz: string) => {
  try {
    return new Intl.DateTimeFormat(undefined,
      { hour: '2-digit', minute: '2-digit', timeZone: tz }).format(new Date());
  } catch { return '—'; }
};
// Compact dollars: $1.2M, $450K, $12,000.
const money = (v: any) => {
  if (v == null) return '—';
  const n = Number(v);
  if (!isFinite(n)) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
};
const num = (v: any) => (v == null ? '—' : Number(v).toLocaleString());
const x2 = (v: any) => (v == null ? '—' : `${Number(v).toFixed(2)}×`);
const pct = (v: any) => (v == null ? '—' : `${(Number(v) * 100).toFixed(0)}%`);

export default function UnusualActivityPage({ ctx }: { ctx: PageContext }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const resp = useApi<any>((s) => api2.unusualActivityScan(s), [],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  const partA: any[] = resp.data?.part_a || [];
  const partB: any[] = resp.data?.part_b || [];
  const universe: number = resp.data?.universe || 0;
  const loading = resp.initialLoading && !partA.length && !partB.length;

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Zap size={20} /> Unusual Activity</h1>
        <p>Unusual option prints and call/put premium skews across every name reporting in the
          next ~10 trading days ({universe || '…'} names). Read-only.
          <b> Now New York {clock('America/New_York')} · Dubai {clock('Asia/Dubai')}</b></p>
      </div>

      {/* Part A — Unusual Activity (one row per contract). */}
      <Panel title={<span className="at-col-title">Part A — Unusual Activity (which call / which put)</span>}>
        <div className="ivc-help">
          <b>What qualifies</b> — a single contract whose <b>today's volume ≥ 3× its 20-day average</b>,
          whose <b>volume ≥ open interest</b> (mostly new positions) and whose <b>premium ≥ $100K</b>.
          <b> Side</b> = BUY if ≥60% traded at the ask, SELL if ≥60% at the bid, else MIXED. Top 40 by premium.
        </div>
        {loading ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Loading…</p>
        ) : !partA.length ? (
          <p className="at-note">No contract in the universe meets all three filters right now.</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <th className="ts-sym">Ticker</th>
                  <th>Expiry</th>
                  <th className="ts-num">Strike</th>
                  <th>C/P</th>
                  <th className="ts-num">Volume<br /><span className="ivc-sub">(× 20-day avg)</span></th>
                  <th className="ts-num">Vol / OI</th>
                  <th className="ts-num">Premium</th>
                  <th>Side</th>
                  <th className="ts-num">Ask% / Bid%</th>
                </tr>
              </thead>
              <tbody>
                {partA.map((r, i) => (
                  <tr key={`${r.symbol}-${r.expiry}-${r.strike}-${r.right}-${i}`}>
                    <td className="ts-sym">{r.symbol}</td>
                    <td className="ts-date">{mdd(r.expiry)}</td>
                    <td className="ts-num">{r.strike ?? '—'}</td>
                    <td><span className={r.right === 'C' ? 'ua-call' : 'ua-put'}>
                      {r.right === 'C' ? 'CALL' : r.right === 'P' ? 'PUT' : '—'}</span></td>
                    <td className="ts-num">{num(r.volume)}<br />
                      <span className="ivc-sub">{r.vol_mult != null ? `${Number(r.vol_mult).toFixed(1)}×` : '—'}</span></td>
                    <td className="ts-num">{r.vol_oi != null ? `${Number(r.vol_oi).toFixed(1)}×` : '—'}</td>
                    <td className="ts-num"><b>{money(r.premium)}</b></td>
                    <td><span className={`ua-side ua-${(r.side || '').toLowerCase()}`}>{r.side}</span></td>
                    <td className="ts-num">{pct(r.ask_pct)} / {pct(r.bid_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {/* Part B — Call/Put Disparity (one row per stock). */}
      <Panel title={<span className="at-col-title">Part B — Call/Put Disparity</span>}>
        <div className="ivc-help">
          <b>How to read this</b> — the call/put <b>premium</b> ratio today vs its normal (20-session
          average). <b>Disparity</b> = today ÷ normal. <b>CALL HEAVY</b> at ≥2×, <b>PUT HEAVY</b> at ≤0.5×.
          Only skewed names are shown, most extreme first.
        </div>
        {loading ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Loading…</p>
        ) : !partB.length ? (
          <p className="at-note">No name in the universe is CALL HEAVY or PUT HEAVY right now
            {resp.data?.warm_syms?.length ? ' (still filling…)' : ''}.</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <th className="ts-sym">Ticker</th>
                  <th className="ts-num">Call premium</th>
                  <th className="ts-num">Put premium</th>
                  <th className="ts-num">Call/Put today</th>
                  <th className="ts-num">Normal (20d)</th>
                  <th className="ts-num">Disparity</th>
                  <th>Leaning</th>
                </tr>
              </thead>
              <tbody>
                {partB.map((r) => (
                  <tr key={r.symbol}>
                    <td className="ts-sym">{r.symbol}</td>
                    <td className="ts-num">{money(r.call_premium)}</td>
                    <td className="ts-num">{money(r.put_premium)}</td>
                    <td className="ts-num">{x2(r.ratio)}</td>
                    <td className="ts-num">{x2(r.normal_ratio)}</td>
                    <td className="ts-num"><b>{x2(r.disparity)}</b></td>
                    <td><span className={r.leaning === 'CALL HEAVY' ? 'ua-call' : 'ua-put'}>
                      {r.leaning}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="ts-foot">Premium = total $ traded today, straight from Unusual Whales. The
          normal ratio is the average over the last 20 sessions. Read-only — never places an order.</div>
      </Panel>
    </div>
  );
}
