import React from 'react';
import { NavLink } from 'react-router-dom';
import { CalendarDays, Database, Gauge, Layers, Radio, Target, Zap } from 'lucide-react';
import {
  Area, AreaChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import type { LandingParts } from './InsightsPage';
import analysisHero from '../assets/analysis-hero.webp';
import '../new-dashboard.css';
import '../new-figma.css';   // values read node-by-node from frame 2:2

/**
 * The Analysis Intraday LANDING in the redesigned shell (Figma frame 2:2).
 *
 * THE FRAME IS NAMED "Market intelligence dashboard" AND IS NOT THE DASHBOARD. Its panels - Top
 * Movers, the index list, the earnings and news list - are the ones on THIS page's landing, not on
 * /dashboard. It was mapped by its name once; the owner corrected it, and the panels settle it.
 *
 * NOTHING HERE FETCHES ANYTHING. Both the controls that start an analysis and the three data panels
 * are PASSED IN, already built by InsightsPage from the components it has always used. This file is
 * layout and styling only. That is deliberate: a redesigned landing that re-implemented the panels
 * would be a second copy of the page's data logic, and the copy is the one that quietly stops
 * matching the original. Restyling is done in CSS, against the panels' own class names.
 *
 * OPTIONS FLOW, MARKET SENTIMENT AND SECTOR PERFORMANCE ARE BUILT HERE. They were left out of the
 * first version on the grounds that the live landing did not have them - which was the rule applied
 * backwards. "Leave out what we cannot measure" is not "show only what this page already showed";
 * all three are measured, and they drive Market Overview and Unusual Activity. Two requests cover
 * them for the whole page, both server-cached, neither per row.
 *
 * NOT built: the frame's one-symbol detail card (price, chart, score, RSI, ATR). This landing has no
 * selected symbol, picking one for the reader would be arbitrary, and the analysed view the search
 * leads to IS that card in full - with a quote and a chart request that would otherwise land on
 * every visit to the busiest page here. Nor "150+ Analytical Signals" or "85% Backtest Coverage":
 * nothing measures either, and a claim a reader cannot check is the thing this redesign removed.
 *
 * Only the IDLE landing changes. The scanning view and the analysed result are untouched, so a
 * search started here behaves exactly as it does today.
 */
export default function NewAnalysisLanding({
  ctx, controls, panels,
}: {
  ctx: PageContext;
  controls: React.ReactNode;
  /** Frame 2:2 puts Top Movers in a three-up row beside the featured symbol and the earnings
   *  calendar, so the panels arrive as PARTS to place rather than as one finished strip. The data
   *  still belongs to InsightsPage; only the arrangement is decided here. */
  panels: (layout?: (p: LandingParts) => React.ReactNode) => React.ReactNode;
}) {
  // Two page-level reads, both server-cached. The index strip reuses the shell's own indices, so it
  // costs nothing extra.
  const pulse = useApi<any>((sig) => api2.marketPulse(sig), [], { refreshMs: 300_000 });
  // One shared, server-cached read for the whole page - see /api/landing/featured.
  const featured = useApi<any>((sig) => api2.landingFeatured(sig), [], { refreshMs: 300_000 });
  const upcoming = useApi<any>((sig) => api2.earningsUpcoming(7, sig), [], { refreshMs: 600_000 });
  const tape = useApi<any>((sig) => api2.flowMarketTape(12, sig), [], { refreshMs: 300_000 });
  const [calTab, setCalTab] = React.useState<'today' | 'tomorrow' | 'week'>('today');
  // The frame's detail card has range tabs. 1D arrives with the featured payload (one shared,
  // server-cached series); 5D and 1M are fetched only when a reader presses that tab, and the
  // server caches those too - so a visit that never touches the tabs costs nothing for them.
  const [range, setRange] = React.useState<'1D' | '5D' | '1M'>('1D');
  const [extra, setExtra] = React.useState<Record<string, any[]>>({});
  const flow = useApi<any>((sig) => api2.flowMarketSummary(sig), [], { refreshMs: 300_000 });
  // One shared payload for every line on the page. It answers DISABLED until the server is told to
  // spend the requests, and in that state nothing below draws a line.
  const sparks = useApi<any>((sig) => api2.landingSparklines(sig), [], { refreshMs: 300_000 });

  // The PULSE's index rows, not /api/indices: they are scaled to the index level (SPY 712 ->
  // S&P 7,125), and they carry Russell 2000 and a VIXY-backed VIX that the other endpoint leaves
  // null. Same request the page already makes. /api/indices is fixed at source as well, so the
  // fallback below is correct too rather than merely available.
  // FOUR, and these four: frame 2:2 draws S&P 500, NASDAQ, DOW and VIX across the full width. A
  // fifth card (Russell 2000) wrapped onto a second row and broke the frame's rhythm; it is still
  // on /market. Ordered by the frame, not by whatever order the feed returns.
  const STRIP_ORDER = ['S&P 500', 'NASDAQ', 'NASDAQ 100', 'DOW', 'DOW JONES', 'VIX'];
  const idxAll: any[] = pulse.data?.indices || ctx.indices.data?.indices || [];
  const idx: any[] = React.useMemo(() => {
    const rank = (r: any) => {
      const i = STRIP_ORDER.indexOf(String(r?.label || '').toUpperCase());
      return i < 0 ? 99 : i;
    };
    return [...idxAll]
      .filter((r) => rank(r) < 99)
      .sort((a, b) => rank(a) - rank(b))
      .slice(0, 4);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idxAll]);
  const sectors: any[] = (pulse.data?.sectors || [])
    .filter((x: any) => x && x.change_percent != null)
    .slice(0, 6);
  const sent = pulse.data?.sentiment || null;
  const pc = pulse.data?.put_call || null;

  /** Green for a rise, red for a fall, neither for flat - +0.00% is not a gain. */
  const tone = (v: any) => {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return 'flat';
    return n > 0 ? 'up' : 'down';
  };
  const fq: any = featured.data?.quote || {};

  // Earnings, bucketed by date from the rows themselves. /api/indices `dates` carries an
  // earnings_count per range, but it reads 0 for every range while this endpoint returns rows for
  // the same week - so the rows are the source and the counts on the tabs are counted here.
  const todayIso = new Date().toISOString().slice(0, 10);
  const tomorrowIso = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
  const earnRows: any[] = upcoming.data?.rows || [];
  const earnBuckets = React.useMemo(() => ({
    today: earnRows.filter((r) => String(r.date || '').slice(0, 10) === todayIso),
    tomorrow: earnRows.filter((r) => String(r.date || '').slice(0, 10) === tomorrowIso),
    week: earnRows,
  }), [earnRows, todayIso, tomorrowIso]);
  const earnShown = earnBuckets[calTab].slice(0, 6);

  const prints: any[] = (tape.data?.rows || tape.data?.prints || []).slice(0, 8);
  // From /api/landing/featured, which counts the screener snapshot it already loaded. The
  // market pulse has no such field - reading one off it was a guess, and it rendered a dash.
  const stocksTracked: number | null = featured.data?.names_tracked ?? null;
  const marketOpen: boolean = !!pulse.data?.market?.is_open;
  const sessionLabel: string = pulse.data?.market?.label || '';

  const pctTxt = (v: any) =>
    (v === null || v === undefined ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
  const money0 = (v: any) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
    // Thousands too: one premium printed as "$900000" beside "$1.3M" in the flow table, and that
    // mismatch reads as a different scale rather than simply a smaller number.
    if (Math.abs(n) >= 1e3) return `$${Math.round(n / 1e3)}K`;
    return `$${n.toFixed(0)}`;
  };

  const featSym: string = featured.data?.symbol || '';
  React.useEffect(() => {
    if (range === '1D' || !featSym || extra[`${featSym}:${range}`]) return;
    let live = true;
    const ctrl = new AbortController();
    api2.chart(featSym, range, ctrl.signal)
      .then((r: any) => { if (live) setExtra((p) => ({ ...p, [`${featSym}:${range}`]: r?.bars || [] })); })
      .catch(() => { /* the card keeps the range it has */ });
    return () => { live = false; ctrl.abort(); };
  }, [range, featSym, extra]);

  const bars: any[] = range === '1D'
    ? (featured.data?.chart?.bars || [])
    : (extra[`${featSym}:${range}`] || []);
  const barsLoading = range !== '1D' && !extra[`${featSym}:${range}`];
  const series = React.useMemo(() => bars
    .filter((b: any) => b && b.close != null)
    .map((b: any) => ({ t: String(b.time || b.date || b.t || ''), close: Number(b.close) })), [bars]);
  // Recharts defaults a numeric axis to [0, max], which flattens an intraday line into a straight
  // band near the top of the box. The frame's chart fills its own range, so this one does too.
  const closes = series.map((x) => x.close);
  const lo = closes.length ? Math.min(...closes) : 0;
  const hi = closes.length ? Math.max(...closes) : 1;
  const padY = (hi - lo) * 0.12 || 1;
  const tech = featured.data?.technicals || {};
  const trendWord = (t: any) => {
    const v = String(t || '').toUpperCase();
    if (v.includes('BULL')) return 'Bullish';
    if (v.includes('BEAR')) return 'Bearish';
    if (!v || v === 'INSUFFICIENT_DATA') return null;
    return v.charAt(0) + v.slice(1).toLowerCase();
  };

  /** A sparkline, or nothing at all.
   *
   * Nothing is the important half. With no series the frame's line is simply absent - not a flat
   * rule, not a dash, not a shape drawn from one price. The card says less rather than implying
   * something it cannot show.
   */
  const sparkSeries: Record<string, number[]> = sparks.data?.series || {};
  const Spark = ({ symbol, tone: t }: { symbol?: string | null; tone: string }) => {
    const pts = symbol ? sparkSeries[String(symbol).toUpperCase()] : undefined;
    if (!pts || pts.length < 2) return null;
    const lo2 = Math.min(...pts);
    const hi2 = Math.max(...pts);
    const span = hi2 - lo2 || 1;
    const d = pts.map((v, i) => {
      const x = (i / (pts.length - 1)) * 100;
      const y = 100 - ((v - lo2) / span) * 100;
      return `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    return (
      <svg className="nal-spark" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <path d={d} fill="none" strokeWidth={4} vectorEffect="non-scaling-stroke"
          stroke={t === 'down' ? '#fe374a' : '#15c86e'} />
      </svg>
    );
  };

  // The gauge the frame draws: a score out of 100, as a half-ring.
  // `value`, not `score`: the pulse returns {value, label, components}. Reading a field that does
  // not exist is why this gauge showed "No sentiment reading right now" against a live 46/Neutral.
  const score = sent?.value != null ? Math.max(0, Math.min(100, Number(sent.value))) : null;
  const gauge = score == null ? [] : [
    { name: 'score', value: score, fill: score >= 60 ? 'var(--nu-up)' : score <= 40 ? 'var(--nu-down)' : 'var(--nu-warn)' },
    { name: 'rest', value: 100 - score, fill: 'var(--nu-line)' },
  ];

  return (
    <div className="nd nal">
      <section className="nd-hero nal-hero">
        <img className="nd-hero-img" src={analysisHero} alt="" aria-hidden="true" loading="eager" />

        <div className="nd-hero-left">
          <span className="nd-eyebrow">US Market Intelligence Platform</span>
          <h1>Trade Smarter with<br />Real Data &amp; <em>AI Insights</em></h1>
          <p>Earnings · Options flow · Technicals · Sentiment, read for one ticker at a time.</p>
          <div className="nal-controls">{controls}</div>
        </div>

        {/* Links to pages that exist. No figure is claimed on this artwork: the four cards the design
            painted into the image were replaced by real ones on the old landing, and a landing that
            states a number the server does not measure is the thing we removed, not a thing to add. */}
        <div className="nd-hero-right nal-feats">
          <NavLink to="/earnings-calendar" className="nd-hero-feat">
            <span className="nd-feat-ico violet"><CalendarDays size={18} /></span>
            <span><b>Earnings</b><i>Dates, estimates and surprises</i></span>
          </NavLink>
          <NavLink to="/unusual-activity" className="nd-hero-feat">
            <span className="nd-feat-ico amber"><Layers size={18} /></span>
            <span><b>Options Flow</b><i>Unusual contracts and disparity</i></span>
          </NavLink>
          <NavLink to="/strategy-trades" className="nd-hero-feat">
            <span className="nd-feat-ico green"><Target size={18} /></span>
            <span><b>Strategy Trades</b><i>Tracked entries, stops and exits</i></span>
          </NavLink>
          <NavLink to="/smart-move/engine20" className="nd-hero-feat">
            <span className="nd-feat-ico blue"><Zap size={18} /></span>
            <span><b>Smart Money Move</b><i>Calls before the window closes</i></span>
          </NavLink>
        </div>
      </section>

      {/* ------------------------------------------------------------ index strip */}
      {!!idx.length && (
        <div className="nal-strip">
          {idx.map((r: any) => (
            <div className="nal-strip-item" key={r.label}>
              <span className="nal-strip-k">
                {r.label}
                {/* The value is the PROXY's price, not the index level. Saying which instrument it
                    is turns a wrong-looking number into a correct one. */}
                {r.is_proxy && !!r.instrument && <i className="nal-via">via {r.instrument}</i>}
              </span>
              {/* /api/indices carries `value`; the pulse payload calls it `price`. Read both,
                  value first, so the strip matches the panel beside it rather than showing a dash. */}
              <b className={(r.price ?? r.value) == null ? 'nal-na' : undefined}>
                {(r.price ?? r.value) != null
                  ? Number(r.price ?? r.value).toLocaleString(undefined, { maximumFractionDigits: 2 })
                  : 'unavailable'}</b>
              {r.change_percent != null
                ? <i className={tone(r.change_percent)}>{pctTxt(r.change_percent)}</i>
                : <i className="nal-na">no reading</i>}
              <Spark symbol={r.instrument || r.symbol} tone={tone(r.change_percent)} />
            </div>
          ))}
        </div>
      )}

      {/* ------------------------------------------------------------- what we run on */}
      {/* Every figure here is counted, not claimed. The frame also advertises "150+ Analytical
          Signals" and "85% Backtest Coverage"; nothing measures either, so they are not here. */}
      <div className="nal-stats">
        <div className="nal-stat">
          <span className="nal-stat-ico blue"><Database size={17} /></span>
          <div><b>4</b><span>Data sources</span><i>Unusual Whales, Finnhub, Finviz, SEC EDGAR</i></div>
        </div>
        <div className="nal-stat">
          <span className="nal-stat-ico violet"><Layers size={17} /></span>
          <div>
            <b>{stocksTracked != null ? stocksTracked.toLocaleString() : '—'}</b>
            <span>Names tracked</span><i>optionable US stocks</i>
          </div>
        </div>
        <div className="nal-stat">
          <span className={`nal-stat-ico ${marketOpen ? 'green' : 'amber'}`}><Radio size={17} /></span>
          <div>
            <b>{marketOpen ? 'Live' : 'Closed'}</b>
            <span>{marketOpen ? 'Market data streaming' : 'Last session’s close'}</span>
            <i>{sessionLabel || ''}</i>
          </div>
        </div>
      </div>

      {/* The live panels      {/* Rows C and D of frame 2:2. Row C: Top Movers 404 | featured symbol 464 | earnings
          calendar 363, all 304 tall. Row D: options flow 466 | sentiment 375 | sectors 393,
          174 tall. Top Movers is the live panel, placed here rather than re-implemented.

          NOT placed: the frame has no Market Overview panel and no Earnings & News panel in this
          view - the index strip above is the market overview, and the calendar is the earnings
          list. Both are still built by InsightsPage and still live on /market and /news. */}
      {panels(({ movers }) => (
        <>
      <div className="nal-rowC">
        {movers}
        {/* ------------------------------------------------------------- featured symbol */}
        {!!fq.symbol && (
          <section className="nd-card nal-featured">
            <header className="nal-feat-head">
              <div>
                <h3>{fq.symbol}</h3>
                <span className="nst-mute">{fq.name || ''}</span>
              </div>
              <div className="nal-feat-price">
                <b>{fq.price != null ? `$${Number(fq.price).toFixed(2)}` : 'unavailable'}</b>
                {fq.change_percent != null && (
                  <i className={tone(fq.change_percent)}>
                    {fq.change != null ? `${Number(fq.change) >= 0 ? '+' : ''}${Number(fq.change).toFixed(2)} ` : ''}
                    ({pctTxt(fq.change_percent)})
                  </i>
                )}
              </div>
              <NavLink to={`/ai-insights?symbol=${fq.symbol}`} className="nal-more">Analyse {fq.symbol} →</NavLink>
            </header>
            {/* 2:2 draws the price chart here, with range tabs above it. */}
            <div className="nal-feat-tabs">
              {(['1D', '5D', '1M'] as const).map((k) => (
                <button type="button" key={k} className={range === k ? 'is-on' : ''}
                  onClick={() => setRange(k)}>{k}</button>
              ))}
            </div>
            <div className="nal-feat-chart">
              {series.length > 1 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={series} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
                    <defs>
                      <linearGradient id="nalFeatFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#15c86e" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#15c86e" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="t" hide />
                    <YAxis domain={[lo - padY, hi + padY]} hide />
                    <Tooltip formatter={(v: any) => [`$${Number(v).toFixed(2)}`, fq.symbol]}
                      labelFormatter={(l: any) => String(l)} />
                    <Area type="monotone" dataKey="close" stroke="#07832f" strokeWidth={1.6}
                      fill="url(#nalFeatFill)" dot={false} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="nd-empty">
                  {barsLoading || featured.loading ? 'Loading…' : `No ${range} series for ${fq.symbol}.`}
                </div>
              )}
            </div>
            {/* Trend, RSI and ATR are arithmetic over daily bars the server already holds. The
                frame's "AI Score" tile is absent by design: it needs an analysis run, nothing
                caches one per symbol, and this card will not print a score it cannot source. */}
            {(tech.rsi_14 != null || tech.atr_14 != null || trendWord(tech.trend)) && (
              <div className="nal-feat-tiles">
                {!!trendWord(tech.trend) && (
                  <div><span>Trend</span>
                    <b className={String(tech.trend).toUpperCase().includes('BULL') ? 'up'
                      : String(tech.trend).toUpperCase().includes('BEAR') ? 'down' : undefined}>
                      {trendWord(tech.trend)}</b></div>
                )}
                {tech.rsi_14 != null && (
                  <div><span>RSI (14)</span><b>{Number(tech.rsi_14).toFixed(1)}</b></div>
                )}
                {tech.atr_14 != null && (
                  <div><span>ATR (14)</span><b>{Number(tech.atr_14).toFixed(2)}</b></div>
                )}
              </div>
            )}
            <div className="nal-feat-grid">
              <div><span>Bid</span><b>{fq.bid != null ? Number(fq.bid).toFixed(2) : '—'}</b></div>
              <div><span>Ask</span><b>{fq.ask != null ? Number(fq.ask).toFixed(2) : '—'}</b></div>
              <div><span>Day range</span>
                <b>{fq.low != null && fq.high != null
                  ? `${Number(fq.low).toFixed(2)} – ${Number(fq.high).toFixed(2)}` : '—'}</b></div>
              <div><span>Previous close</span>
                <b>{fq.previous_close != null ? Number(fq.previous_close).toFixed(2) : '—'}</b></div>
            </div>
            <p className="nal-feat-note">
              Picked automatically — the largest company among today’s movers. One shared reading,
              refreshed for everyone at once.
            </p>
          </section>
        )}
        <section className="nd-card nal-cal">
          <header className="nd-card-head">
            <h3><CalendarDays size={14} /> Earnings calendar</h3>
            <div className="nd-tabs">
              {([['today', 'Today'], ['tomorrow', 'Tomorrow'], ['week', 'This week']] as const)
                .map(([k, label]) => (
                  <button type="button" key={k} className={calTab === k ? 'is-on' : ''}
                    onClick={() => setCalTab(k)}>
                    {label}<i className="nal-chip-n">{earnBuckets[k].length}</i>
                  </button>
                ))}
            </div>
          </header>
          {earnShown.length ? (
            <table className="nd-table nal-cal-table">
              <thead>
                <tr><th>Symbol</th><th>Session</th><th className="r">EPS estimate</th></tr>
              </thead>
              <tbody>
                {earnShown.map((r: any) => (
                  <tr key={`${r.symbol}-${r.date}`}>
                    <td>
                      <NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink>
                      <div className="nst-mute nal-cal-co">{r.company || ''}</div>
                    </td>
                    <td className="nst-mute">{r.timing_label || r.short_label || '—'}</td>
                    <td className="r">{r.eps_estimate != null ? `$${Number(r.eps_estimate).toFixed(2)}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="nd-empty">
              {upcoming.loading ? 'Loading…'
                : calTab === 'today' ? 'No company reports today.'
                  : calTab === 'tomorrow' ? 'No company reports tomorrow.' : 'No reports this week.'}
            </div>
          )}
        </section>
      </div>

      <div className="nal-rowD">
        <section className="nd-card nal-tape">
          <header className="nd-card-head">
            <h3><Layers size={14} /> Options flow</h3>
            <div className="nal-flow-sum">
              {!!flow.data && (
                <>
                  <span>Calls <b className="up">{money0(flow.data.call_premium)}</b></span>
                  <span>Puts <b className="down">{money0(flow.data.put_premium)}</b></span>
                  {flow.data.call_premium_share != null && (
                    <span>Call share <b>{Number(flow.data.call_premium_share).toFixed(0)}%</b></span>
                  )}
                  {!!pc?.ratio && <span>Put/call <b>{pc.ratio}</b></span>}
                </>
              )}
              <NavLink to="/unusual-activity" className="nal-more">View all →</NavLink>
            </div>
          </header>
          {prints.length ? (
            <div className="nst-scroll">
              <table className="nd-table nal-tape-table">
                <thead>
                  <tr>
                    <th>Symbol</th><th>Type</th><th className="r">Strike</th><th>Expiry</th>
                    <th className="r">Premium</th><th>Filled</th>
                  </tr>
                </thead>
                <tbody>
                  {prints.map((r: any, i: number) => (
                    <tr key={`${r.option_symbol || r.symbol}-${i}`}>
                      <td><span className="nd-sym">{r.symbol}</span></td>
                      <td>
                        <span className={`nd-chip ${String(r.type).toUpperCase() === 'PUT' ? 'put' : 'call'}`}>
                          {String(r.type || (r.right === 'P' ? 'PUT' : 'CALL')).toUpperCase()}
                        </span>
                      </td>
                      <td className="r">{r.strike != null ? Number(r.strike).toLocaleString() : '—'}</td>
                      <td className="nst-mute">{r.expiry_label || r.expiry || '—'}</td>
                      <td className="r"><b>{money0(r.premium)}</b></td>
                      <td className="nst-mute">{r.side ? String(r.side) : '—'}{r.sweep ? ' · sweep' : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="nd-empty">{tape.loading ? 'Loading…' : 'No notable prints right now.'}</div>
          )}
        </section>
        <section className="nd-card nal-mini">
          <header className="nd-card-head"><h3><Gauge size={14} /> Market sentiment</h3></header>
          {score != null ? (
            <div className="nal-gauge">
              {/* 92, not 132: row D is 174 tall in the frame and a taller ring was clipped at
                  the card's floor. */}
              <ResponsiveContainer width="100%" height={92}>
                <PieChart>
                  <Pie data={gauge} dataKey="value" startAngle={180} endAngle={0}
                    innerRadius={34} outerRadius={48} stroke="none" cy="92%">
                    {gauge.map((g) => <Cell key={g.name} fill={g.fill} />)}
                  </Pie>
                  <Tooltip formatter={(v: any, n: any) => (n === 'score' ? [`${v}/100`, 'Score'] : ['', ''])} />
                </PieChart>
              </ResponsiveContainer>
              <div className="nal-gauge-txt">
                <b>{score}<i>/100</i></b>
                <span>{sent?.label || ''}</span>
              </div>
            </div>
          ) : (
            <div className="nd-empty">{pulse.loading ? 'Loading…' : 'No sentiment reading right now.'}</div>
          )}
        </section>

        <section className="nd-card nal-mini">
          <header className="nd-card-head">
            <h3><Target size={14} /> Sector performance</h3>
            <NavLink to="/market" className="nal-more">View all →</NavLink>
          </header>
          {sectors.length ? (
            <ul className="nal-sectors">
              {/* Bars, as the frame draws them. The width is the move relative to the biggest move
                  on screen, so the longest bar is always full and the rest are honestly in scale. */}
              {sectors.map((x: any) => {
                const pctv = Number(x.change_percent) || 0;
                const peak = Math.max(...sectors.map((y: any) => Math.abs(Number(y.change_percent) || 0)), 0.01);
                return (
                  <li key={x.label || x.symbol}>
                    <span>{x.label}</span>
                    <span className="nal-bar">
                      <i className={pctv >= 0 ? 'up' : 'down'}
                        style={{ width: `${Math.min(100, (Math.abs(pctv) / peak) * 100)}%` }} />
                    </span>
                    <i className={tone(x.change_percent)}>{pctTxt(x.change_percent)}</i>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="nd-empty">{pulse.loading ? 'Loading…' : 'No sector reading right now.'}</div>
          )}
        </section>
      </div>
        </>
      ))}

      {ctx.isAdmin && (
        <p className="nal-admin">Landing panels: Top Movers, index prices, earnings and news — the same sources as the classic landing.</p>
      )}
    </div>
  );
}
