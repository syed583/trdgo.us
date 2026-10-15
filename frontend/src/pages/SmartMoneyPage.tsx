import { useMemo, useState } from 'react';
import { Loader2, Landmark, ChevronLeft } from 'lucide-react';
import { useParams, useNavigate } from 'react-router-dom';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './smart-money.css';

// Smart Money -- scanner + detail. Behind SMART_MONEY_TAB=on (endpoint returns
// DISABLED otherwise). Read-only research tab. Scanner score == detail score
// (same function, same cache).

const money = (v: any) => {
  if (v == null) return '—';
  const n = Number(v); const a = Math.abs(n);
  const s = n < 0 ? '-' : '';
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(0)}K`;
  return `${s}$${a.toFixed(0)}`;
};
const sh = (v: any) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toLocaleString()}`);
const biasClass = (b: string) =>
  b === 'BULLISH' ? 'sm-bull' : b === 'BEARISH' ? 'sm-bear' : b === 'INSUFFICIENT' ? 'sm-insuf' : 'sm-mixed';
const callClass = (c: string) =>
  c === 'BUY' ? 'sm-bull' : c === 'SELL' ? 'sm-bear' : c === 'INSUFFICIENT' ? 'sm-insuf' : 'sm-mixed';
const et = (iso: any) => {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: '2-digit', hour: '2-digit',
      minute: '2-digit', timeZone: 'America/New_York' }).format(new Date(iso));
  } catch { return String(iso); }
};

export default function SmartMoneyPage({ ctx }: { ctx: PageContext }) {
  const { symbol } = useParams();
  if (symbol) return <SmartMoneyDetail symbol={symbol.toUpperCase()} ctx={ctx} />;
  return <SmartMoneyScanner ctx={ctx} />;
}

function SmartMoneyScanner({ ctx }: { ctx: PageContext }) {
  const nav = useNavigate();
  const [filter, setFilter] = useState<'ALL' | 'BULLISH' | 'BEARISH' | 'MIXED'>('ALL');
  const resp = useApi<any>((s) => api2.smartMoneyScan(s), [], { refreshMs: ctx.demo ? undefined : 10_000 });
  const d = resp.data;
  if (d && d.status === 'DISABLED') {
    return <div className="page"><Panel title="Smart Money"><p className="at-note">
      This tab is off. Set <code>SMART_MONEY_TAB=on</code> in the backend .env to enable it.</p></Panel></div>;
  }
  const rows: any[] = d?.rows || [];
  const counts = d?.counts || {};
  const scored = d?.scored ?? rows.length;
  const total = d?.universe ?? 0;
  const stillScanning = total > 0 && scored < total;
  const warming = (resp.initialLoading && !rows.length) || (!rows.length && stillScanning);
  const shown = useMemo(() => filter === 'ALL' ? rows
    : rows.filter((r) => (r.bias === filter) || (filter === 'MIXED' && r.bias === 'MIXED')), [rows, filter]);

  const Card = ({ n, label, cls }: any) => (
    <div className={`sm-card ${cls}`}><div className="sm-card-n">{n ?? '—'}</div><div className="sm-card-l">{label}</div></div>
  );

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Landmark size={20} /> Smart Money</h1>
        <p>Where insiders, big options flow and institutions are really positioning. Signed 0–100 score
          (50 = neutral). <b>Call: BUY ≥ 55 · SELL ≤ 45 · NEUTRAL</b> (from this page's score only).
          Read-only research. Options are live; Form 4 is delayed; 13F is quarterly.</p>
      </div>

      <div className="sm-cards">
        <Card n={counts.bullish} label="Bullish" cls="sm-bull" />
        <Card n={counts.bearish} label="Bearish" cls="sm-bear" />
        <Card n={counts.mixed} label="Mixed" cls="sm-mixed" />
        <Card n={counts.insufficient} label="Insufficient data" cls="sm-insuf" />
      </div>

      <div className="sm-filters">
        {(['ALL', 'BULLISH', 'BEARISH', 'MIXED'] as const).map((f) => (
          <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>{f}</button>
        ))}
      </div>

      <Panel title={<span className="at-col-title">Smart Money scanner</span>}>
        {warming ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Scanning insiders, options flow and 13F…
            {total ? ` (${scored}/${total})` : ''}</p>
        ) : !shown.length ? (
          <p className="at-note">No names match this filter.{stillScanning ? ` Still scoring (${scored}/${total})…` : ''}</p>
        ) : (
          <div className="sm-wrap">
            {stillScanning && <p className="at-note"><Loader2 size={12} className="spin" /> Scoring {scored} of {total}…</p>}
            <table className="sm-tbl">
              <thead><tr>
                <th>Symbol</th><th className="n">Price</th><th className="n">Insider 30d</th>
                <th className="n">Unusual (net)</th><th className="n">Call prem %</th>
                <th className="n">Fund net sh</th><th className="n">Score</th><th>Call</th><th>Bias</th><th className="n">Cov</th>
              </tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.symbol} className="sm-rowlink" onClick={() => nav(`/smart-money/${r.symbol}`)}>
                    <td className="sym">{r.symbol}</td>
                    <td className="n">{r.price != null ? `$${Number(r.price).toFixed(2)}` : '—'}</td>
                    <td className={`n ${(r.insider_net_30d || 0) > 0 ? 'pos' : ''}`}>{r.insider_net_30d ? money(r.insider_net_30d) : '—'}</td>
                    <td className={`n ${(r.unusual_net || 0) > 0 ? 'pos' : (r.unusual_net || 0) < 0 ? 'neg' : ''}`}>{r.unusual_net != null ? money(r.unusual_net) : '—'}</td>
                    <td className="n">{r.call_share != null ? `${Math.round(r.call_share * 100)}%` : '—'}</td>
                    <td className={`n ${(r.fund_net_shares || 0) > 0 ? 'pos' : (r.fund_net_shares || 0) < 0 ? 'neg' : ''}`}>{r.fund_net_shares != null ? sh(r.fund_net_shares) : '—'}</td>
                    <td className="n"><b>{r.score ?? '—'}</b></td>
                    <td><span className={`sm-bias ${callClass(r.call)}`}>{r.call === 'INSUFFICIENT' ? '—' : r.call}</span></td>
                    <td><span className={`sm-bias ${biasClass(r.bias)}`}>{r.bias === 'INSUFFICIENT' ? 'Insufficient' : r.bias}</span></td>
                    <td className="n">{r.coverage_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="ts-foot">Score = 50 + net/2. Bias needs ≥65/≤35 and group agreement; coverage &lt; 60
          shows “Insufficient data”. Missing data never counts as 0. Times in New York.</div>
      </Panel>
    </div>
  );
}

const SECTIONS: Array<[string, string]> = [
  ['insider_news', 'Insider News (last 3 days)'],
  ['insider_trades', 'Insider Buy / Sell (Form 4)'],
  ['unusual', 'Unusual Options Activity'],
  ['disparity', 'Options Disparity'],
  ['fund_flow', 'Fund Buy / Sell (latest 13F)'],
  ['fund_positions', 'Positions in Funds'],
];

function SmartMoneyDetail({ symbol, ctx }: { symbol: string; ctx: PageContext }) {
  const nav = useNavigate();
  const resp = useApi<any>((s) => api2.smartMoneyDetail(symbol, s), [symbol],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  const d = resp.data;
  const params: any[] = d?.params || [];
  const pOf = (n: string) => params.find((p) => p.name === n) || {};

  return (
    <div className="page">
      <div className="ts-head sm-detail-head">
        <button className="sm-back" onClick={() => nav('/smart-money')}><ChevronLeft size={14} /> Scanner</button>
        <h1><Landmark size={20} /> {symbol}</h1>
        {d && !resp.initialLoading && d.status !== 'DISABLED' && (<>
          {!d.insufficient && <span className={`sm-bias ${callClass(d.call)}`}>{d.call} · {d.score}</span>}
          <span className={`sm-bias ${biasClass(d.bias)}`}>
            {d.insufficient ? 'Insufficient data' : d.bias}</span>
        </>)}
        {d?.asof && <span className="sm-asof">as of {et(d.asof)} ET</span>}
      </div>

      {resp.initialLoading && !d ? (
        <Panel title={symbol}><p className="at-note"><Loader2 size={12} className="spin" /> Loading…</p></Panel>
      ) : d?.status === 'DISABLED' ? (
        <Panel title={symbol}><p className="at-note">Smart Money is off (SMART_MONEY_TAB).</p></Panel>
      ) : (
        <>
          {/* points breakdown */}
          <Panel title={<span className="at-col-title">Points breakdown — score {d.insufficient ? '(insufficient)' : d.score}</span>}>
            <div className="sm-breakdown">
              {params.map((p) => (
                <div className="sm-bd" key={p.name}>
                  <span>{(SECTIONS.find(([n]) => n === p.name) || [, p.name])[1]}</span>
                  <b className={p.value > 0 ? 'pos' : p.value < 0 ? 'neg' : p.has_data ? '' : 'nd'}>
                    {p.has_data ? `${p.value > 0 ? '+' : ''}${p.value} / ${p.max}` : `no data / ${p.max}`}</b>
                </div>
              ))}
            </div>
          </Panel>

          {SECTIONS.map(([name, title]) => {
            const p = pOf(name); const x = p.extra || {};
            return (
              <Panel key={name} title={<span className="at-col-title">{title}</span>}>
                {!p.has_data ? <p className="at-note">No data.</p> : (
                  <SmSection name={name} x={x} detail={p.detail} />
                )}
              </Panel>
            );
          })}
          <div className="ts-foot">Real-time: options. Delayed: Form 4 (insider). Quarterly: 13F (never shown
            as live). All timestamps New York. Read-only.</div>
        </>
      )}
    </div>
  );
}

function SmSection({ name, x, detail }: { name: string; x: any; detail: string }) {
  if (name === 'insider_news') {
    return (<>
      <div className="sm-sub">{detail}</div>
      {(x.headlines || []).map((h: any, i: number) => (
        <div className="sm-news" key={i}>
          <span className={`sm-dot sm-${h.lean}`} />
          <span className="sm-news-h">{h.headline}</span>
          <span className="sm-news-m">{h.source} · {et(h.date)} ET {h.sentiment ? `· ${h.sentiment}` : ''}</span>
        </div>
      ))}
      {!(x.headlines || []).length && <p className="at-note">No material news in the last 3 days.</p>}
    </>);
  }
  if (name === 'insider_trades') {
    return (
      <div className="sm-wrap"><table className="sm-tbl">
        <thead><tr><th>Insider</th><th>Role</th><th>P/S</th><th className="n">Shares</th><th className="n">Value</th>
          <th>Trade date</th><th>Filed</th><th>10b5-1</th></tr></thead>
        <tbody>{(x.trades || []).map((t: any, i: number) => (
          <tr key={i}>
            <td>{t.owner}</td><td className="sm-sub">{t.title || t.role}</td>
            <td><span className={t.code === 'P' ? 'pos' : 'neg'}>{t.code}</span></td>
            <td className="n">{sh(t.shares)}</td><td className="n">{money(t.value)}</td>
            <td className="sm-sub">{String(t.transaction_date || '').slice(0, 10)}</td>
            <td className="sm-sub">{String(t.filing_date || '').slice(0, 10)}</td>
            <td>{t.is_10b5_1 ? 'yes' : 'no'}</td>
          </tr>))}
        </tbody></table>
        {!(x.trades || []).length && <p className="at-note">No qualifying open-market trades (90d).</p>}
      </div>
    );
  }
  if (name === 'unusual') {
    return (
      <div className="sm-wrap"><table className="sm-tbl">
        <thead><tr><th>Time</th><th>C/P</th><th className="n">Strike</th><th>Expiry</th><th className="n">Premium</th>
          <th>Type</th><th>Side</th><th className="n">Vol/OI</th></tr></thead>
        <tbody>{(x.prints || []).map((t: any, i: number) => (
          <tr key={i}>
            <td className="sm-sub">{et(t.time)}</td>
            <td><span className={t.type === 'CALL' ? 'pos' : 'neg'}>{t.type}</span></td>
            <td className="n">{t.strike}</td><td className="sm-sub">{String(t.expiry || '').slice(0, 10)}</td>
            <td className="n">{money(t.premium)}</td>
            <td className="sm-sub">{t.sweep ? 'sweep' : ''}{t.block ? ' block' : ''}</td>
            <td><span className={t.side === 'bull' ? 'pos' : 'neg'}>{t.side}</span></td>
            <td className="n">{t.vol_oi != null ? Number(t.vol_oi).toFixed(1) : '—'}</td>
          </tr>))}
        </tbody></table>
        {!(x.prints || []).length && <p className="at-note">No qualifying sweeps/blocks (≥$100k, ≤45 DTE, today+2 sessions).</p>}
      </div>
    );
  }
  if (name === 'disparity') {
    const cs = x.call_share != null ? Math.round(x.call_share * 100) : null;
    return (<>
      <div className="sm-sub">{detail}</div>
      {cs != null && (
        <div className="sm-bar"><div className="sm-bar-c" style={{ width: `${cs}%` }} />
          <span className="sm-bar-lbl">calls {cs}% · puts {100 - cs}%</span></div>
      )}
      <div className="sm-kv">
        <span>Call/Put volume: <b>{x.call_put_vol ?? '—'}</b></span>
        <span>20-day call share: <b>{x.norm_share != null ? `${Math.round(x.norm_share * 100)}%` : '—'}</b></span>
        <span>Net delta: <b>no data</b></span>
      </div>
      {x.divergence && <div className="sm-warn">⚠ Divergence: {x.divergence}</div>}
    </>);
  }
  if (name === 'fund_flow') {
    return (<>
      <div className="sm-sub">13F — quarter ending {x.quarter_end || '—'}, filed {x.filed || '—'} <b>(quarterly, delayed)</b></div>
      <div className="sm-kv">
        <span>Increasing: <b className="pos">{x.increasers ?? '—'}</b></span>
        <span>Decreasing: <b className="neg">{x.decreasers ?? '—'}</b></span>
        <span>New positions: <b>{x.new_positions ?? '—'}</b></span>
        <span>Closed: <b>no data</b></span>
        <span>Net shares: <b className={(x.net_shares || 0) > 0 ? 'pos' : 'neg'}>{sh(x.net_shares)}</b></span>
        <span>% of float: <b>{x.pct_float != null ? `${x.pct_float}%` : '—'}</b></span>
      </div>
    </>);
  }
  if (name === 'fund_positions') {
    return (<>
      <div className="sm-sub">Institutional ownership trend (newest→oldest): {(x.ownership_trend_pct || []).map((v: number) => `${v}%`).join(' · ') || '—'}</div>
      <div className="sm-sub">Active/hedge vs index split: <b>no data</b> (UW doesn’t classify fund type)</div>
      <div className="sm-wrap"><table className="sm-tbl">
        <thead><tr><th>Top holder</th><th className="n">Shares</th><th className="n">Value</th><th className="n">Δ shares</th><th className="n">% float</th></tr></thead>
        <tbody>{(x.top_holders || []).map((h: any, i: number) => (
          <tr key={i}><td>{h.name}</td><td className="n">{h.units != null ? Number(h.units).toLocaleString() : '—'}</td>
            <td className="n">{money(h.value)}</td>
            <td className={`n ${(h.units_changed || 0) > 0 ? 'pos' : (h.units_changed || 0) < 0 ? 'neg' : ''}`}>{sh(h.units_changed)}</td>
            <td className="n">{h.pct_float != null ? `${h.pct_float}%` : '—'}</td></tr>))}
        </tbody></table></div>
    </>);
  }
  return <div className="sm-sub">{detail}</div>;
}
