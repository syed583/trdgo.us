import { useEffect, useState } from 'react';
import { Loader2, Activity } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Table 1 - IV Crush Check. Just before the result, show what the ATM call and
// ATM put cost 10 trading sessions ago and what they cost now, with IV for each.
// No ranking, scores, status, colours or extra columns.

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
// A row's "last updated" wall-clock in New York (HH:MM), from its UTC timestamp.
const upd = (iso: any) => {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York',
    }).format(new Date(iso));
  } catch { return '—'; }
};
// "$rate / IV%" for one leg at the locked strike, or "—" when missing.
const leg = (x: any) => {
  if (!x) return '—';
  const r = x.rate != null ? `$${Number(x.rate).toFixed(2)}` : '—';
  const iv = x.iv != null ? `${Number(x.iv).toFixed(1)}%` : '—';
  return `${r} / ${iv}`;
};

export default function OptionScannerPage({ ctx }: { ctx: PageContext }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const resp = useApi<any>((s) => api2.tabBScan(15, s), [],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  const rows: any[] = resp.data?.rows || [];
  // 10 trading sessions ago is the same date for every US name; show it in the header.
  const d10 = (() => { const r = rows.find((x) => x.date_10d); return r ? mdd(r.date_10d) : ''; })();

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Activity size={20} /> IV Crush Check</h1>
        <p>One locked strike — the ATM from 10 trading sessions ago — shown then vs now (rate + IV for
          the call and put). A clean like-for-like so you can see the IV run-up. Every name reporting
          in the next ~10 trading days, <b>next-session reporters (•) first</b>; thin ATMs are filtered
          out. <b>Now New York {clock('America/New_York')} · Dubai {clock('Asia/Dubai')}</b></p>
      </div>
      <div className="ivc-help">
        <b>How to read this</b> — each cell is <code>strike / rate / IV</code>.
        <ul>
          <li><b>Strike (locked)</b> = the ATM strike 10 sessions ago. The SAME strike is used for
            "then" and "today" — a like-for-like comparison.</li>
          <li><b>Rate</b> = the option's mid price, (bid + ask) ÷ 2. "Today" is live; the "10 sessions
            ago" value is that day's closing mid from the contract history.</li>
          <li><b>IV</b> = that contract's implied volatility. At the locked strike, watch it <b>rise</b>
            into earnings (the run-up) — e.g. TSM 455 went ~37% → ~63%. After the result it
            <b>crushes</b> back down. “—” means no history for that day.</li>
        </ul>
      </div>
      <Panel title={<span className="at-col-title">IV Crush Check</span>}>
        {resp.initialLoading && !rows.length ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Loading…</p>
        ) : !rows.length ? (
          <p className="at-note">No names report in the next ~10 trading days.</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <th className="ts-sym">Ticker</th>
                  <th>Result</th>
                  <th>Expiry</th>
                  <th className="ts-num" title="locked strike = ATM 10 sessions ago">Strike</th>
                  <th className="ts-num">Call {d10 || '10D ago'}<br /><span className="ivc-sub">rate / IV</span></th>
                  <th className="ts-num">Call today<br /><span className="ivc-sub">rate / IV</span></th>
                  <th className="ts-num">Put {d10 || '10D ago'}<br /><span className="ivc-sub">rate / IV</span></th>
                  <th className="ts-num">Put today<br /><span className="ivc-sub">rate / IV</span></th>
                  <th className="ts-num" title="when this row was last fetched (New York)">Updated<br /><span className="ivc-sub">NY</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const pending = r.status === 'PENDING' || !r.call_now;
                  return (
                    <tr key={r.symbol}>
                      <td className="ts-sym">{r.symbol}
                        {r.next_session ? <span className="ivc-ns" title="reports the next session">•</span> : ''}</td>
                      <td className="ts-date">{mdd(r.date || r.result_date)}
                        {r.result_code ? ` ${r.result_code}` : ''}</td>
                      <td className="ts-date">{mdd(r.expiry)}</td>
                      <td className="ts-num"><b>{r.strike ?? '—'}</b></td>
                      <td className="ts-num">{pending ? '' : leg(r.call_then)}</td>
                      <td className="ts-num">{pending ? 'loading…' : leg(r.call_now)}</td>
                      <td className="ts-num">{pending ? '' : leg(r.put_then)}</td>
                      <td className="ts-num">{pending ? '' : leg(r.put_now)}</td>
                      <td className="ts-num"><span className="ivc-sub">{pending ? '' : upd(r.updated_at)}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="ts-foot">Rate = NBBO mid (bid+ask)/2; the 10-sessions-ago value is that day's
          closing mid. "—" means no contract history for that day. Read-only.</div>
      </Panel>
    </div>
  );
}
