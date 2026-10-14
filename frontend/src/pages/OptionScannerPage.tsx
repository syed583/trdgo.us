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
// "strike / $rate / IV%" for a leg, or "—" when history is missing.
const leg = (x: any) => {
  if (!x) return '—';
  const k = x.strike != null ? x.strike : '—';
  const r = x.rate != null ? `$${Number(x.rate).toFixed(2)}` : '—';
  const iv = x.iv != null ? `${Number(x.iv).toFixed(1)}%` : '—';
  return `${k} / ${r} / ${iv}`;
};

export default function OptionScannerPage({ ctx }: { ctx: PageContext }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const resp = useApi<any>((s) => api2.tabBScan(5, s), [],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  const rows: any[] = resp.data?.rows || [];

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Activity size={20} /> IV Crush Check</h1>
        <p>Just before the result, what the ATM call and ATM put cost 10 trading sessions ago vs now,
          with IV for each. Next-session reporters only. <b>Now New York {clock('America/New_York')} ·
          Dubai {clock('Asia/Dubai')}</b></p>
      </div>
      <div className="ivc-help">
        <b>How to read this</b> — each cell is <code>strike / rate / IV</code>.
        <ul>
          <li><b>Strike</b> = the option strike nearest the stock price that day (so the 10-days-ago
            and today strikes differ when the stock has moved).</li>
          <li><b>Rate</b> = the option's mid price, (bid + ask) ÷ 2. Today is live; 10 days ago is
            that day's closing mid from the contract history.</li>
          <li><b>IV</b> = that contract's implied volatility. Watch it <b>rise</b> into earnings
            (the run-up) — e.g. TSM 40% → 56%. After the result it <b>crushes</b> back down, and
            both the call and put lose that inflated premium. “—” means no history for that day.</li>
        </ul>
      </div>
      <Panel title={<span className="at-col-title">IV Crush Check</span>}>
        {resp.initialLoading && !rows.length ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Loading…</p>
        ) : !rows.length ? (
          <p className="at-note">No watchlist names report the next session.</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <th className="ts-sym">Ticker</th>
                  <th>Result</th>
                  <th>Expiry</th>
                  <th>Call 10D ago<br /><span className="ivc-sub">strike / rate / IV</span></th>
                  <th>Call today<br /><span className="ivc-sub">strike / rate / IV</span></th>
                  <th>Put 10D ago<br /><span className="ivc-sub">strike / rate / IV</span></th>
                  <th>Put today<br /><span className="ivc-sub">strike / rate / IV</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const pending = r.status === 'PENDING' || !r.call_today;
                  return (
                    <tr key={r.symbol}>
                      <td className="ts-sym">{r.symbol}</td>
                      <td className="ts-date">{mdd(r.date || r.result_date)}
                        {r.result_code ? ` ${r.result_code}` : ''}</td>
                      <td className="ts-date">{mdd(r.expiry)}</td>
                      <td className="ts-num">{pending ? '' : leg(r.call_10d)}</td>
                      <td className="ts-num">{pending ? 'loading…' : leg(r.call_today)}</td>
                      <td className="ts-num">{pending ? '' : leg(r.put_10d)}</td>
                      <td className="ts-num">{pending ? '' : leg(r.put_today)}</td>
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
