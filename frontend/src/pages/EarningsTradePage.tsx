import { useMemo, useState } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  Area, AreaChart, Bar, BarChart, Cell, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  CalendarDays, Plus, TrendingUp, Target, Lightbulb, AlertTriangle, ChevronRight,
  Loader2, Scale,
} from 'lucide-react';
import type { PageContext } from '../App';
import { api, api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { num, signed } from '../lib/format';
import { ScoreGauge } from '../components/common';
import UpcomingEarnings from '../components/UpcomingEarnings';
import './earnings-trade-screen.css';

const SETUP = (s: number | null) =>
  s == null ? '--' : s >= 75 ? 'Strong Setup' : s >= 55 ? 'Good Setup'
    : s >= 45 ? 'Neutral' : 'Weak Setup';
// This theme's --blue token is actually tan, so use an explicit blue here to
// match the mockup's palette.
const ETS_BLUE = '#3a63f0';
const SETUP_COLOR = (s: number | null) =>
  s == null ? 'var(--text-mute)' : s >= 75 ? 'var(--green)' : s >= 55 ? ETS_BLUE
    : s >= 45 ? 'var(--amber)' : 'var(--red)';
const DEC_TONE: Record<string, string> = {
  BUY: 'buy', SELL: 'sell', STRADDLE: 'buy', STRANGLE: 'buy', 'NO TRADE': 'flat',
};
// Analyst-estimate lean -> colour: bullish green, bearish red, neutral amber.
const LEAN_TONE: Record<string, string> = {
  Bullish: 'buy', Bearish: 'sell', Neutral: 'flat',
};

function Gauge({ score, label, title }: { score: number | null; label: string; title: string }) {
  const c = SETUP_COLOR(score);
  return (
    <div className="ets-gauge-wrap">
      <div className="ets-gauge-title">{title}</div>
      <div className="ets-gauge">
        <ScoreGauge value={score ?? 0} color={c} size={118} />
        <div className="ets-gauge-num">
          <span style={{ color: c }}>{score == null ? '--' : Math.round(score)}</span>
          <span className="ets-gauge-max">/100</span>
        </div>
      </div>
      <div className="ets-setup" style={{ color: c }}>{label}</div>
    </div>
  );
}

// The "15-Day Signal" subtab: the stock's daily Stock/Options call over the last
// 15 days, with the date. A day whose call changed from the previous captured day
// shows a half/half split pill (old→new) with a pink pulse.
function Signal15View({ symbol }: { symbol: string }) {
  const q = useApi<any>(
    (s) => api2.earningsSignalTimeline([symbol], 15, true, s),
    [symbol],
    { refreshMs: 60_000 },
  );
  const cols: string[] = q.data?.cols || [];
  const row = q.data?.rows?.[symbol];
  const stockSrc: any[] = row?.stock_src || [];
  const optSrc: any[] = row?.options_src || [];
  const RGB: Record<string, string> = {
    BUY: '46,184,122', SELL: '242,70,90', NEUTRAL: '217,164,65',
  };
  const fmt = (iso: string) => {
    const dd = new Date(iso + 'T00:00:00');
    return dd.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  };
  const prevVal = (arr: any[], i: number) => {
    for (let j = i - 1; j >= 0; j--) if (arr[j] != null) return arr[j];
    return null;
  };
  const pill = (arr: any[], src: any[], i: number) => {
    const v = arr[i];
    if (v == null) return <span className="s15-pill s15-empty">—</span>;
    const est = src[i] === 'est';
    const estCls = est ? ' s15-est' : '';
    const estMark = est ? '~' : '';
    const prev = prevVal(arr, i);
    if (prev != null && prev !== v) {
      const style = {
        background: `linear-gradient(100deg, rgba(${RGB[prev]},.5) 0 47%,`
          + ` #fff 49% 51%, rgba(${RGB[v]},.5) 53% 100%)`,
      };
      // Blink only captured changes; estimates change quietly.
      return <span className={`s15-pill s15-changed${est ? '' : ' s15-blink'}${estCls}`}
        style={style as any}
        title={`${prev} → ${v}${est ? ' (estimated)' : ''}`}>{estMark}{prev}→{v}</span>;
    }
    const cls = v === 'BUY' ? 'buy' : v === 'SELL' ? 'sell' : 'neutral';
    return <span className={`s15-pill sig-${cls}${estCls}`}
      title={est ? 'Estimated from price history' : 'Captured signal'}>{estMark}{v}</span>;
  };
  if (q.initialLoading) return <div className="ets-empty">Loading the 15-day signal…</div>;
  if (!row || !cols.length) return <div className="ets-empty">No signal history yet.</div>;
  return (
    <div className="s15-card">
      <div className="s15-head"><span>Date</span><span>Stock</span><span>Options</span></div>
      {cols.map((_c, idx) => {
        const i = cols.length - 1 - idx;   // newest day first
        return (
          <div key={cols[i]} className="s15-row">
            <span className="s15-date">{fmt(cols[i])}</span>
            {pill(row.stock, stockSrc, i)}
            {pill(row.options, optSrc, i)}
          </div>
        );
      })}
      <div className="ets-disclaimer">Daily Stock / Options call over the last 15 days.
        Solid = captured that day; “~” italic/dashed = estimated from price history
        (trend for the stock, realised volatility for options) because no snapshot
        was captured then. A pink-pulsing split marks a captured day the call changed.</div>
    </div>
  );
}

// The landing view: the two change tabs (Intraday / Last 10 days) live in the
// page header and drive the Upcoming-earnings list's change banner below.
function EarningsTradeLanding({ demo, search, base, profile, embedded }:
  { demo?: boolean; search: string; base: string; profile?: string; embedded?: boolean }) {
  return (
    <div className="page ets">
      {!embedded && (
        <div className="ets-landing-head">
          <h1>Earnings Trade</h1>
          <p>Combined equity + options earnings analysis. Pick a stock reporting soon.</p>
        </div>
      )}
      <UpcomingEarnings base={base} title="Upcoming earnings"
        demo={demo} search={search} hideChanges profile={profile} />
    </div>
  );
}

// Score Breakdown: splits one engine's parameters into what's pushing the score
// up (+) vs down (-), with each one's points and the net total. Self-contained
// -- the only maths shown is this box's own +/- and net.
function ScoreSplit({ equity, options }: { equity: any; options: any }) {
  const [side, setSide] = useState<'equity' | 'options'>('equity');
  const src = side === 'equity' ? equity : options;
  const rows: any[] = (src?.why || []).filter((w: any) => w.available && w.points != null);
  const ups = rows.filter((w) => w.points > 0).sort((a, b) => b.points - a.points);
  const downs = rows.filter((w) => w.points < 0).sort((a, b) => a.points - b.points);
  const neutral = rows.filter((w) => w.points === 0).length;
  const noDataRows: any[] = (src?.why || []).filter((w: any) => !w.available);
  const noData = noDataRows.length;
  const sumUp = ups.reduce((s, w) => s + w.points, 0);
  const sumDown = downs.reduce((s, w) => s + w.points, 0);   // <= 0
  const net = sumUp + sumDown;
  // PRESENT = weight that actually had data; the score maps NET onto 0-100.
  const present = rows.reduce((s, w) => s + (Number(w.weight) || 0), 0);
  const score = src?.score;
  const fmt = (n: number) => `${n > 0 ? '+' : ''}${n.toFixed(1)}`;
  const pts = (w: any) => (w.points_label || '').replace(' of ', ' / ');
  return (
    <div className="ets-card ets-split">
      <div className="ets-card-h">
        <span><Scale size={15} /> Score Breakdown</span>
        <div className="ets-split-toggle">
          <button className={side === 'equity' ? 'on' : ''}
            onClick={() => setSide('equity')}>Stock Trade</button>
          <button className={side === 'options' ? 'on' : ''}
            onClick={() => setSide('options')}>Options Straddle</button>
        </div>
      </div>
      <div className="ets-split-cols">
        <div className="ets-split-col up">
          <div className="ets-split-ch">Pushing up (+) <b>{fmt(sumUp)}</b></div>
          {ups.map((w, i) => (
            <div key={i} className="ets-split-r"><span>{w.label}</span><em>{pts(w)}</em></div>
          ))}
          {!ups.length && <div className="ets-split-empty">None</div>}
        </div>
        <div className="ets-split-col down">
          <div className="ets-split-ch">Pushing down (−) <b>{fmt(sumDown)}</b></div>
          {downs.map((w, i) => (
            <div key={i} className="ets-split-r"><span>{w.label}</span><em>{pts(w)}</em></div>
          ))}
          {!downs.length && <div className="ets-split-empty">None</div>}
        </div>
      </div>
      {noDataRows.length > 0 && (
        <div className="ets-split-nodata">
          <span className="ets-split-nd-h">No data (not scored)</span>
          {noDataRows.map((w, i) => (
            <span key={i} className="ets-split-nd-chip" title={w.detail || ''}>{w.label}</span>
          ))}
        </div>
      )}
      <div className="ets-split-net">
        <span className="ets-split-net-k">Net</span>
        <b className={net >= 0 ? 'pos' : 'neg'}>{fmt(net)} pts</b>
        <span className="ets-split-sub">
          = {fmt(sumUp)} − {Math.abs(sumDown).toFixed(1)}
          {neutral ? ` · ${neutral} neutral` : ''}{noData ? ` · ${noData} no data` : ''}
        </span>
      </div>
      {score != null && present > 0 && (
        <div className="ets-split-calc">
          Score = 50 + (<b className={net >= 0 ? 'pos' : 'neg'}>{fmt(net)}</b> ÷ {present} available)
          × 50 = <b className="ets-split-final">{Math.round(score)}</b> / 100
        </div>
      )}
    </div>
  );
}

// `base`/`profile`/`embedded` let the My Calls page reuse this same engine view
// under its own route and scoring profile ("final") without duplicating the UI.
export default function EarningsTradePage({ ctx, base = '/earnings-trade', profile, embedded }:
  { ctx: PageContext; base?: string; profile?: string; embedded?: boolean }) {
  const { symbol, demo } = ctx;
  const { symbol: pathSym } = useParams();
  const navigate = useNavigate();
  // Hooks must run every render (Rules of Hooks), so they stay above the
  // list-view early return; the data calls are disabled until a ticker is picked.
  const on = !!pathSym && !demo;
  const [whyOpen, setWhyOpen] = useState(false);
  const [view, setView] = useState<'trade' | 'signal15'>('trade');
  // Carry the scoring profile into the equity/options subtab links.
  const subSearch = profile && profile !== 'default'
    ? (ctx.search ? `${ctx.search}&profile=${profile}` : `?profile=${profile}`)
    : ctx.search;

  const q = useApi<any>(
    (s) => (on ? api2.earningsTrade(symbol, profile, s) : Promise.resolve(null)),
    // Poll fairly often: a cold build can return a transient LOADING payload,
    // and the next poll then picks up the finished analysis on screen.
    [symbol, demo, pathSym, profile], { refreshMs: on ? 15_000 : undefined, enabled: on });
  const chart = useApi<any>(
    (s) => (on ? api.chart(symbol, '3M', s) : Promise.resolve(null)),
    [symbol, demo, pathSym], { enabled: on });
  const alerts = useApi<any>(
    (s) => (on ? api2.earningsAlerts(symbol, s) : Promise.resolve(null)),
    [symbol, demo, pathSym], { enabled: on });

  const d = q.data;
  const em = d?.expected_move || {};
  const strat = d?.strategy || {};
  const earn = d?.earnings || {};

  // Straddle payoff curve.
  const payoff = useMemo(() => {
    const cs = strat.call_strike, ps = strat.put_strike, prem = strat.total_premium;
    if (cs == null || ps == null || !prem) return [];
    const mid = (cs + ps) / 2;
    const pts = [];
    for (let i = -0.25; i <= 0.25; i += 0.025) {
      const S = mid * (1 + i);
      const pl = (Math.max(S - cs, 0) + Math.max(ps - S, 0) - prem) * 100;
      pts.push({ S: Math.round(S), pl: Math.round(pl) });
    }
    return pts;
  }, [strat]);

  const priceBars = useMemo(
    () => (chart.data?.bars || []).map((b: any) => ({ t: b.label, c: b.close })),
    [chart.data]);
  const sparkBars = useMemo(
    () => (d?.spark || []).map((v: number, i: number) => ({ i, v })), [d]);

  // List view (no ticker) -- all hooks above have run, safe to branch now.
  if (!pathSym) {
    return <EarningsTradeLanding demo={demo} search={ctx.search}
      base={base} profile={profile} embedded={embedded} />;
  }

  return (
    <div className="page ets">
      <Link to={`${base}${ctx.search}`} className="ets-back">
        <ChevronRight size={14} style={{ transform: 'rotate(180deg)' }} /> Upcoming earnings
      </Link>

      {demo ? (
        <div className="ets-empty">Disabled in demo mode.</div>
      ) : q.initialLoading ? (
        <div className="ets-empty ets-loading">
          <Loader2 className="ets-spin" size={18} /> Loading the earnings trade analysis…
        </div>
      ) : !d || d.status === 'LOADING' || d.status === 'DATA_UNAVAILABLE' ? (
        <div className="ets-empty ets-loading">
          <Loader2 className="ets-spin" size={18} />
          Building the earnings trade analysis… this can take a moment for a stock
          we haven't scored yet.
        </div>
      ) : d.status !== 'OK' ? (
        <div className="ets-empty">{d?.detail || 'No analysis available.'}</div>
      ) : (
        <>
          {/* Header */}
          <div className="ets-header">
            <div className="ets-id">
              <div className="ets-logo">{symbol.slice(0, 2)}</div>
              <div>
                <div className="ets-name-row">
                  <span className="ets-name">{d.name || symbol}</span>
                  <span className="ets-ticker">{symbol}</span>
                </div>
                <div className="ets-meta">
                  {d.exchange || 'US'} · {d.sector || '—'}
                </div>
              </div>
            </div>
            <div className="ets-price">
              <div className="ets-price-v">{d.price != null ? `$${num(d.price, 2)}` : '--'}</div>
              <div className={`ets-price-c ${(d.change_percent || 0) >= 0 ? 'pos' : 'neg'}`}>
                {signed(d.change, 2)} ({signed(d.change_percent, 2)}%)
              </div>
            </div>
            <div className="ets-spark">
              {sparkBars.length > 1 && (
                <ResponsiveContainer width="100%" height={48}>
                  <AreaChart data={sparkBars}>
                    <defs>
                      <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--green)" stopOpacity={0.4} />
                        <stop offset="100%" stopColor="var(--green)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <Area dataKey="v" stroke="var(--green)" strokeWidth={1.5}
                      fill="url(#sg)" isAnimationActive={false} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
            <div className="ets-earn">
              <div className="ets-earn-l">Earnings Date</div>
              <div className="ets-earn-v"><CalendarDays size={13} /> {earn.label || earn.date || '—'}</div>
              <div className="ets-earn-t">{earn.time || ''}</div>
            </div>
            {earn.days_to != null && (
              <div className="ets-days">
                <div className="ets-days-n">{earn.days_to}</div>
                <div className="ets-days-l">Days</div>
              </div>
            )}
            <div className="ets-actions">
              <button className="btn-primary" onClick={() => api2.watchlistAdd(symbol).catch(() => {})}>
                <Plus size={15} /> Add to Watchlist
              </button>
            </div>
          </div>

          {/* Sub-tabs */}
          <div className="ets-subtabs">
            <button type="button" className={`ets-subtab${view === 'trade' ? ' active' : ''}`}
              onClick={() => setView('trade')}>Earnings Trade</button>
            <button type="button" className={`ets-subtab${view === 'signal15' ? ' active' : ''}`}
              onClick={() => setView('signal15')}>15-Day Signal</button>
            <Link className="ets-subtab" to={`/earnings-equity/${symbol}${subSearch}`}>Equity detail</Link>
            <Link className="ets-subtab" to={`/earnings-options/${symbol}${subSearch}`}>Options detail</Link>
            <Link className="ets-subtab" to={`/options-flow/${symbol}${ctx.search}`}>Options Chain</Link>
            <Link className="ets-subtab" to={`/news/${symbol}${ctx.search}`}>News &amp; Sentiment</Link>
          </div>

          {view === 'signal15' ? (
            <Signal15View symbol={symbol} />
          ) : (<>
          {/* Row 1: signal / expected move / calendar */}
          <div className="ets-row ets-row-3">
            <div className="ets-card">
              <div className="ets-card-h">
                <span><Target size={15} /> Earnings Trade Signal</span>
                <span className={`ets-badge ${DEC_TONE[d.overall_decision] || 'flat'}`}>{d.overall_decision}</span>
              </div>
              <div className="ets-gauges">
                <Gauge title="Stock Trade" score={d.equity?.score} label={SETUP(d.equity?.score)} />
                <Gauge title="Options Straddle" score={d.options?.score} label={SETUP(d.options?.score)} />
              </div>
              <button className="ets-why-toggle" onClick={() => setWhyOpen((v) => !v)}
                aria-expanded={whyOpen}>
                {whyOpen ? 'Hide' : 'Why these scores?'}
                <ChevronRight size={13}
                  style={{ transform: whyOpen ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }} />
              </button>
              {whyOpen && (
                <div className="ets-why">
                  {([['Stock Trade', d.equity], ['Options Straddle', d.options]] as const)
                    .map(([title, side]) => (
                      <div key={title} className="ets-why-side">
                        <div className="ets-why-h">{title}
                          <span className="ets-why-score">{side?.score != null
                            ? `${Math.round(side.score)}/100 · ${SETUP(side.score)}` : '--'}</span>
                        </div>
                        {(side?.why || []).map((w: any, i: number) => (
                          <div key={i} className="ets-why-row">
                            <span className={`ets-why-dot ${
                              w.available === false ? 'none'
                                : w.leaning === 'Bullish' ? 'buy'
                                  : w.leaning === 'Bearish' ? 'sell' : 'flat'}`} />
                            <div>
                              <div className="ets-why-t">
                                <b>{w.label}</b>
                                <em>{w.available === false ? 'no data' : w.points_label}</em>
                              </div>
                              {w.detail && <div className="ets-why-d">{w.detail}</div>}
                            </div>
                          </div>
                        ))}
                        {!(side?.why || []).length && (
                          <div className="ets-why-d">No parameter detail available.</div>
                        )}
                      </div>
                    ))}
                  <div className="ets-note">Every parameter the engine scored,
                    strongest mover first. The score is the sum of their points.</div>
                </div>
              )}
            </div>

            <div className="ets-card">
              <div className="ets-card-h"><span><TrendingUp size={15} /> Expected Move</span></div>
              <div className="ets-em">
                <div className="ets-em-big">±{em.percent != null ? num(em.percent, 1) : '--'}%</div>
                <div className="ets-em-dollar">(~${em.dollars != null ? num(em.dollars, 2) : '--'})</div>
              </div>
              <div className="ets-em-range">
                <div><span>Lower</span><b>${em.lower != null ? num(em.lower, 2) : '--'}</b></div>
                <div><span>Upper</span><b>${em.upper != null ? num(em.upper, 2) : '--'}</b></div>
              </div>
              <div className="ets-em-bar"><div className="ets-em-dot" /></div>
              <div className="ets-em-cur">Current ${em.current != null ? num(em.current, 2) : '--'}</div>
            </div>

            <div className={`ets-card ets-alerts-card ${
              ((alerts.data?.counts?.RED || 0) + (alerts.data?.counts?.AMBER || 0)) > 0
                ? 'has-alerts' : ''}`}>
              <div className="ets-card-h">
                <span><AlertTriangle size={15} /> News &amp; Risk Alerts</span>
                {alerts.data?.counts && (
                  <span className="ets-badge alert-count">
                    {(alerts.data.counts.RED || 0) + (alerts.data.counts.AMBER || 0)} alerts
                  </span>
                )}
              </div>
              <div className="ets-alerts">
                {(alerts.data?.alerts || []).filter((a: any) => a.severity !== 'GREY')
                  .map((a: any, i: number) => (
                    <div key={i} className="ets-alert">
                      <span className={`ets-alert-tag ${a.severity === 'RED' ? 'red' : 'amber'}`}>
                        {a.severity === 'RED' ? 'High' : 'Medium'}
                      </span>
                      <div>
                        <div className="ets-alert-t">{a.title}</div>
                        <div className="ets-alert-d">{a.date || a.source || ''}</div>
                      </div>
                    </div>
                  ))}
                {!(alerts.data?.alerts || []).some((a: any) => a.severity !== 'GREY') && (
                  <div className="ets-empty-sm">No high-impact alerts.</div>
                )}
              </div>
            </div>
          </div>

          {/* Score Breakdown: +/- parameter contributions and the net. */}
          {(d.equity?.why?.length || d.options?.why?.length) ? (
            <div className="ets-row">
              <ScoreSplit equity={d.equity} options={d.options} />
            </div>
          ) : null}

          {/* Forecast: forward-looking analyst estimates (EPS / revenue / guidance). */}
          {(d.forecast || []).length > 0 && (
            <div className="ets-row">
              <div className="ets-card ets-forecast">
                <div className="ets-card-h">
                  <span><TrendingUp size={15} /> Forecast · Analyst Estimates</span>
                </div>
                <div className="ets-fc-grid">
                  {d.forecast.map((f: any) => (
                    <div key={f.name} className="ets-fc-item" title={f.detail || ''}>
                      <div className="ets-fc-label">{f.label}</div>
                      <div className={`ets-fc-lean ${f.available ? (LEAN_TONE[f.leaning] || 'flat') : 'na'}`}>
                        {f.available ? (f.leaning || '—') : 'No data'}
                      </div>
                      <div className="ets-fc-pts">{f.available ? f.points_label : ''}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Row 2: price chart / payoff / strategy */}
          <div className="ets-row ets-row-chart">
            <div className="ets-card">
              <div className="ets-card-h"><span>Price Chart · 3M</span></div>
              <ResponsiveContainer width="100%" height={230}>
                <LineChart data={priceBars}>
                  <XAxis dataKey="t" tick={{ fontSize: 10, fill: 'var(--text-mute)' }} minTickGap={40} />
                  <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10, fill: 'var(--text-mute)' }} width={44} />
                  <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', fontSize: 12 }} />
                  <Line dataKey="c" stroke="var(--green)" strokeWidth={1.6} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="ets-card">
              <div className="ets-card-h"><span>Options Straddle · Payoff</span></div>
              {payoff.length ? (
                <ResponsiveContainer width="100%" height={230}>
                  <LineChart data={payoff}>
                    <XAxis dataKey="S" tick={{ fontSize: 10, fill: 'var(--text-mute)' }} />
                    <YAxis tick={{ fontSize: 10, fill: 'var(--text-mute)' }} width={48} />
                    <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', fontSize: 12 }} />
                    <ReferenceLine y={0} stroke="var(--border-2)" />
                    {em.current != null && <ReferenceLine x={Math.round(em.current)} stroke="var(--text-mute)" strokeDasharray="3 3" />}
                    <Line dataKey="pl" stroke="#3a63f0" strokeWidth={1.8} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              ) : <div className="ets-empty-sm">No straddle data.</div>}
            </div>

            <div className="ets-card ets-strategy">
              <div className="ets-card-h">
                <span>Recommended Strategy</span>
                <span className="ets-badge strat">{strat.type || 'STRADDLE'}</span>
              </div>
              <div className="ets-strat-sub">Buy Call + Buy Put</div>
              <dl className="ets-strat-list">
                <div><dt>Expiry</dt><dd>{strat.expiry || '—'}</dd></div>
                <div><dt>Call Strike</dt><dd>{strat.call_strike != null ? `$${num(strat.call_strike, 2)}` : '—'}</dd></div>
                <div><dt>Put Strike</dt><dd>{strat.put_strike != null ? `$${num(strat.put_strike, 2)}` : '—'}</dd></div>
                <div><dt>Total Premium</dt><dd>{strat.total_premium != null ? `$${num(strat.total_premium, 2)}` : '—'}</dd></div>
                <div><dt>Breakeven (Upper)</dt><dd>{strat.breakeven_upper != null ? `$${num(strat.breakeven_upper, 2)}` : '—'}</dd></div>
                <div><dt>Breakeven (Lower)</dt><dd>{strat.breakeven_lower != null ? `$${num(strat.breakeven_lower, 2)}` : '—'}</dd></div>
              </dl>
              <button className="btn-primary ets-strat-btn"
                onClick={() => navigate(`/options-flow/${symbol}${ctx.search}`)}>
                View Options Chain <ChevronRight size={15} />
              </button>
            </div>
          </div>

          {/* Row 3: history / takeaway / alerts */}
          <div className="ets-row ets-row-3">
            <div className="ets-card">
              <div className="ets-card-h"><span>Historical Earnings Performance</span></div>
              {(d.historical_moves || []).length ? (
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={d.historical_moves}>
                    <XAxis dataKey="label" tick={{ fontSize: 9, fill: 'var(--text-mute)' }} />
                    <YAxis tick={{ fontSize: 10, fill: 'var(--text-mute)' }} width={34} unit="%" />
                    <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', fontSize: 12 }} />
                    <ReferenceLine y={0} stroke="var(--border-2)" />
                    <Bar dataKey="move_pct" radius={[2, 2, 0, 0]} maxBarSize={22} isAnimationActive={false}>
                      {d.historical_moves.map((m: any, i: number) => (
                        <Cell key={i} fill={m.move_pct >= 0 ? 'var(--green)' : 'var(--red)'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : <div className="ets-empty-sm">No post-earnings history yet.</div>}
            </div>

            <div className="ets-card">
              <div className="ets-card-h"><span><Lightbulb size={15} /> Key Takeaway</span></div>
              <ul className="ets-takeaways">
                {(d.takeaways || []).map((t: string, i: number) => (
                  <li key={i}><span className="ets-check">✓</span> {t}</li>
                ))}
              </ul>
            </div>

            <div className="ets-card">
              <div className="ets-card-h"><span><CalendarDays size={15} /> Earnings Calendar</span></div>
              <div className="ets-cal-date"><CalendarDays size={14} /> {earn.label || earn.date || '—'}</div>
              <div className="ets-cal-time">{earn.time || ''}</div>
              <div className="ets-timeline">
                {['Today', '7d before', `Earnings`, '1d after'].map((t, i) => (
                  <div key={t} className={`ets-tl-step ${i === 2 ? 'active' : ''}`}>
                    <span className="ets-tl-dot" /><span className="ets-tl-l">{t}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="ets-disclaimer">Analysis, not financial advice. Scores are
            computed from the full parameter set in the engine; only the summary is shown.</div>
          </>)}
        </>
      )}
    </div>
  );
}
