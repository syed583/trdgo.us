import { useMemo, useState } from 'react';
import { Loader2, Compass, ChevronRight } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Pre-Earnings Direction (v2 spec). All-positive 0-100 direction score for every
// name reporting in the next ~15 days. Signal BUY >= 65 / NEUTRAL / SELL <= 35.
// Columns: Ticker, Earnings date, Days, Score, Signal, then one per parameter.

const PARAMS = [
  ['options', 'Options'],
  ['price_action', 'Price Act.'],
  ['insider', 'Insider'],
  ['peers', 'Peers'],
  ['eps_rev', 'EPS rev'],
  ['hist_reaction', 'Hist react'],
  ['rev_rev', 'Rev rev'],
  ['prior_guidance', 'Guidance'],
  ['dark_pool', 'Dark pool'],
] as const;

const mdd = (iso: any) => {
  if (!iso) return '—';
  try {
    return new Date(String(iso).slice(0, 10) + 'T12:00:00')
      .toLocaleDateString(undefined, { day: '2-digit', month: 'short' });
  } catch { return String(iso); }
};
const sigClass = (s: string) =>
  s === 'BUY' ? 'ped-buy' : s === 'SELL' ? 'ped-sell' : s === 'PENDING' ? 'ped-pend' : 'ped-neu';
// Colour a parameter cell by how far its bias leans from neutral.
const biasClass = (b: any) => {
  if (b == null) return 'ped-mute';
  if (b > 0.15) return 'ped-up';
  if (b < -0.15) return 'ped-dn';
  return '';
};

export default function PreEarningsDirectionPage({ ctx }: { ctx: PageContext }) {
  const [maxDays, setMaxDays] = useState(15);
  const [open, setOpen] = useState<string | null>(null);

  // The scan does a large cold DB read on the first request after a restart, so
  // the endpoint can first answer with a LOADING/empty payload. Poll every 8s so
  // it fills quickly, and show "Loading" (not "No names report") until ready.
  const resp = useApi<any>((s) => api2.preEarningsDirectionScan(15, s), [],
    { refreshMs: ctx.demo ? undefined : 8_000 });
  const allRows: any[] = resp.data?.rows || [];
  const scored = resp.data?.scored || 0;
  const notReady = !resp.data || !Array.isArray(resp.data.rows)
    || resp.data.status === 'LOADING' || resp.data.status === 'DATA_UNAVAILABLE';

  const rows = useMemo(
    () => allRows.filter((r) => r.days_to == null || r.days_to <= maxDays),
    [allRows, maxDays]);

  const pOf = (r: any, name: string) => (r.params || []).find((p: any) => p.name === name);

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Compass size={20} /> Pre-Earnings Direction</h1>
        <p>An all-positive 0–100 score for the pre-earnings drift of every stock reporting in the next
          ~15 days. <b>BUY ≥ 65 · NEUTRAL · SELL ≤ 35</b>; 50 = no signal. Nine parameters, each 0 to its
          weight; missing data scores half (never 0). {scored ? `${scored} scored.` : ''}</p>
      </div>

      <div className="ivc-help">
        <b>How to read this</b> — each parameter cell shows <code>points / max</code>. Green leans bullish,
        red bearish, grey = neutral or no data. Click a row for the full breakdown (bias and sub-splits).
        Options = disparity/skew 7 + flow/OI 6 + unusual 3. Dark pool only moves off neutral when it agrees
        with options or price action. The listed weights total 90, so the headline <b>Score</b> is the row
        total rescaled to 0–100 (no-signal = 50, exactly 15 from each 65/35 cut-off).
        <span className="ped-filter">Days to earnings ≤&nbsp;
          <input type="number" min={1} max={30} value={maxDays}
            onChange={(e) => setMaxDays(Math.max(1, Math.min(30, Number(e.target.value) || 15)))} />
        </span>
      </div>

      <Panel title={<span className="at-col-title">Pre-Earnings Direction — ranked by score</span>}>
        {(resp.initialLoading || notReady) && !allRows.length ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Loading… (scoring upcoming earnings)</p>
        ) : !rows.length ? (
          <p className="at-note">No names report within {maxDays} days.</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl ped-tbl">
              <thead>
                <tr>
                  <th className="ts-sym">Ticker</th>
                  <th>Earnings</th>
                  <th className="ts-num">Days</th>
                  <th className="ts-num">Score</th>
                  <th>Signal</th>
                  {PARAMS.map(([k, lbl]) => (
                    <th key={k} className="ts-num" title={k}>{lbl}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const pending = r.signal === 'PENDING' || r.score == null;
                  const isOpen = open === r.symbol;
                  return (
                    <>
                      <tr key={r.symbol} className={`ped-row ${isOpen ? 'ped-open' : ''}`}
                        onClick={() => setOpen(isOpen ? null : r.symbol)}>
                        <td className="ts-sym">
                          <ChevronRight size={11} className={`ped-caret ${isOpen ? 'rot' : ''}`} />
                          {r.symbol}
                        </td>
                        <td className="ts-date">{mdd(r.date)}{r.time_label ? ` ${r.time_label}` : ''}</td>
                        <td className="ts-num">{r.days_to ?? '—'}</td>
                        <td className="ts-num"><b>{pending ? '…' : r.score}</b></td>
                        <td><span className={`ped-sig ${sigClass(r.signal)}`}>{r.signal}</span></td>
                        {PARAMS.map(([k]) => {
                          const p = pOf(r, k);
                          return (
                            <td key={k} className={`ts-num ${p ? biasClass(p.bias) : 'ped-mute'}`}>
                              {p ? `${p.points}/${p.max}` : (pending ? '' : '—')}
                            </td>
                          );
                        })}
                      </tr>
                      {isOpen && !pending && (
                        <tr className="ped-detail-row">
                          <td colSpan={5 + PARAMS.length}>
                            <div className="ped-detail">
                              {(r.params || []).map((p: any) => (
                                <div className="ped-pcard" key={p.name}>
                                  <div className="ped-pcard-h">
                                    <span>{p.label}</span>
                                    <b className={biasClass(p.bias)}>{p.points} / {p.max}</b>
                                  </div>
                                  <div className="ped-pcard-b">
                                    bias {p.bias == null ? 'n/a (half)' : p.bias}
                                    {p.confirmed === false ? ' · not confirmed' : ''}
                                  </div>
                                  {p.sub && (
                                    <div className="ped-sub">
                                      {p.sub.map((s: any) => (
                                        <span key={s.name} className={biasClass(s.bias)}>
                                          {s.label}: {s.points}/{s.max}
                                        </span>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="ts-foot">All weights, bands and cut-offs live in settings (PED_*). Scores are
          logged daily so decisions can be compared with the actual move later.</div>
      </Panel>
    </div>
  );
}
