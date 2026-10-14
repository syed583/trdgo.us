import { Fragment, useEffect, useState } from 'react';
import { Loader2, Flame, Maximize2, Minimize2 } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Short Straddle Scanner (change spec, 14 Oct 2026): event + back expiry prices,
// full IV breakdown, implied-vs-historical move, 10-session inflation multiples.
// Primary rank = avg(premium multiple, IV multiple); fallback = Front/Back (TS).

const stClass = (s: string) =>
  s === 'SELL STRADDLE' ? 'pos' : s === 'WATCH' ? 'neu'
    : s === 'NO EDGE' ? 'neg' : 'skip';
const mrClass = (v: number | null) =>
  v == null ? '' : v >= 1.2 ? 'pos' : v >= 1.0 ? 'neu' : 'neg';
const n1 = (v: any) => (v == null ? '—' : Number(v).toFixed(1));
const n2 = (v: any) => (v == null ? '—' : Number(v).toFixed(2));
const pct = (v: any) => (v == null ? '—' : `${Number(v).toFixed(1)}%`);
const money = (v: any) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);
const mult = (v: any) => (v == null ? '—' : `×${Number(v).toFixed(2)}`);
const mdd = (iso: any) => { // "2026-10-16" -> "16 Oct"
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

export default function OptionScannerPage({ ctx }: { ctx: PageContext }) {
  const [open, setOpen] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  const [, setTick] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000); // refresh clocks
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFull(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [full]);

  const resp = useApi<any>((s) => api2.tabBScan(5, s), [],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  const rows: any[] = resp.data?.rows || [];
  const sellable = resp.data?.sellable ?? rows.filter((r) => r.status === 'SELL STRADDLE').length;

  const earn = (r: any) => `${mdd(r.date || r.report_date)}${r.result_code ? ' ' + r.result_code : ''}`;

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Flame size={20} /> Short Straddle Scanner · IV Crush</h1>
        <p>Sell the ATM call + ATM put the session before earnings; profit from the IV crush after
          the result. Ranked by inflation vs 10 sessions ago (premium & IV multiples); until snapshots
          build it uses the Front ÷ Back IV term structure. A trade needs implied move &gt; the stock's
          usual earnings move. <b>Now NY {clock('America/New_York')} · Dubai {clock('Asia/Dubai')}</b></p>
      </div>
      <div className={`ts-fswrap${full ? ' on' : ''}`}>
      <Panel
        title={<span className="at-col-title">Next-session straddles</span>}
        right={(
          <span className="ts-controls">
            <span className="at-chip">{sellable} sellable</span>
            <button className="ts-fsbtn" onClick={() => setFull((v) => !v)}
              title={full ? 'Exit full screen (Esc)' : 'Full screen'}>
              {full ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          </span>
        )}
      >
        {resp.initialLoading && !rows.length ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Scanning next-session straddles…</p>
        ) : !rows.length ? (
          <p className="at-note">No watchlist names report the next session. This tab fills the evening
            before a watchlist name's earnings.</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl ss-tbl">
              <thead>
                <tr className="ss-grp">
                  <th colSpan={5} className="ss-g-id">Identity</th>
                  <th colSpan={4} className="ss-g-px">Prices</th>
                  <th colSpan={6} className="ss-g-iv">IV breakdown</th>
                  <th colSpan={4} className="ss-g-10s">10-session</th>
                  <th colSpan={3} className="ss-g-mv">Move</th>
                  <th colSpan={3} className="ss-g-dec">Decision</th>
                </tr>
                <tr>
                  <th className="ts-num ss-freeze1">Rank</th>
                  <th className="ts-sym ss-freeze2">Ticker</th>
                  <th>Earnings</th>
                  <th className="ts-num">Spot</th>
                  <th className="ts-num">Event / DTE</th>
                  <th className="ts-num">Strike</th>
                  <th className="ts-num" title="bid / mid / ask">Call b/m/a</th>
                  <th className="ts-num" title="bid / mid / ask">Put b/m/a</th>
                  <th className="ts-num">Straddle</th>
                  <th className="ts-num">Call IV</th>
                  <th className="ts-num">Put IV</th>
                  <th className="ts-num">Front IV</th>
                  <th className="ts-num">Back IV</th>
                  <th className="ts-num" title="Front IV / Back IV">Front/Back</th>
                  <th className="ts-num" title="isolated earnings move">Event move</th>
                  <th className="ts-num">IV 10s</th>
                  <th className="ts-num">IV ×</th>
                  <th className="ts-num">Strad 10s</th>
                  <th className="ts-num">Prem ×</th>
                  <th className="ts-num" title="straddle / spot">Impl move</th>
                  <th className="ts-num" title="avg last-8 reaction move">Hist (n)</th>
                  <th className="ts-num" title="implied / historical">Move ratio</th>
                  <th>Method</th>
                  <th>Status</th>
                  <th>Tags</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const pending = r.status === 'PENDING';
                  const isOpen = open === r.symbol && !pending;
                  const c = r.call || {}; const p = r.put || {};
                  return (
                  <Fragment key={r.symbol}>
                  <tr className={isOpen ? 'ts-open' : ''}
                    onClick={() => !pending && setOpen(isOpen ? null : r.symbol)}>
                    <td className="ts-num ss-freeze1 ts-score pos">{r.rank ?? '—'}</td>
                    <td className="ts-sym ss-freeze2">{r.symbol}</td>
                    <td className="ts-date">{earn(r)}
                      {r.date_status && r.date_status !== 'CONFIRMED'
                        ? <span className="ts-excl"> {r.date_status}</span> : ''}</td>
                    <td className="ts-num">{money(r.spot)}</td>
                    <td className="ts-num">{mdd(r.event_expiry)}{r.event_dte != null ? ` (${r.event_dte}d)` : ''}</td>
                    <td className="ts-num">{r.atm_strike ?? '—'}</td>
                    <td className="ts-num">{pending ? 'warming' : `${n2(c.bid)}/${n2(c.mid)}/${n2(c.ask)}`}</td>
                    <td className="ts-num">{pending ? '' : `${n2(p.bid)}/${n2(p.mid)}/${n2(p.ask)}`}</td>
                    <td className="ts-num">{money(r.straddle)}</td>
                    <td className="ts-num">{pct(r.iv_call)}</td>
                    <td className="ts-num">{pct(r.iv_put)}</td>
                    <td className="ts-num"><b>{pct(r.iv_front)}</b></td>
                    <td className="ts-num">{pct(r.iv_back)}{r.back_expiry ? ` (${mdd(r.back_expiry)})` : ''}</td>
                    <td className="ts-num">{mult(r.front_back)}</td>
                    <td className="ts-num">{pct(r.event_move)}</td>
                    <td className="ts-num">{r.iv_10s_ago != null ? pct(r.iv_10s_ago) : <span className="ts-muted">—</span>}</td>
                    <td className="ts-num">{r.iv_multiple != null ? mult(r.iv_multiple) : <span className="ts-muted">—</span>}</td>
                    <td className="ts-num">{r.straddle_10s_ago != null ? money(r.straddle_10s_ago) : <span className="ts-muted">—</span>}</td>
                    <td className="ts-num">{r.prem_multiple != null ? mult(r.prem_multiple) : <span className="ts-muted">—</span>}</td>
                    <td className="ts-num"><b>{pct(r.implied_move)}</b></td>
                    <td className="ts-num">{pct(r.hist_move)}{r.hist_n != null ? ` (${r.hist_n})` : ''}</td>
                    <td className={`ts-num ${mrClass(r.move_ratio)}`}><b>{mult(r.move_ratio)}</b></td>
                    <td><span className="ts-muted">{r.method || '—'}</span></td>
                    <td><span className={`ts-badge ${stClass(r.status)}`}>{pending ? 'warming' : r.status}</span></td>
                    <td className="ts-excl">{(r.tags || []).join(', ')}</td>
                  </tr>
                  {isOpen && (
                    <tr className="ts-detailrow">
                      <td colSpan={25}>
                        <div className="ts-detail">
                          <div className="ts-detail-h">
                            {r.symbol} — sell the ${r.atm_strike} straddle, {mdd(r.event_expiry)} expiry:
                            {' '}sell 1 call + 1 put, collect {money(r.straddle)}. Buy back after the IV crush.
                            {r.inflation != null ? ` · inflation ${mult(r.inflation)} (${r.method})` : ''}
                            {r.move_ratio != null ? ` · move ratio ${mult(r.move_ratio)}` : ''}
                          </div>
                          <div className="ts-detail-grid">
                            <div className="ts-detail-item"><span className="ts-detail-lbl">Call (event)</span>
                              <span className="ts-detail-pts">bid <b>{n2(c.bid)}</b> / mid <b>{n2(c.mid)}</b> / ask <b>{n2(c.ask)}</b>
                                <em> · IV {pct(c.iv)} · Δ {n2(c.delta)} · OI {c.oi ?? '—'} · vol {c.vol ?? '—'}</em></span></div>
                            <div className="ts-detail-item"><span className="ts-detail-lbl">Put (event)</span>
                              <span className="ts-detail-pts">bid <b>{n2(p.bid)}</b> / mid <b>{n2(p.mid)}</b> / ask <b>{n2(p.ask)}</b>
                                <em> · IV {pct(p.iv)} · Δ {n2(p.delta)} · OI {p.oi ?? '—'} · vol {p.vol ?? '—'}</em></span></div>
                            <div className="ts-detail-item"><span className="ts-detail-lbl">IV term structure</span>
                              <span className="ts-detail-pts">front <b>{pct(r.iv_front)}</b> / back <b>{pct(r.iv_back)}</b> = {mult(r.front_back)}
                                <em> · back expiry {mdd(r.back_expiry)} · event-only move {pct(r.event_move)}</em></span></div>
                            <div className="ts-detail-item"><span className="ts-detail-lbl">Move edge</span>
                              <span className="ts-detail-pts">implied <b>{pct(r.implied_move)}</b> vs hist <b>{pct(r.hist_move)}</b> ({r.hist_n}) = <b className={mrClass(r.move_ratio)}>{mult(r.move_ratio)}</b></span></div>
                          </div>
                          <div className="ts-detail-sub">Earnings {earn(r)} · {r.date_status} · event expiry {mdd(r.event_expiry)}
                            {(r.tags || []).length ? ` · ${(r.tags).join(', ')}` : ''} · ⚠ short straddle = unlimited risk on a big gap; size small.</div>
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="cn-legend" style={{ margin: '10px 2px 0', flexWrap: 'wrap', gap: 14 }}>
          <span><b>SELL STRADDLE</b> inflation ≥ 1.15 · move ratio ≥ 1.2 · confirmed · no wide spread</span>
          <span><b>WATCH</b> inflated but move ratio 1.0–1.2 · <b>NO EDGE</b> move ratio &lt; 1.0 · <b>NO REAL INFLATION</b> &lt; 1.15 · <b>NOT DUE</b> not next session / date changed</span>
          <span><b>Method</b> 10S = 10-session multiples · TS = Front ÷ Back fallback</span>
        </div>
      </Panel>
      </div>
    </div>
  );
}
