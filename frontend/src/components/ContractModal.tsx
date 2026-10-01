import React, { useMemo, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { X } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { money, num } from '../lib/format';

const GREEN = '#22a06b';
const RED = '#e5484d';
const MID = '#8a94a6';

const TABS = ['Summary', 'Analysis', 'Time & Sales', 'Volume', 'History'] as const;
type Tab = typeof TABS[number];

function premium(v: number | null | undefined): string {
  if (v == null) return '--';
  const abs = Math.abs(v); const sign = v < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function clock(iso: string | null | undefined): string {
  if (!iso) return '--';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

/** The detail for one option contract: summary, analysis, prints, volume, history. */
export function ContractModal({ occ, onClose }: { occ: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('Summary');
  const detail = useApi<any>((s) => api2.optionsContract(occ, s), [occ]);
  const d = detail.data;
  const m = d?.meta || {};

  const right = m.right === 'P' ? 'PUT' : 'CALL';
  const title = `${m.symbol || ''} $${m.strike ?? ''} ${m.right || ''} ${m.expiry || ''}`;

  return (
    <div className="cm-overlay" onClick={onClose}>
      <div className="cm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="cm-head">
          <div>
            <div className="cm-title">
              <span className={`badge ${m.right === 'C' ? 'green' : 'red'}`}>{right}</span>
              {title}
              {m.moneyness && (
                <span className={`badge ${m.moneyness === 'ITM' ? 'green' : ''}`}
                  title="Moneyness of the strike vs the underlying">
                  {m.moneyness}{m.moneyness_pct != null ? ` ${m.moneyness_pct > 0 ? '+' : ''}${m.moneyness_pct}%` : ''}
                </span>
              )}
            </div>
            {d?.status === 'OK' && (
              <div className="cm-sub">
                Spot {money(m.spot)} · Last {money(m.last_price)} ·
                {' '}Bid/Ask {num(m.bid)}/{num(m.ask)} · IV {m.iv_percent ?? '--'}% ·
                {' '}OI {m.open_interest != null ? Math.round(m.open_interest).toLocaleString() : '--'} ·
                {' '}{m.dte}d
                <br />
                Δ {m.delta != null ? m.delta.toFixed(3) : '--'} ·
                {' '}Γ {m.gamma != null ? m.gamma.toFixed(4) : '--'} ·
                {' '}Θ {m.theta != null ? m.theta.toFixed(3) : '--'}/day ·
                {' '}ν {m.vega != null ? m.vega.toFixed(3) : '--'}
              </div>
            )}
          </div>
          <button className="cm-close" onClick={onClose}><X size={18} /></button>
        </div>

        <div className="cm-tabs">
          {TABS.map((t) => (
            <button key={t} className={`cm-tab ${tab === t ? 'active' : ''}`}
              onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>

        <div className="cm-body">
          {detail.initialLoading && <div className="cm-note">Loading contract…</div>}
          {d && d.status !== 'OK' && (
            <div className="cm-note">
              {d.status === 'INVALID_CONTRACT'
                ? 'This print has no contract symbol to open.'
                : 'No detail available for this contract.'}
            </div>
          )}
          {d && d.status === 'OK' && (
            <>
              {tab === 'Summary' && <SummaryTab summary={d.summary} history={d.history} />}
              {tab === 'Analysis' && <AnalysisTab a={d.analysis} />}
              {tab === 'Time & Sales' && <TimeSalesTab ts={d.time_sales} />}
              {tab === 'Volume' && <VolumeTab vp={d.volume_profile} />}
              {tab === 'History' && <HistoryTab h={d.history} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function SummaryTab({ summary, history }: { summary: any; history: any }) {
  const bars = summary?.bars || [];
  const hist = history?.rows || [];

  // Intraday is the richer view when the contract actually printed through the
  // session. A thin contract trades a handful of times -- one or two minute
  // bars, which draw as a lonely dot that reads as "no data". When intraday is
  // that sparse, fall back to the daily close/volume series so the chart always
  // shows a real line for any contract with a trading history.
  const useIntraday = bars.length >= 3;

  const data = useIntraday
    ? bars.map((b: any) => ({
        t: clock(b.time), close: b.close,
        ask: b.volume_ask, bid: b.volume_bid, mid: b.volume_mid,
      }))
    : [...hist].reverse().map((r: any) => ({
        t: r.date, close: r.last_price, vol: r.volume,
      }));

  const priceLabel = useIntraday ? 'Contract price (intraday)'
    : 'Contract price (daily close)';
  const volLabel = useIntraday ? 'Volume by fill (bid / mid / ask)'
    : 'Daily volume';

  if (!data.length) {
    return <div className="cm-note">No price history for this contract yet.</div>;
  }

  return (
    <>
      {!useIntraday && (
        <div className="cm-hint" style={{ marginBottom: 8 }}>
          {bars.length
            ? `This contract printed only ${bars.length} time(s) today, so the chart shows its daily history.`
            : 'No intraday prints today, so the chart shows this contract’s daily history.'}
        </div>
      )}
      <div className="cm-grid2">
        <div className="cm-card">
          <div className="cm-card-h">{priceLabel}</div>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={data} margin={{ top: 6, right: 10, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="t" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 9 }} domain={['auto', 'auto']} width={44}
                tickFormatter={(v) => `$${v}`} />
              <Tooltip formatter={(v: any) => `$${v}`} />
              <Line type="monotone" dataKey="close" stroke="#2bb3c0"
                dot={data.length <= 5} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="cm-card">
          <div className="cm-card-h">{volLabel}</div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data} margin={{ top: 6, right: 10, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="t" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 9 }} width={44} />
              <Tooltip />
              {useIntraday ? (
                <>
                  <Bar dataKey="bid" stackId="v" fill="#e08a00" maxBarSize={46} />
                  <Bar dataKey="mid" stackId="v" fill={MID} maxBarSize={46} />
                  <Bar dataKey="ask" stackId="v" fill={GREEN} maxBarSize={46} />
                </>
              ) : (
                <Bar dataKey="vol" fill="#2bb3c0" maxBarSize={28} />
              )}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </>
  );
}

function AnalysisTab({ a }: { a: any }) {
  if (!a || a.status !== 'OK') {
    return <div className="cm-note">{a?.detail || 'Not enough live data to model this contract.'}</div>;
  }
  const pop = a.probability_of_profit;
  return (
    <div>
      {a.verdict && (
        <div className="cm-verdict">
          <span className="cm-verdict-tag">The read</span>
          {a.verdict}
        </div>
      )}
      <div className="cm-card" style={{ marginBottom: 12 }}>
        <div className="cm-card-h">Probability of Profit (held to expiry)</div>
        <div className="cm-pop">{pop}%</div>
        <div className="cm-popbar"><span style={{ left: `${Math.max(0, Math.min(100, pop))}%` }} /></div>
        <div className="cm-pop-scale"><span>0%</span><span>50%</span><span>100%</span></div>
      </div>
      <div className="cm-grid2">
        <div className="cm-card">
          <div className="cm-card-h">Kelly position sizing</div>
          <div className=" cm-metrics">
            <Metric label="Full Kelly" value={`${a.full_kelly}%`} />
            <Metric label="Half Kelly" value={`${a.half_kelly}%`} />
            <Metric label="Quarter Kelly" value={`${a.quarter_kelly}%`} />
          </div>
          <div className="cm-hint">
            Treat Full Kelly as a ceiling, not a target. A fraction of it rides out variance.
          </div>
        </div>
        <div className="cm-card">
          <div className="cm-card-h">Position metrics</div>
          <Metric label="Breakeven" value={money(a.breakeven)} />
          <Metric label={`Move to breakeven`}
            value={a.move_needed_percent != null
              ? (a.move_needed_percent <= 0 ? 'already there'
                : `${a.move_direction === 'rise' ? '+' : '-'}${a.move_needed_percent}%`)
              : '--'} />
          <Metric label="Reward : risk (1 move)"
            value={a.reward_to_risk != null ? `${a.reward_to_risk} : 1` : '--'} />
          <Metric label="Expected move (1σ)" value={money(a.expected_move)} />
          <Metric label="Max loss / contract" value={money(a.max_loss_per_contract)} />
          <Metric label="Prob. of loss" value={`${a.probability_of_loss}%`} />
        </div>
      </div>
      <div className="cm-hint" style={{ marginTop: 10 }}>
        {a.detail} Assumes a long position (max loss = premium paid); a seller's
        risk is the mirror of this.
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: any }) {
  return (
    <div className="cm-metric">
      <span>{label}</span><b>{value}</b>
    </div>
  );
}

function TimeSalesTab({ ts }: { ts: any }) {
  const trades = ts?.trades || [];
  if (!trades.length) return <div className="cm-note">No prints on this contract.</div>;
  return (
    <div className="tbl-scroll" style={{ maxHeight: 420 }}>
      <table className="tbl">
        <thead>
          <tr>
            <th>Time</th><th className="r">Size</th><th className="r">Price</th>
            <th className="r">Premium</th><th>Side</th><th className="r">Spot</th>
            <th className="r">Bid/Ask</th><th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t: any, i: number) => (
            <tr key={i}>
              <td className="num">{clock(t.time)}</td>
              <td className="num r">{t.size != null ? Math.round(t.size).toLocaleString() : '--'}</td>
              <td className="num r">{money(t.price)}</td>
              <td className="num r"><b>{premium(t.premium)}</b></td>
              <td style={{ textTransform: 'capitalize' }}>{t.side}</td>
              <td className="num r">{money(t.underlying_price ?? t.spot)}</td>
              <td className="num r">{t.bid != null && t.ask != null ? `${num(t.bid)}/${num(t.ask)}` : '--'}</td>
              <td>{t.sweep ? 'SWEEP ' : ''}{t.floor ? 'FLOOR' : ''}{!t.sweep && !t.floor ? '--' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SIDE_COLORS: Record<string, string> = { ask: GREEN, bid: '#e08a00', mid: MID, 'no-side': '#c3c8d0' };
const SRC_COLORS: Record<string, string> = {
  normal: '#2bb3c0', sweep: '#c026d3', floor: '#7c3aed', cross: '#0ea5e9',
};

function VolumeTab({ vp }: { vp: any }) {
  const byPrice = vp?.by_price || [];
  const side = vp?.by_side || {};
  const src = vp?.by_source || {};

  // Two separate questions: WHERE in the spread the volume traded (bid/mid/ask,
  // which partitions the total) and HOW it was executed (ordinary vs sweep /
  // floor / cross). They are different dimensions, so they get their own chart
  // instead of being summed into one — which double-counted before.
  // Floor and cross trades have no spread side, so ask+bid+mid can fall short
  // of the total. Showing only the classified slices would imply, say, "89% at
  // the bid" of the whole day when it is 89% of a fraction. The remainder is
  // shown as its own slice so the donut is honest about what is classified.
  const classified = (side.ask || 0) + (side.bid || 0) + (side.mid || 0);
  const noSide = Math.max(0, (vp?.total || 0) - classified);
  const sidePie = useMemo(() => (
    [['ask', side.ask || 0], ['mid', side.mid || 0], ['bid', side.bid || 0],
     ['no-side', noSide]]
      .map(([name, value]) => ({ name, value }))
      .filter((x) => (x.value as number) > 0)
  ), [side, noSide]);
  const srcList = useMemo(() => (
    ['normal', 'sweep', 'floor', 'cross'].map((k) => ({ name: k, value: src[k] || 0 }))
      .filter((x) => x.value > 0)
  ), [src]);

  if (!byPrice.length && !sidePie.length) {
    return <div className="cm-note">No volume profile for this contract.</div>;
  }
  const bpData = byPrice.map((b: any) => ({
    price: `$${b.price}`, ask: b.ask_vol, bid: b.bid_vol, mid: b.mid_vol,
  }));
  const askPct = classified ? Math.round(((side.ask || 0) / classified) * 100) : null;

  return (
    <div className="cm-grid2">
      <div className="cm-card">
        <div className="cm-card-h">Volume by fill price</div>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={bpData} layout="vertical" margin={{ top: 4, right: 10, bottom: 4, left: 6 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis type="number" tick={{ fontSize: 9 }} />
            <YAxis type="category" dataKey="price" tick={{ fontSize: 9 }} width={56} />
            <Tooltip />
            <Bar dataKey="bid" stackId="v" fill="#e08a00" maxBarSize={46} />
            <Bar dataKey="mid" stackId="v" fill={MID} maxBarSize={46} />
            <Bar dataKey="ask" stackId="v" fill={GREEN} maxBarSize={46} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="cm-card">
        <div className="cm-card-h">
          Volume by side {askPct != null && <span className="cm-dim">· {askPct}% at ask (of classified)</span>}
        </div>
        <ResponsiveContainer width="100%" height={200}>
          <PieChart>
            <Pie data={sidePie} dataKey="value" nameKey="name" cx="50%" cy="50%"
              innerRadius={44} outerRadius={78} paddingAngle={2}>
              {sidePie.map((e) => <Cell key={e.name} fill={SIDE_COLORS[e.name] || MID} />)}
            </Pie>
            <Tooltip />
          </PieChart>
        </ResponsiveContainer>
        <div className="cm-srclist">
          {sidePie.map((e) => (
            <div key={e.name} className="cm-srcrow">
              <span>
                <i style={{ background: SIDE_COLORS[e.name] || MID }} />
                {e.name === 'no-side' ? 'No-side' : e.name === 'mid' ? 'Mid'
                  : `${e.name.charAt(0).toUpperCase()}${e.name.slice(1)}-side`}
              </span>
              <b>{Math.round(e.value).toLocaleString()}</b>
            </div>
          ))}
          {srcList.length > 0 && (
            <div className="cm-srcrow" style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 6, color: 'var(--text-mute)' }}>
              <span>By execution</span><span />
            </div>
          )}
          {srcList.map((e) => (
            <div key={e.name} className="cm-srcrow">
              <span><i style={{ background: SRC_COLORS[e.name] || MID }} />{e.name}</span>
              <b>{Math.round(e.value).toLocaleString()}</b>
            </div>
          ))}
          {vp?.multi > 0 && (
            <div className="cm-srcrow">
              <span><i style={{ background: '#0ea5e9' }} />multi-leg</span>
              <b>{Math.round(vp.multi).toLocaleString()}</b>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function HistoryTab({ h }: { h: any }) {
  const rows = h?.rows || [];
  if (!rows.length) return <div className="cm-note">No daily history for this contract.</div>;
  return (
    <div className="tbl-scroll" style={{ maxHeight: 420 }}>
      <table className="tbl">
        <thead>
          <tr>
            <th>Date</th><th className="r">Volume</th><th className="r">OI</th>
            <th className="r">Last</th><th className="r">High</th><th className="r">Low</th>
            <th className="r">IV</th><th className="r">Premium</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r: any, i: number) => (
            <tr key={i}>
              <td className="num">{r.date}</td>
              <td className="num r">{r.volume != null ? Math.round(r.volume).toLocaleString() : '--'}</td>
              <td className="num r">{r.open_interest != null ? Math.round(r.open_interest).toLocaleString() : '--'}</td>
              <td className="num r">{money(r.last_price)}</td>
              <td className="num r">{money(r.high_price)}</td>
              <td className="num r">{money(r.low_price)}</td>
              <td className="num r">{r.iv != null ? `${r.iv}%` : '--'}</td>
              <td className="num r">{premium(r.total_premium)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
