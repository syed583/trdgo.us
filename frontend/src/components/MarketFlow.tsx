import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity, AlertTriangle, ArrowRight, Check, Download, Layers, PhoneCall,
  Plus, RefreshCw, RotateCcw, Scale, Search, SlidersHorizontal,
  TrendingDown, TrendingUp, Wallet, X, Zap,
} from 'lucide-react';
import { api, api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { Panel } from './common';
import { compactMoney, money, num, signedPct } from '../lib/format';

/**
 * Market-wide options flow: the whole watched tape rather than one symbol.
 *
 * The per-symbol panels answer "what is happening in NVDA". These answer
 * "where is the money going", which needs every ticker in one pass. The
 * backend does that in a handful of grouped queries; this file is the reading
 * of them.
 *
 * Selecting a row opens that ticker's detail in place. Navigating away to a
 * symbol page would lose the tape, and the tape is the context that made the
 * row worth looking at.
 */

/**
 * Money in the units a flow desk actually says out loud.
 *
 * The sign goes in front of the currency symbol, not inside it: "-$333M"
 * reads, "$-333M" does not. Formatting the magnitude and prefixing the sign
 * keeps that true for every branch -- an earlier version stripped "$-" back
 * to "$" and silently turned net outflows into inflows.
 */
function premium(value: number | null | undefined, signed = false): string {
  if (value == null) return '--';
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : (signed && value > 0 ? '+' : '');
  let body: string;
  if (abs >= 1e9) body = `${(abs / 1e9).toFixed(2)}B`;
  else if (abs >= 1e6) body = `${(abs / 1e6).toFixed(1)}M`;
  else if (abs >= 1e3) body = `${(abs / 1e3).toFixed(0)}K`;
  else body = abs.toFixed(0);
  return `${sign}$${body}`;
}

function contracts(value: number | null | undefined): string {
  if (value == null) return '--';
  return Math.round(value).toLocaleString();
}

/**
 * A tile's session shape.
 *
 * Scaled to its own minimum and maximum rather than to zero: these are
 * cumulative totals that start large and only grow, so a zero baseline would
 * render every one of them as the same near-flat line across the top.
 */
function Spark({ points, tone }: { points: number[]; tone: string }) {
  if (!points || points.length < 3) return null;
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const step = 100 / (points.length - 1);
  const path = points
    .map((v, i) => `${i === 0 ? 'M' : 'L'} ${(i * step).toFixed(2)} ${(28 - (v - lo) / span * 26).toFixed(2)}`)
    .join(' ');
  const stroke = tone === 'green' ? 'var(--green)'
    : tone === 'red' ? 'var(--red)'
      : tone === 'amber' ? 'var(--amber)' : 'var(--blue)';
  return (
    <svg className="mf-spark" viewBox="0 0 100 30" preserveAspectRatio="none"
      aria-hidden="true">
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Tile({
  label, value, sub, tone = '', change, spark, icon,
}: {
  label: string; value: React.ReactNode;
  sub?: React.ReactNode; tone?: string; change?: number | null;
  spark?: number[]; icon?: React.ReactNode;
}) {
  return (
    <div className="mf-tile">
      <span className="mf-tile-head">
        <span className="mf-tile-label">{label}</span>
        {icon && <span className={`mf-tile-icon ${tone}`}>{icon}</span>}
      </span>
      <div className="mf-tile-main">
        <b className={`mf-tile-value ${tone}`}>{value}</b>
        {spark && <Spark points={spark} tone={tone} />}
      </div>
      <span className="mf-tile-foot">
        {change != null && (
          <em className={change >= 0 ? 'pos' : 'neg'}>
            {change >= 0 ? '▲' : '▼'} {Math.abs(change)}%
          </em>
        )}
        {sub && <span className="mf-tile-sub">{sub}</span>}
      </span>
    </div>
  );
}

/**
 * Flow sentiment as a half-dial.
 *
 * The needle sits on the call share of premium, which is the number the label
 * is derived from -- so the dial and the word beneath it can never disagree.
 * A dial driven by one figure and labelled from another is a chart that lies
 * quietly.
 */
function SentimentGauge({ lean, share }: {
  lean: string; share: number | null;
}) {
  const pct = share == null ? 50 : Math.max(0, Math.min(100, share));
  // -90deg is all puts, +90deg all calls.
  const angle = (pct - 50) / 50 * 90;
  const tone = lean === 'Bullish' ? 'var(--green)'
    : lean === 'Bearish' ? 'var(--red)' : 'var(--amber)';
  return (
    <div className="mf-dial">
      <svg viewBox="0 0 100 56" width="92" height="52">
        <path d="M 8 50 A 42 42 0 0 1 34 11" fill="none"
          stroke="var(--red)" strokeWidth="7" strokeLinecap="round" />
        <path d="M 38 9 A 42 42 0 0 1 62 9" fill="none"
          stroke="var(--amber)" strokeWidth="7" strokeLinecap="round" />
        <path d="M 66 11 A 42 42 0 0 1 92 50" fill="none"
          stroke="var(--green)" strokeWidth="7" strokeLinecap="round" />
        <g transform={`rotate(${angle} 50 50)`}>
          <line x1="50" y1="50" x2="50" y2="18" stroke={tone}
            strokeWidth="2.5" strokeLinecap="round" />
        </g>
        <circle cx="50" cy="50" r="3.5" fill={tone} />
      </svg>
      <b style={{ color: tone }}>{lean}</b>
    </div>
  );
}

/** The six headline figures for the session. */
export function FlowSummary({
  summary, comparison, intraday, unusualCount, onRefresh, loading,
}: {
  summary: any; comparison: any; intraday: any; unusualCount: number | null;
  onRefresh: () => void; loading: boolean;
}) {
  if (!summary || summary.status !== 'OK') {
    return (
      <Panel title="Market Flow Summary" icon={<Activity size={13} />}>
        <div className="mf-note">
          {summary?.detail || 'Market-wide flow is not available.'}
        </div>
      </Panel>
    );
  }

  const net = summary.net_premium ?? 0;
  const lean = summary.sentiment;
  // Only a real comparison against a real previous session is shown. There is
  // no placeholder trend: an arrow nobody measured is worse than no arrow.
  const c = comparison?.status === 'OK' ? comparison : null;
  const series = intraday?.status === 'OK' ? intraday.series : null;

  return (
    <>
      <div className="mf-tiles">
        <Tile label="Call premium" tone="green"
          icon={<PhoneCall size={13} />}
          value={premium(summary.call_premium)}
          change={c?.call_premium_change ?? null}
          spark={series?.call_premium}
          sub={`${contracts(summary.call_volume)} contracts`} />
        <Tile label="Put premium" tone="red"
          icon={<TrendingDown size={13} />}
          value={premium(summary.put_premium)}
          change={c?.put_premium_change ?? null}
          spark={series?.put_premium}
          sub={`${contracts(summary.put_volume)} contracts`} />
        <Tile label="Call / put ratio"
          icon={<Scale size={13} />}
          value={summary.call_put_ratio ?? '--'}
          change={c?.call_put_ratio_change ?? null}
          spark={series?.call_put_ratio}
          sub={`${summary.call_premium_share ?? '--'}% of premium is calls`} />
        <Tile label="Net premium" tone={net >= 0 ? 'green' : 'red'}
          icon={<Wallet size={13} />}
          value={premium(net, true)}
          change={c?.net_premium_change ?? null}
          spark={series?.net_premium}
          sub={net >= 0 ? 'Calls outspending puts' : 'Puts outspending calls'} />
        <Tile label="Unusual contracts"
          icon={<Zap size={13} />}
          value={unusualCount == null ? '--' : contracts(unusualCount)}
          sub="Volume above open interest" />
        <div className="mf-tile">
          <span className="mf-tile-label">Flow sentiment</span>
          <SentimentGauge lean={lean} share={summary.call_premium_share} />
          <span className="mf-tile-foot">
            <span className="mf-tile-sub">
              {contracts(summary.prints)} prints
            </span>
          </span>
        </div>
      </div>
      <div className="mf-session">
        <span>
          Session {summary.session} · {summary.symbols_covered} of{' '}
          {summary.symbols_requested} watched tickers had prints
          {c && ` · change vs ${c.previous_session}`}
        </span>
        <button className="ghost-btn" onClick={onRefresh}>
          <RefreshCw size={12} className={loading ? 'spin' : undefined} />
          Refresh
        </button>
      </div>
    </>
  );
}

/** The live tape: largest prints of the session, every ticker. */
export function FlowTape({
  tape, picked, onPick, filters, onToggleFilters, filtersOpen,
}: {
  tape: any; picked: string | null; onPick: (symbol: string) => void;
  filters: FlowFilterState;
  onToggleFilters: () => void; filtersOpen: boolean;
}) {
  const rows = useMemo(
    () => applyFilters(tape?.rows || [], filters), [tape, filters]);

  /**
   * Export exactly what is on screen, filters included.
   *
   * Exporting the unfiltered tape instead would hand back a file that does not
   * match the table the operator was looking at when they pressed the button.
   */
  const exportCsv = () => {
    const cols = ['time', 'symbol', 'type', 'strike', 'expiry', 'dte', 'spot',
      'bid', 'ask', 'volume', 'open_interest', 'volume_oi', 'premium', 'iv',
      'delta', 'gamma', 'side', 'sentiment'];
    const esc = (v: any) => {
      const text = v == null ? '' : String(v);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const csv = [cols.join(',')]
      .concat(rows.map((r) => cols.map((c) => esc(r[c])).join(',')))
      .join('\n');
    const url = URL.createObjectURL(
      new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `options-flow-${tape?.session || 'session'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Panel
      title="Live Options Flow"
      icon={<Activity size={13} />}
      noBody
      right={(
        <div className="mf-tape-controls">
          <span className="mf-tape-count">
            {rows.length} of {(tape?.rows || []).length} prints
          </span>
          <button className={`mf-chip ${filtersOpen ? 'on' : ''}`}
            onClick={onToggleFilters}>
            <SlidersHorizontal size={11} /> Filters
          </button>
          <button className="mf-chip" onClick={exportCsv}
            disabled={!rows.length}>
            <Download size={11} /> Export
          </button>
        </div>
      )}
    >
      {!tape || tape.status !== 'OK' ? (
        <div className="mf-note">
          {tape?.detail || 'No tape available for this session.'}
        </div>
      ) : !rows.length ? (
        <div className="mf-note">
          No print matches the current filter.
        </div>
      ) : (
        <div className="tbl-scroll" style={{ maxHeight: 520 }}>
          <table className="tbl mf-tape">
            <thead>
              <tr>
                <th>Time</th><th>Ticker</th><th>Type</th>
                <th className="r">Strike</th><th>Expiry</th><th className="r">DTE</th>
                <th className="r">Spot</th><th className="r">Bid/Ask</th>
                <th className="r">Volume</th><th className="r">OI</th>
                <th className="r">Vol/OI</th><th className="r">Premium</th>
                <th className="r">IV</th><th>Side</th><th>Signal</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.symbol}-${r.time}-${r.strike}-${i}`}
                  className={`clickable ${picked === r.symbol ? 'mf-picked' : ''}`}
                  onClick={() => onPick(r.symbol)}>
                  <td className="num">{r.time}</td>
                  <td><b className="mf-ticker">{r.symbol}</b></td>
                  <td>
                    <span className={`badge ${r.right === 'C' ? 'green' : 'red'}`}>
                      {r.type.toUpperCase()}
                    </span>
                  </td>
                  <td className="num r">{num(r.strike, 0)}</td>
                  <td className="num">{r.expiry}</td>
                  <td className="num r">{r.dte ?? '--'}</td>
                  <td className="num r">{money(r.spot)}</td>
                  <td className="num r mf-dim">
                    {r.bid != null && r.ask != null
                      ? `${num(r.bid)} / ${num(r.ask)}` : '--'}
                  </td>
                  <td className="num r">{contracts(r.volume)}</td>
                  <td className="num r mf-dim">{contracts(r.open_interest)}</td>
                  <td className={`num r ${(r.volume_oi ?? 0) >= 2 ? 'mf-hot' : ''}`}>
                    {r.volume_oi != null ? `${r.volume_oi}x` : '--'}
                  </td>
                  <td className="num r"><b>{premium(r.premium)}</b></td>
                  <td className="num r mf-dim">{r.iv != null ? `${r.iv}%` : '--'}</td>
                  <td className="mf-dim">{r.side}</td>
                  <td>
                    <span className={`badge ${r.sentiment === 'Bullish' ? 'green' : 'red'}`}>
                      {r.sentiment}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="hint">{tape?.detail}</div>
    </Panel>
  );
}

/** Contracts trading well above their own open interest. */
export function UnusualTable({ unusual, onPick }: {
  unusual: any; onPick: (symbol: string) => void;
}) {
  return (
    <Panel title="Top Unusual Options Activity"
      icon={<AlertTriangle size={13} />} noBody
      right={unusual?.count
        ? <span className="badge amber">{unusual.count} contracts</span>
        : undefined}>
      {!unusual || unusual.status !== 'OK' ? (
        <div className="mf-note">
          {unusual?.detail || 'No unusual activity detected this session.'}
        </div>
      ) : (
        <div className="tbl-scroll" style={{ maxHeight: 300 }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Ticker</th><th>Type</th><th className="r">Strike</th>
                <th>Expiry</th><th className="r">Vol/OI</th>
                <th className="r">Premium</th>
              </tr>
            </thead>
            <tbody>
              {unusual.rows.map((r: any, i: number) => (
                <tr key={`${r.symbol}-${r.strike}-${r.expiry}-${i}`}
                  className="clickable" onClick={() => onPick(r.symbol)}>
                  <td><b className="mf-ticker">{r.symbol}</b></td>
                  <td>
                    <span className={`badge ${r.right === 'C' ? 'green' : 'red'}`}>
                      {r.type.toUpperCase()}
                    </span>
                  </td>
                  <td className="num r">{num(r.strike, 0)}</td>
                  <td className="num">{r.expiry}</td>
                  <td className="num r mf-hot">{r.volume_oi}x</td>
                  <td className="num r"><b>{premium(r.premium)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="hint">{unusual?.detail}</div>
    </Panel>
  );
}

/**
 * Which sectors the option market is leaning on.
 *
 * Counted in transactions, not premium. The provider publishes a per-sector
 * transaction count and a sentiment score; the previous source published
 * dollars. Labelling counts as money would have been one word's change here
 * and a wrong number on the screen, so the column says what it holds.
 */
export function SectorFlow({ sectors, }: { sectors: any }) {
  const rows = sectors?.rows || [];
  const peak = useMemo(
    () => Math.max(1, ...rows.map((r: any) => r.total_trades || 0)),
    [sectors],
  );

  return (
    <Panel title="Sector Options Flow" icon={<Layers size={13} />}
      right={sectors?.total_trades
        ? <span className="badge gray">{num(sectors.total_trades, 0)} trades</span>
        : undefined}>
      {!sectors || sectors.status !== 'OK' || !rows.length ? (
        <div className="mf-note">
          {sectors?.detail || 'No sector breakdown available.'}
        </div>
      ) : (
        <div className="mf-sectors">
          {rows.map((r: any) => {
            const callLean = (r.share ?? 50) >= 50;
            return (
              <div className="mf-sector" key={r.sector}
                title={`${num(r.call_trades, 0)} call trades against `
                  + `${num(r.put_trades, 0)} put`}>
                <span className="mf-sector-name">{r.sector}</span>
                <span className="mf-sector-bar">
                  <i style={{ width: `${(r.total_trades || 0) / peak * 100}%` }}
                    className={callLean ? 'up' : 'dn'} />
                </span>
                <b>{num(r.total_trades, 0)}</b>
                <em className={callLean ? 'pos' : 'neg'}>
                  {r.share == null ? '--' : `${r.share}% calls`}
                </em>
              </div>
            );
          })}
        </div>
      )}
      <div className="hint">{sectors?.detail}</div>
    </Panel>
  );
}

/**
 * Tape filters.
 *
 * Applied in the browser, not by refetching: the session already came back as
 * one query, so narrowing it is a pass over an array the page is holding. A
 * round trip to show fewer rows would cost seconds to do less.
 */
export interface FlowFilterState {
  ticker: string;
  right: 'ALL' | 'C' | 'P';
  minPremium: number;
  minVolume: number;
  maxDte: number | null;
  unusualOnly: boolean;
}

export const EMPTY_FILTERS: FlowFilterState = {
  ticker: '', right: 'ALL', minPremium: 0, minVolume: 0,
  maxDte: null, unusualOnly: false,
};

const PREMIUM_STEPS = [
  { label: 'Any', value: 0 },
  { label: '$1M+', value: 1e6 },
  { label: '$5M+', value: 5e6 },
  { label: '$10M+', value: 1e7 },
];
const VOLUME_STEPS = [
  { label: 'Any', value: 0 },
  { label: '1K+', value: 1000 },
  { label: '5K+', value: 5000 },
  { label: '25K+', value: 25000 },
];
const DTE_STEPS: { label: string; value: number | null }[] = [
  { label: 'All', value: null },
  { label: '0-7d', value: 7 },
  { label: '0-30d', value: 30 },
  { label: '0-90d', value: 90 },
];

export function applyFilters(rows: any[], f: FlowFilterState): any[] {
  const needle = f.ticker.trim().toUpperCase();
  return rows.filter((r) => {
    if (needle && !r.symbol.includes(needle)) return false;
    if (f.right !== 'ALL' && r.right !== f.right) return false;
    if (f.minPremium && (r.premium ?? 0) < f.minPremium) return false;
    if (f.minVolume && (r.volume ?? 0) < f.minVolume) return false;
    if (f.maxDte != null && (r.dte == null || r.dte > f.maxDte)) return false;
    if (f.unusualOnly && (r.volume_oi ?? 0) < 2) return false;
    return true;
  });
}

export function FlowFilters({ filters, onChange }: {
  filters: FlowFilterState;
  onChange: (next: FlowFilterState) => void;
}) {
  const set = (patch: Partial<FlowFilterState>) =>
    onChange({ ...filters, ...patch });
  const dirty = JSON.stringify(filters) !== JSON.stringify(EMPTY_FILTERS);

  return (
    <div className="mf-filters">
      <span className="mf-filter-search">
        <Search size={12} color="var(--text-mute)" />
        <input value={filters.ticker} placeholder="Search ticker"
          onChange={(e) => set({ ticker: e.target.value.toUpperCase() })} />
      </span>

      <label className="mf-filter">
        <span>Type</span>
        <select value={filters.right}
          onChange={(e) => set({ right: e.target.value as any })}>
          <option value="ALL">Calls &amp; Puts</option>
          <option value="C">Calls only</option>
          <option value="P">Puts only</option>
        </select>
      </label>

      <label className="mf-filter">
        <span>Min premium</span>
        <select value={filters.minPremium}
          onChange={(e) => set({ minPremium: Number(e.target.value) })}>
          {PREMIUM_STEPS.map((o) => (
            <option key={o.label} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      <label className="mf-filter">
        <span>Min volume</span>
        <select value={filters.minVolume}
          onChange={(e) => set({ minVolume: Number(e.target.value) })}>
          {VOLUME_STEPS.map((o) => (
            <option key={o.label} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      <label className="mf-filter">
        <span>DTE</span>
        <select value={filters.maxDte == null ? '' : String(filters.maxDte)}
          onChange={(e) => set({
            maxDte: e.target.value === '' ? null : Number(e.target.value),
          })}>
          {DTE_STEPS.map((o) => (
            <option key={o.label} value={o.value == null ? '' : o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <label className="mf-check">
        <input type="checkbox" checked={filters.unusualOnly}
          onChange={(e) => set({ unusualOnly: e.target.checked })} />
        Unusual only
      </label>

      <button className="ghost-btn mf-reset" disabled={!dirty}
        onClick={() => onChange(EMPTY_FILTERS)}>
        <RotateCcw size={12} /> Reset
      </button>
    </div>
  );
}

/**
 * EDGAR files company names in capitals ("NVIDIA CORP"), which reads as
 * shouting in a header. Lower-cased word by word, with the short forms that
 * genuinely are initialisms left alone.
 */
const KEEP_UPPER = new Set([
  'NVIDIA', 'AMD', 'IBM', 'AT&T', 'JPMORGAN', 'HP', 'ADT', 'CVS', 'UPS',
  'LLC', 'PLC', 'NV', 'SA', 'AG',
]);

function titleCase(name: string): string {
  return name.split(/\s+/).map((word) => {
    const bare = word.replace(/[^A-Z&]/gi, '').toUpperCase();
    if (KEEP_UPPER.has(bare)) return word.toUpperCase();
    if (word.length <= 1) return word.toUpperCase();
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
  }).join(' ');
}

/**
 * The ticker's mark.
 *
 * A monogram rather than a real logo: fetching brand images means an external
 * asset host, and the app's content policy blocks those. The colour is derived
 * from the ticker itself, so a given symbol always looks the same and two
 * tickers side by side are easy to tell apart -- which is the whole job a logo
 * does in a list.
 */
function TickerMark({ symbol, size = 34 }: { symbol: string; size?: number }) {
  const hue = useMemo(() => {
    let h = 0;
    for (let i = 0; i < symbol.length; i += 1) {
      h = (h * 31 + symbol.charCodeAt(i)) % 360;
    }
    return h;
  }, [symbol]);
  return (
    <span className="mf-mark" style={{
      width: size,
      height: size,
      background: `linear-gradient(140deg, hsl(${hue} 62% 42%), hsl(${(hue + 38) % 360} 58% 30%))`,
      fontSize: size * 0.36,
    }}>
      {symbol.slice(0, 2)}
    </span>
  );
}

/** Money in a compact form for exposure figures, which run to billions. */
function exposure(value: number | null | undefined): string {
  if (value == null) return '--';
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function Row({ label, value, sub }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode;
}) {
  return (
    <div className="mf-row">
      <span>{label}</span>
      <b>{value}</b>
      {sub && <em>{sub}</em>}
    </div>
  );
}

/** A ring showing one share against its complement. */
function Ring({ percent, tone = 'green', size = 74 }: {
  percent: number | null; tone?: string; size?: number;
}) {
  const R = 30;
  const C = 2 * Math.PI * R;
  const share = percent == null ? 0 : Math.max(0, Math.min(100, percent));
  const lit = share / 100 * C;
  const colour = tone === 'red' ? 'var(--red)'
    : tone === 'amber' ? 'var(--amber)' : 'var(--green)';
  return (
    <svg viewBox="0 0 80 80" width={size} height={size} className="mf-ring">
      <g transform="rotate(-90 40 40)">
        <circle cx="40" cy="40" r={R} fill="none"
          stroke="var(--red)" strokeWidth="10" opacity="0.85" />
        <circle cx="40" cy="40" r={R} fill="none"
          stroke={colour} strokeWidth="10"
          strokeDasharray={`${lit} ${C - lit}`} />
      </g>
    </svg>
  );
}

/**
 * The score as a gauge.
 *
 * Confidence is printed beside it rather than drawn into the arc: a ring that
 * encoded both would be a single shape claiming two different things, and the
 * reader could not tell which one the arc was showing.
 */
function ScoreGauge({ value, label, confidence }: {
  value: number | null; label: string | null; confidence: number | null;
}) {
  const R = 34;
  const C = 2 * Math.PI * R;
  const lit = value == null ? 0 : Math.max(0, Math.min(100, value)) / 100 * C;
  const tone = label === 'BULLISH' ? 'var(--green)'
    : label === 'BEARISH' ? 'var(--red)' : 'var(--amber)';
  return (
    <div className="mf-gauge">
      <svg viewBox="0 0 90 90" width={84} height={84}>
        <g transform="rotate(-90 45 45)">
          <circle cx="45" cy="45" r={R} fill="none"
            stroke="var(--panel-2)" strokeWidth="8" />
          <circle cx="45" cy="45" r={R} fill="none" stroke={tone}
            strokeWidth="8" strokeLinecap="round"
            strokeDasharray={`${lit} ${C - lit}`} />
        </g>
        <text x="45" y="47" textAnchor="middle" className="mf-gauge-value">
          {value ?? '--'}
        </text>
        <text x="45" y="59" textAnchor="middle" className="mf-gauge-unit">
          /100
        </text>
      </svg>
      <div className="mf-gauge-body">
        <b style={{ color: tone }}>
          {label ? label.charAt(0) + label.slice(1).toLowerCase() : 'No data'}
        </b>
        <i>Confidence: {confidence ?? '--'}%</i>
      </div>
    </div>
  );
}

/**
 * What stands out about this ticker today, stated as findings rather than
 * decoration.
 *
 * Each card is a claim with the evidence that produced it. Nothing is listed
 * unless the measurement behind it exists: a signal that fires on missing data
 * is worse than a shorter list.
 */
function FlowSignals({ rows, baseline, levels }: {
  rows: any[]; baseline: any; levels: any;
}) {
  const signals: {
    kind: string; title: string; detail: string; at?: string;
  }[] = [];
  // The session time of the print a signal is drawn from, so a card says when
  // rather than implying "now".
  const latest = rows.reduce(
    (best: string | null, r: any) => (
      !best || String(r.time) > best ? String(r.time) : best), null);

  const calls = rows.filter((r) => r.right === 'C');
  const puts = rows.filter((r) => r.right === 'P');
  const callPremium = calls.reduce((n, r) => n + (r.premium || 0), 0);
  const putPremium = puts.reduce((n, r) => n + (r.premium || 0), 0);

  if (calls.length && callPremium > putPremium * 2) {
    signals.push({
      kind: 'bull',
      title: 'Call-led tape',
      detail: `${calls.length} call print${calls.length === 1 ? '' : 's'} `
        + `totalling ${premium(callPremium)} against ${premium(putPremium)} of puts.`,
      at: latest || undefined,
    });
  } else if (puts.length && putPremium > callPremium * 2) {
    signals.push({
      kind: 'bear',
      title: 'Put-led tape',
      detail: `${puts.length} put print${puts.length === 1 ? '' : 's'} `
        + `totalling ${premium(putPremium)} against ${premium(callPremium)} of calls.`,
      at: latest || undefined,
    });
  }

  const biggest = rows.reduce(
    (best, r) => ((r.premium || 0) > (best?.premium || 0) ? r : best), null as any);
  if (biggest) {
    signals.push({
      kind: biggest.right === 'C' ? 'bull' : 'bear',
      title: 'Largest block',
      detail: `${contracts(biggest.contracts)} contracts at `
        + `${num(biggest.strike, 0)} ${biggest.expiry} — ${premium(biggest.premium)}.`,
      at: biggest.time,
    });
  }

  const opening = rows.filter((r) => (r.volume_oi ?? 0) >= 2);
  if (opening.length) {
    signals.push({
      kind: 'warn',
      title: 'Positions being opened',
      detail: `${opening.length} contract${opening.length === 1 ? '' : 's'} `
        + 'trading above existing open interest, so this is new risk rather '
        + 'than a position being traded around.',
    });
  }

  if (baseline?.status === 'OK' && baseline.volume_ratio != null) {
    const hot = baseline.volume_ratio >= 1.5;
    const cold = baseline.volume_ratio <= 0.6;
    if (hot || cold) {
      signals.push({
        kind: hot ? 'warn' : 'quiet',
        title: hot ? 'Unusual volume' : 'Quieter than usual',
        detail: `${baseline.volume_ratio}x its own ${baseline.sessions}-session `
          + 'average option volume.',
      });
    }
  }

  const iv = levels?.data?.expected_move;
  if (iv?.iv_rank != null) {
    if (iv.iv_rank >= 70 || iv.iv_rank <= 20) {
      signals.push({
        kind: iv.iv_rank >= 70 ? 'warn' : 'quiet',
        title: iv.iv_rank >= 70 ? 'Elevated IV' : 'Depressed IV',
        detail: `Implied volatility is in the ${Math.round(iv.iv_rank)}th `
          + 'percentile of its own recent range.',
      });
    }
  }

  if (!signals.length) {
    return (
      <div className="mf-note">
        Nothing on this ticker stands out against its own normal today.
      </div>
    );
  }

  return (
    <div className="mf-signals">
      {signals.slice(0, 4).map((sig) => (
        <div className={`mf-signal ${sig.kind}`} key={sig.title}>
          <span className="mf-signal-head">
            {sig.kind === 'bull' ? <TrendingUp size={11} />
              : sig.kind === 'bear' ? <TrendingDown size={11} />
                : sig.kind === 'warn' ? <Zap size={11} />
                  : <Activity size={11} />}
            <b>{sig.title}</b>
            {sig.at && <em>{String(sig.at).slice(0, 5)}</em>}
          </span>
          <i>{sig.detail}</i>
        </div>
      ))}
    </div>
  );
}

export const DETAIL_TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'oi', label: 'Open Interest' },
  { key: 'gex', label: 'GEX & Max Pain' },
  { key: 'prints', label: 'Flow Details' },
] as const;
export type DetailTab = typeof DETAIL_TABS[number]['key'];

/**
 * Everything read off the chain, grouped behind the tab that asks for it.
 *
 * Seven sections is more than a 330px column can show at once, so they are
 * divided by the question being asked rather than stacked and scrolled: the
 * score and the expected move answer "what now", open interest and expiry
 * answer "what is positioned", gamma answers "how will dealers behave".
 *
 * The score and its confidence sit beside each other but are never combined.
 * A 64 from five agreeing signals and a 64 from one lonely signal are the same
 * number and different claims; merging them throws that difference away.
 */
function LevelsSections({ levels, tab, flow, baseline, prints }: {
  levels: any; tab: DetailTab; flow: any; baseline: any; prints: any[];
}) {
  if (levels.initialLoading) {
    return (
      <div className="mf-detail-sec">
        <div className="mf-note">Loading chain structure…</div>
      </div>
    );
  }

  const d = levels.data;
  if (!d || d.status !== 'OK') {
    return (
      <div className="mf-detail-sec">
        <h4>Chain structure</h4>
        <div className="mf-note">
          {d?.detail || 'No option chain available for this ticker.'}
        </div>
      </div>
    );
  }

  const oi = d.open_interest || {};
  const g = d.gamma || {};
  const em = d.expected_move || {};
  const gk = d.greeks || {};
  const score = d.score || {};
  const conf = d.confidence || {};
  const expiries = d.expiration_flow?.expirations || [];
  const peakExpiry = Math.max(1, ...expiries.map(
    (e: any) => (e.calls || 0) + (e.puts || 0)));

  if (tab === 'overview') {
    const callPremium = flow?.call_premium ?? null;
    const putPremium = flow?.put_premium ?? null;
    const totalPremium = (callPremium ?? 0) + (putPremium ?? 0);
    const callShare = totalPremium
      ? Math.round((callPremium ?? 0) / totalPremium * 1000) / 10 : null;

    return (
      <>
        <div className="mf-cards">
          <div className="mf-card">
            <span className="mf-card-label">Call vs put premium</span>
            {callShare == null ? (
              <div className="mf-note">Not on today&apos;s tape.</div>
            ) : (
              <div className="mf-card-ring">
                <Ring percent={callShare} />
                <div>
                  <b className="pos">{callShare}% {premium(callPremium)}</b>
                  <i>calls</i>
                  <b className="neg">
                    {Math.round((100 - callShare) * 10) / 10}% {premium(putPremium)}
                  </b>
                  <i>puts</i>
                </div>
              </div>
            )}
          </div>

          <div className="mf-card">
            <span className="mf-card-label">Option volume</span>
            <b className="mf-card-value">
              {baseline?.status === 'OK'
                ? contracts(baseline.today_volume) : '--'}
            </b>
            <span className="mf-card-sub">
              {baseline?.status === 'OK' && baseline.volume_ratio != null ? (
                <em className={baseline.volume_ratio >= 1 ? 'pos' : 'neg'}>
                  {baseline.volume_ratio}x its {baseline.sessions}-session average
                </em>
              ) : (baseline?.detail || 'No baseline on record')}
            </span>
          </div>

          <div className="mf-card">
            <span className="mf-card-label">Implied volatility</span>
            <b className="mf-card-value">
              {em.implied_volatility != null
                ? `${num(em.implied_volatility, 1)}%` : '--'}
            </b>
            <span className="mf-card-sub">
              {em.iv_rank != null
                ? `rank ${em.iv_rank} · ${em.iv_percentile}th percentile`
                : 'no IV history on record'}
            </span>
          </div>
        </div>

        <div className="mf-detail-sec">
          <h4>Options score · {d.expiry_label || d.expiry} · {d.dte}d</h4>
          <div className="mf-score-row">
            <ScoreGauge value={score.value ?? null} label={score.label ?? null}
              confidence={conf.confidence ?? null} />
            <div className="mf-levels mf-levels-card">
              {(d.levels || []).map((l: any) => (
                <div className="mf-level" key={l.name}>
                  <span>{l.name}</span>
                  <b>{l.value != null ? num(l.value, 2) : '--'}</b>
                </div>
              ))}
              <div className="mf-level">
                <span>Expected move</span>
                <b>{em.percent != null ? `±${em.percent}%` : '--'}</b>
              </div>
            </div>
          </div>
          {conf.status === 'OK' && (
            <div className="mf-note">
              {conf.coverage}% of the intended signal weight was measurable,
              and those signals agree to {conf.agreement}%.
            </div>
          )}
        </div>

        <div className="mf-detail-sec">
          <h4>Options flow signals</h4>
          <FlowSignals rows={prints} baseline={baseline} levels={levels} />
        </div>

        <div className="mf-detail-sec">
          <h4>Greeks</h4>
          {gk.status !== 'OK' ? (
            <div className="mf-note">{gk.detail || 'No Greeks on this chain.'}</div>
          ) : (
            <>
              <div className="mf-greeks">
                <div><span>Delta</span><b>{gk.delta}</b></div>
                <div><span>Gamma</span><b>{gk.gamma}</b></div>
                <div><span>Theta</span><b>{gk.theta}</b></div>
                <div><span>Vega</span><b>{gk.vega}</b></div>
              </div>
              <div className="mf-note">
                Weighted by open interest across {contracts(gk.contracts)}{' '}
                contracts, so one illiquid strike cannot set the profile.
              </div>
            </>
          )}
        </div>
      </>
    );
  }

  if (tab === 'oi') {
    return (
      <>
        <div className="mf-detail-sec">
          <h4>Open interest</h4>
          <Row label="Call / put OI"
            value={`${contracts(oi.call_oi)} / ${contracts(oi.put_oi)}`}
            sub={oi.put_call_oi != null ? `PCR ${oi.put_call_oi}` : undefined} />
          <Row label="Change today"
            value={oi.net_oi_change ? contracts(oi.net_oi_change) : '--'}
            sub={oi.net_oi_change
              ? `calls ${contracts(oi.call_oi_change)}, puts ${contracts(oi.put_oi_change)}`
              : 'not published for this chain'} />
        </div>

        <div className="mf-detail-sec">
          <h4>Flow by expiration</h4>
          {!expiries.length ? (
            <div className="mf-note">No expiry breakdown on this chain.</div>
          ) : (
            <div className="mf-expiries">
              {expiries.slice(0, 8).map((e: any) => (
                <div className="mf-expiry" key={e.expiry}>
                  <span>{e.label}</span>
                  <span className="mf-expiry-bar">
                    <i className="up"
                      style={{ width: `${(e.calls || 0) / peakExpiry * 100}%` }} />
                    <i className="dn"
                      style={{ width: `${(e.puts || 0) / peakExpiry * 100}%` }} />
                  </span>
                  <em>{e.dte}d</em>
                </div>
              ))}
            </div>
          )}
        </div>
      </>
    );
  }

  if (tab === 'gex') {
    return (
      <>
        <div className="mf-detail-sec">
          <h4>Key levels</h4>
          <div className="mf-levels">
            {(d.levels || []).map((l: any) => (
              <div className="mf-level" key={l.name}>
                <span>{l.name}</span>
                <b>{l.value != null ? num(l.value, 2) : '--'}</b>
                <em className={l.distance == null ? ''
                  : l.distance >= 0 ? 'pos' : 'neg'}>
                  {l.distance == null ? '--'
                    : `${l.distance > 0 ? '+' : ''}${l.distance}%`}
                </em>
              </div>
            ))}
          </div>
          <div className="mf-note">
            Distance from spot {money(d.spot)}. A level with no value is one
            the chain does not support: a wall is only a wall when a single
            strike actually carries the open interest.
          </div>
        </div>

        <div className="mf-detail-sec">
          <h4>Gamma exposure</h4>
          {g.status !== 'OK' ? (
            <div className="mf-note">{g.detail || 'No gamma on this chain.'}</div>
          ) : (
            <>
              <Row label="Net GEX" value={exposure(g.net_gex)}
                sub={g.regime === 'LONG_GAMMA' ? 'Long gamma' : 'Short gamma'} />
              <Row label="Call / put GEX"
                value={`${exposure(g.call_gex)} / ${exposure(g.put_gex)}`} />
              <Row label="Gamma flip"
                value={g.gamma_flip != null ? num(g.gamma_flip, 2) : '--'}
                sub={g.gamma_flip_distance != null
                  ? `${g.gamma_flip_distance > 0 ? '+' : ''}${g.gamma_flip_distance}%`
                  : undefined} />
              {!!(g.concentrations || []).length && (
                <div className="mf-note">
                  Largest concentrations:{' '}
                  {g.concentrations.slice(0, 3).map(
                    (c: any) => `${num(c.strike, 0)} (${exposure(c.gex)})`).join(' · ')}
                </div>
              )}
              <div className="mf-note">{g.regime_detail}</div>
            </>
          )}
        </div>
      </>
    );
  }

  return null;
}

/** Premium by expiration across the whole tape, calls against puts. */
export function ExpiryFlow({ expiries }: { expiries: any }) {
  const rows = expiries?.rows || [];
  const peak = Math.max(1, ...rows.map((r: any) => r.total));

  return (
    <Panel title="Options Flow by Expiry" icon={<Layers size={13} />}
      right={expiries?.session
        ? <span className="badge gray">{expiries.session}</span>
        : undefined}>
      {!rows.length ? (
        <div className="mf-note">
          {expiries?.detail || 'No expiry breakdown for this session.'}
        </div>
      ) : (
        <>
          <div className="mf-legend">
            <span><i className="up" />Call premium</span>
            <span><i className="dn" />Put premium</span>
          </div>
          <div className="mf-expchart">
            {rows.map((r: any) => (
              <div className="mf-expcol" key={r.expiry}
                title={`${r.expiry}: calls ${exposure(r.calls)}, puts ${exposure(r.puts)}`}>
                <span className="mf-expbars">
                  <i className="up" style={{ height: `${r.calls / peak * 100}%` }} />
                  <i className="dn" style={{ height: `${r.puts / peak * 100}%` }} />
                </span>
                <em>{r.expiry.slice(5)}</em>
              </div>
            ))}
          </div>
          <div className="hint">{expiries.detail}</div>
        </>
      )}
    </Panel>
  );
}

/**
 * One ticker's detail, opened from the tape without leaving the page.
 *
 * Deliberately shallow: the score, the levels, and how today's volume compares
 * with this name's own normal. Anything deeper is the per-symbol screen, which
 * the button leads to -- duplicating it here would mean two places to keep
 * right.
 */
export function TickerDetail({ symbol, tape, summary, onClose, onOpen }: {
  symbol: string | null; tape: any; summary: any; onClose: () => void;
  onOpen: (symbol: string) => void;
}) {
  const navigate = useNavigate();

  const baseline = useApi<any>(
    (s) => (symbol ? api2.flowBaseline(symbol, s) : Promise.resolve(null)),
    [symbol],
  );
  // Structure comes from the chain: open interest, dealer gamma, the levels,
  // the expected move and the Greeks. Its own request, so a slow chain never
  // holds up the flow figures above it -- those need no chain at all.
  const levels = useApi<any>(
    (s) => (symbol ? api2.optionsLevels(symbol, s) : Promise.resolve(null)),
    [symbol],
  );
  // The live quote refreshes on its own: a price that was fetched when the row
  // was clicked and never again is a stale number wearing a live label.
  const quote = useApi<any>(
    (s) => (symbol ? api.quote(symbol, s) : Promise.resolve(null)),
    [symbol],
    { refreshMs: symbol ? 20000 : undefined },
  );

  const [tab, setTab] = useState<DetailTab>('overview');
  // Saving is a write, so the button reports what actually happened rather
  // than flipping to "saved" the moment it is clicked.
  const [watch, setWatch] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle');
  const [watchError, setWatchError] = useState<string | null>(null);

  // A different ticker is a different question: the button resets with it.
  useEffect(() => { setWatch('idle'); setWatchError(null); }, [symbol]);

  const addToWatchlist = async () => {
    if (!symbol || watch !== 'idle') return;
    setWatch('saving');
    try {
      await api2.watchlistAdd(symbol);
      setWatch('saved');
    } catch (err) {
      setWatchError((err as Error).message);
      setWatch('failed');
    }
  };
  const rows = useMemo(
    () => (tape?.rows || []).filter((r: any) => r.symbol === symbol),
    [tape, symbol],
  );

  // Session totals for this ticker come from the market summary, which
  // aggregated every print. The tape holds only the largest ones, so summing
  // it would understate the day and disagree with the tiles above.
  const flow = useMemo(
    () => (summary?.per_symbol || []).find((e: any) => e.symbol === symbol)
      || null,
    [summary, symbol],
  );

  const totals = useMemo(() => {
    let call = 0; let put = 0;
    rows.forEach((r: any) => {
      if (r.right === 'C') call += r.premium || 0;
      else put += r.premium || 0;
    });
    return { call, put, net: call - put };
  }, [rows]);

  // Identity comes from the chain payload, which carries the EDGAR profile.
  const company = levels.data?.company || null;
  const exchange = tape?.rows?.find((r: any) => r.symbol === symbol)?.exchange
    || null;

  // Outside regular hours the extended print is the live one; the top-level
  // fields stay on the last completed session, so taking them blindly would
  // show yesterday's close as the current price.
  const q = quote.data;
  const live = q?.extended || null;
  const price = live?.price ?? q?.price ?? null;
  const change = live?.change ?? q?.change ?? null;
  const changePct = live?.change_percent ?? q?.change_percent ?? null;
  const session = live?.session_label || q?.market?.label || null;

  if (!symbol) {
    return (
      <Panel className="mf-detail" noBody title={undefined}>
        <div className="mf-empty">
          Select a print to see that ticker&apos;s flow, how today compares with
          its own normal, and a way through to the full chain.
        </div>
      </Panel>
    );
  }

  const b = baseline.data;
  const hot = b?.volume_ratio != null && b.volume_ratio >= 1.5;

  return (
    <Panel className="mf-detail" noBody title={undefined}>
      <div className="mf-detail-head">
        <TickerMark symbol={symbol} size={40} />
        <div className="mf-ident">
          <b>{company ? `${titleCase(company)} (${symbol})` : symbol}</b>
          <i title={company || undefined}>
            {[company ? titleCase(company) : null, exchange]
              .filter(Boolean).join(' | ')
              || `${rows.length} print${rows.length === 1 ? '' : 's'} on the tape`}
          </i>
        </div>
        <button className="mf-close" onClick={onClose} aria-label="Close">
          <X size={14} />
        </button>
      </div>

      <div className="mf-price">
        <div className="mf-price-main">
          {quote.initialLoading ? (
            <span className="mf-price-wait">Fetching price…</span>
          ) : price != null ? (
            <>
              <b>{money(price)}</b>
              <em className={(changePct ?? 0) >= 0 ? 'pos' : 'neg'}>
                {change != null ? `${change >= 0 ? '+' : ''}${num(change)} ` : ''}
                ({signedPct(changePct)})
              </em>
              {session && <span className="mf-price-session">{session}</span>}
            </>
          ) : (
            <span className="mf-price-wait">
              {quote.data?.error || quote.data?.status
                || 'No live price available'}
            </span>
          )}
        </div>
        <div className="mf-price-actions">
          <button className={`mf-watch ${watch === 'saved' ? 'saved' : ''}`}
            onClick={addToWatchlist} disabled={watch !== 'idle'}
            title={watch === 'failed' ? watchError || 'Could not save' : undefined}>
            {watch === 'saved' ? <Check size={11} />
              : watch === 'failed' ? <AlertTriangle size={11} />
                : <Plus size={11} />}
            {watch === 'saved' ? 'On watchlist'
              : watch === 'saving' ? 'Saving…'
                : watch === 'failed' ? 'Failed' : 'Add to Watchlist'}
          </button>
          <button className="mf-analyse" onClick={() => onOpen(symbol)}>
            Analyze
          </button>
        </div>
      </div>

      <div className="mf-detail-tabs">
        {DETAIL_TABS.map((t) => (
          <button key={t.key}
            className={`mf-detail-tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>

      <div className="mf-kv">
        <div>
          <span>Call premium</span>
          <b className="pos">{premium(totals.call)}</b>
        </div>
        <div>
          <span>Put premium</span>
          <b className="neg">{premium(totals.put)}</b>
        </div>
        <div>
          <span>Net</span>
          <b className={totals.net >= 0 ? 'pos' : 'neg'}>
            {premium(totals.net, true)}
          </b>
        </div>
        <div>
          <span>Spot</span>
          <b>{money(rows[0]?.spot)}</b>
        </div>
      </div>

      {tab === 'prints' && (
      <div className="mf-detail-sec">
        <h4>Today vs its own normal</h4>
        {baseline.initialLoading ? (
          <div className="mf-note">Measuring the baseline…</div>
        ) : b?.status !== 'OK' ? (
          <div className="mf-note">
            {b?.detail || 'No baseline available for this ticker.'}
          </div>
        ) : (
          <>
            <div className="mf-ratio">
              <b className={hot ? 'mf-hot' : ''}>{b.volume_ratio}x</b>
              <span>
                {contracts(b.today_volume)} contracts today vs a{' '}
                {b.sessions}-session average of {contracts(b.average_volume)}
              </span>
            </div>
            <div className="mf-note">
              Premium {b.premium_ratio}x normal. {b.detail}
            </div>
          </>
        )}
      </div>

      )}

      {tab === 'prints' && (
      <div className="mf-detail-sec">
        <h4>Largest prints</h4>
        {!rows.length ? (
          <div className="mf-note">No print for {symbol} on the current tape.</div>
        ) : (
          <div className="mf-prints">
            {rows.slice(0, 6).map((r: any, i: number) => (
              <div className="mf-print" key={`${r.time}-${r.strike}-${i}`}>
                <span className={`badge ${r.right === 'C' ? 'green' : 'red'}`}>
                  {r.type.toUpperCase()}
                </span>
                <span className="mf-print-body">
                  <b>{num(r.strike, 0)} · {r.expiry}</b>
                  <i>
                    {contracts(r.contracts)} @ {num(r.price ?? r.premium / (r.contracts * 100))}
                    {r.delta != null && ` · Δ ${num(r.delta)}`}
                    {r.gamma != null && ` · Γ ${num(r.gamma, 4)}`}
                  </i>
                </span>
                <b className="mf-print-premium">{premium(r.premium)}</b>
              </div>
            ))}
          </div>
        )}
      </div>

      )}

      <LevelsSections levels={levels} tab={tab} flow={flow}
        baseline={baseline.data} prints={rows} />

      <div className="mf-detail-cta">
        <button className="ghost-btn" onClick={() => onOpen(symbol)}>
          Full chain &amp; analytics <ArrowRight size={12} />
        </button>
        <button className="ghost-btn"
          onClick={() => navigate(`/ai-insights/${symbol}?run=1`)}>
          Analyse
        </button>
      </div>
    </Panel>
  );
}
