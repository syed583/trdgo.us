import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ArrowRight, BarChart3, Building2, CalendarDays, CheckCircle2,
  Database, FileText, Gauge, Globe, LineChart, Loader2, Newspaper, Search,
  Star, TrendingUp, User, X, XCircle, Zap,
} from 'lucide-react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import WorldGlobe from '../components/WorldGlobe';
import analysisHero from '../assets/analysis-hero.webp';
import HeroStatCards from '../components/HeroStatCards';
import NewAnalysisLanding from './NewAnalysisLanding';
import scanVideo from '../assets/scan-globe.mp4';
import { api, api2 } from '../api/client';
import WhyCall from '../components/WhyCall';
import CorporateEvents from '../components/CorporateEvents';
import Disparity from '../components/Disparity';
import ParameterWhy from '../components/ParameterWhy';
import EarningsPreview from '../components/EarningsPreview';
import InstitutionalPanel from '../components/InstitutionalPanel';
import SymbolNews from '../components/SymbolNews';
import IntradayV2Panel from '../components/IntradayV2Panel';
import Dividends from '../components/Dividends';
import FundActivity from '../components/FundActivity';
import HorizonSwitch, { HORIZON_COPY, defaultHorizon } from '../components/HorizonSwitch';
import type { Horizon } from '../components/HorizonSwitch';
import { useApi } from '../hooks/useApi';
import { money, num, safeHref, signedPct } from '../lib/format';
import { useAnalysisStream } from '../hooks/useAnalysisStream';
import type { Category, DirSignalLike, Stage } from '../hooks/useAnalysisStream';

const GREEN = '#21d07a';
const RED = '#f2465a';
const AMBER = '#f0a92b';
const DIM = '#8892a8';

const SUGGESTED = ['AAPL', 'TSLA', 'MSFT', 'AMZN', 'META'];

const ICONS: Record<string, React.ReactNode> = {
  sec_filings: <FileText size={16} />,
  insider: <User size={16} />,
  institutional: <Building2 size={16} />,
  options_flow: <LineChart size={16} />,
  gex: <Database size={16} />,
  price_action: <BarChart3 size={16} />,
  relative_strength: <TrendingUp size={16} />,
  macro: <Globe size={16} />,
  iv_greeks: <Activity size={16} />,
  volume: <Gauge size={16} />,
  news: <Newspaper size={16} />,
  earnings: <CalendarDays size={16} />,
};

type Phase = 'idle' | 'searching' | 'scanning' | 'complete' | 'results';

/**
 * One analysis page that moves through its own states.
 *
 * Search, scan, complete and result are phases of a single screen rather than
 * separate routes. Splitting them across menu entries would make the user
 * navigate their own analysis, and the intermediate states have nothing to
 * show on their own -- a "scanning" page reached directly would be a page
 * about nothing.
 *
 * Phases advance on real events from the stream, not on timers. The scan
 * begins when the first provider is contacted and completes when the last
 * one answers, so a cached symbol moves through in seconds and a cold one
 * takes a minute. The screen reflects the work rather than performing it.
 */
/**
 * The shortest time the scan screen stays up.
 *
 * A deliberate run now re-reads every provider rather than replaying cache,
 * so it takes the better part of a minute on its own and this floor rarely
 * applies. It is kept for the case where a symbol genuinely resolves in a
 * couple of seconds -- a globe that appears and vanishes reads as the
 * analysis having been skipped. It is a floor on the screen, never padding on
 * the work: the elapsed figure on the result is always what the run took.
 */
const SCAN_FLOOR_MS = 4000;

export default function InsightsPage({ ctx }: { ctx: PageContext }) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [draft, setDraft] = useState('');
  const [symbol, setSymbol] = useState('');
  const [tab, setTab] = useState('overview');
  const seenResult = useRef(false);
  const scanStartedAt = useRef<number>(0);
  // One automatic start per arrival: re-running on every render would restart
  // the stream under the operator mid-scan.
  const autoRan = useRef(false);

  const navigate = useNavigate();
  const location = useLocation();

  // The outlook being asked about. A link from AI Trade names one; otherwise
  // it starts from the market clock -- today during the session, tomorrow any
  // other time -- and stays wherever the reader puts it.
  const clock = useApi<any>((s) => api.status(s), []);
  const session = clock.data?.market?.session as string | undefined;
  const urlHorizon = (new URLSearchParams(ctx.search).get('horizon') || '')
    .toUpperCase() as Horizon;
  const [picked, setPicked] = useState<Horizon | null>(
    ['TODAY', 'TOMORROW', 'SWING'].includes(urlHorizon) ? urlHorizon : null);
  const horizon: Horizon = picked ?? defaultHorizon(session);

  const stream = useAnalysisStream(symbol, false, horizon);
  const { stages, state, log, result, categories, elapsed, running, error } = stream;

  // Phase advances off the stream, so the screen can never claim to be
  // scanning something that has already finished.
  useEffect(() => {
    if (phase === 'searching' && Object.keys(state).length) setPhase('scanning');
  }, [phase, state]);

  useEffect(() => {
    if (!result || seenResult.current) return undefined;

    // Straight to the score -- the old "Analysis complete" card was a page
    // whose only content was the news that there was content. But not before
    // the scan has been on screen long enough to watch: a cached symbol
    // returns in about two seconds, and replacing the globe that fast reads
    // as the analysis having been skipped.
    const shown = Date.now() - (scanStartedAt.current || Date.now());
    const wait = Math.max(0, SCAN_FLOOR_MS - shown);
    const timer = setTimeout(() => {
      seenResult.current = true;
      setPhase('results');
    }, wait);
    return () => clearTimeout(timer);
  }, [result]);

  const analyse = (raw: string) => {
    if (ctx.readOnly) return; // view-only accounts cannot run the analysis
    const next = (raw || '').trim().toUpperCase();
    if (!next) return;
    seenResult.current = false;
    scanStartedAt.current = Date.now();
    setSymbol(next);
    setPhase('searching');
  };

  // The stream is keyed on the symbol and the outlook, so starting it has to
  // wait for both to land. The outlook comes from the market clock, which
  // arrives a moment after the page: firing before it landed started a run
  // for "tomorrow", then abandoned it and started a second for "today" --
  // and the second waited on the first, which no longer had a reader. The
  // screen sat at five of twelve sources for as long as anyone watched it.
  const clockReady = !clock.loading || !!clock.data || !!picked;
  useEffect(() => {
    if (symbol && phase === 'searching' && clockReady) stream.run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, clockReady]);

  /**
   * Start immediately when another screen sent a symbol here.
   *
   * Arriving with ?run=1 means the operator already pressed Analyse somewhere
   * else; showing them a search box with the ticker pre-typed would make them
   * ask for the same thing twice. Without the flag the page still opens idle,
   * because a bare visit to /ai-insights should not spend a minute of every
   * provider's rate limit on whichever symbol was last in the URL.
   */
  useEffect(() => {
    const wanted = new URLSearchParams(ctx.search).get('run');
    if (!wanted || !ctx.symbol || autoRan.current) return;
    autoRan.current = true;
    analyse(ctx.symbol);

    // Consume the flag. It means "start this one now", not "start something
    // every time this page is opened from here on" -- and every link in the
    // shell is built from the current query string, so left in the URL it
    // rode along to the sidebar's own Analysis entry. Clicking that then
    // re-ran the last symbol instead of showing the search box.
    const params = new URLSearchParams(ctx.search);
    params.delete('run');
    params.delete('horizon');
    const qs = params.toString();
    navigate(
      { pathname: location.pathname, search: qs ? `?${qs}` : '' },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx.symbol, ctx.search]);

  /**
   * A fresh arrival on this route starts at the search box.
   *
   * The page is not remounted when the shell navigates from one analysis URL
   * to another, so without this, opening Analysis while a finished run was on
   * screen showed that old result rather than asking which stock to read.
   * Only the path is watched: typing a ticker changes local state, not the
   * URL, so a run in progress is never interrupted by this.
   */
  const lastPath = useRef(location.pathname);
  useEffect(() => {
    if (location.pathname === lastPath.current) return;
    lastPath.current = location.pathname;
    if (new URLSearchParams(location.search).get('run')) return;
    autoRan.current = false;
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  const reset = () => {
    stream.stop();
    setPhase('idle');
    setSymbol('');
    setDraft('');
    seenResult.current = false;
  };

  if (phase === 'idle') {
    // ONLY the landing changes with the new shell. The scanning view and the analysed result below
    // are the same components either way, so a search started here behaves exactly as it does today.
    const controls = (
      <LandingControls draft={draft} onDraft={setDraft} onAnalyse={analyse}
        horizon={horizon} onHorizon={setPicked} session={session} readOnly={ctx.readOnly} />
    );
    // The panels are the live ones, built here and handed over, so the redesign cannot drift from
    // what the classic landing shows.
    const panels = (layout?: (p: LandingParts) => React.ReactNode) => (
      <LandingPanels ctx={ctx} layout={layout}
        onPick={(sym) => { setDraft(sym); if (!ctx.readOnly) analyse(sym); }} />
    );
    if (ctx.newUi) return <NewAnalysisLanding ctx={ctx} controls={controls} panels={panels} />;
    return (
      <SearchScreen draft={draft} onDraft={setDraft} onAnalyse={analyse}
        horizon={horizon} onHorizon={setPicked} session={session}
        readOnly={ctx.readOnly} ctx={ctx} />
    );
  }

  if (phase === 'results' && result) {
    return (
      <ResultScreen
        symbol={symbol} result={result} categories={categories}
        elapsed={elapsed} sources={stages.length} tab={tab} onTab={setTab}
        search={ctx.search} onBack={reset} horizon={horizon}
      />
    );
  }

  return (
    <ScanScreen
      symbol={symbol} stages={stages} state={state} log={log}
      elapsed={elapsed} percent={stream.percent} done={stream.done}
      total={stream.total} running={running} error={error}
      phase={phase} onBack={reset}
    />
  );
}

function missingCount(state: Record<string, { status: string }>): number {
  return Object.values(state).filter((s) => s.status === 'unavailable').length;
}

/* ------------------------------------------------------------ 1. search */

// ----- Landing data panels: Top Movers / Market Overview / Earnings & News ---

function compact(n: number | null | undefined): string {
  if (n == null) return '--';
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}
function Spark({ data, up }: { data: number[]; up: boolean }) {
  const col = up ? 'var(--signal-up, #22a06b)' : 'var(--signal-down, #e5556b)';
  if (!data || data.length < 2) {
    return <svg width="60" height="22"><line x1="2" y1="11" x2="58" y2="11" stroke={col} strokeWidth="1.5" strokeDasharray="3 3" opacity="0.5" /></svg>;
  }
  const w = 60, h = 22, min = Math.min(...data), max = Math.max(...data), rng = (max - min) || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / rng) * (h - 3) - 1.5}`).join(' ');
  return <svg width={w} height={h}><polyline points={pts} fill="none" stroke={col} strokeWidth="1.5" strokeLinejoin="round" /></svg>;
}
const sentTone = (s: string) =>
  s === 'positive' ? 'buy' : s === 'negative' ? 'sell' : 'flat';
const sentWord = (s: string) =>
  s === 'positive' ? 'Bullish' : s === 'negative' ? 'Bearish' : 'Neutral';

// Real company logos by ticker (parqet logo CDN). Falls back to a coloured
// monogram when it is not a ticker (e.g. "Macro") or the image fails to load,
// so the panels never show a broken image.
// A bare logo image (ticker) that simply hides itself if it fails to load --
// for inline use like the Try chips.
function TickerLogo({ sym, className }: { sym: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img className={className} alt="" loading="lazy"
      src={`https://assets.parqet.com/logos/symbol/${encodeURIComponent(sym.toUpperCase())}?format=png&size=48`}
      onError={() => setFailed(true)} />
  );
}

function Dot({ sym }: { sym: string }) {
  const s = (sym || '?').toUpperCase();
  const [failed, setFailed] = useState(false);
  if (!failed && /^[A-Z][A-Z.]{0,5}$/.test(s)) {
    return (
      <img className="anp-logo" alt={s} loading="lazy"
        src={`https://assets.parqet.com/logos/symbol/${encodeURIComponent(s)}?format=png&size=64`}
        onError={() => setFailed(true)} />
    );
  }
  const hue = (s.charCodeAt(0) * 37 + (s.charCodeAt(1) || 0) * 11) % 360;
  return <span className="anp-dot" style={{ background: `hsl(${hue} 60% 46%)` }}>{s.slice(0, 2)}</span>;
}

// The landing panels show what this server measured, or they say they have nothing. They used to be
// seeded with a representative set - NVDA at $184.72, the S&P at 5682.14, invented headlines - so the
// page "always rendered populated like the design". A reader cannot tell a specimen price from a real
// one, and these were shown as the product's own figures. A panel with no data now says so.
/** What a panel shows when it has nothing: the reason, not an empty box and never a specimen. */
function PanelNote({ loading, what }: { loading: boolean; what: string }) {
  return (
    <div className="anp-trow anp-mute" style={{ padding: '14px 0', display: 'block' }}>
      {loading ? 'Loading…' : `No ${what} right now.`}
    </div>
  );
}

const sparkFor = (up: boolean, real?: number[]) =>
  (real && real.length > 1) ? real : null;      // no series, no drawing - never an invented shape

/** The three live landing panels.
 *
 * `layout` exists because frame 2:2 does not stack these three in one strip: Top Movers sits in a
 * three-up row beside the featured symbol and the earnings calendar. Rendering the panels as parts
 * lets the redesigned landing place them where the frame puts them WITHOUT a second copy of their
 * data logic - the hooks below still run once, in this component, whoever arranges the output.
 */
export type LandingParts = { movers: React.ReactNode; overview: React.ReactNode; news: React.ReactNode };

function LandingPanels({ onPick, ctx, layout }: {
  onPick: (s: string) => void; ctx: PageContext;
  layout?: (p: LandingParts) => React.ReactNode;
}) {
  const navigate = useNavigate();
  const news = useApi<any>((s) => api2.newsDesk(s), [], { refreshMs: 120_000 });
  const upcoming = useApi<any>((s) => api2.earningsUpcoming(10, s), []);

  const [mv, setMv] = useState<'active' | 'gainers' | 'losers'>('active');
  const [ev, setEv] = useState<'news' | 'earnings'>('news');

  // The day's movers, from the provider. This panel never had a real source at all: it sorted a fixed
  // six-row specimen set and presented it as the market.
  const moversApi = useApi<any>((s) => api2.movers(25, s), [], { refreshMs: 300_000 });
  // Frame 2:2 draws a line beside each mover. /api/movers has no series - see the note below - so
  // it comes from the landing's shared sparkline payload, which answers DISABLED until the owner
  // switches it on. Disabled means the column is not rendered at all.
  const sparkApi = useApi<any>((s) => api2.landingSparklines(s), [], { refreshMs: 300_000 });
  const sparkSeries: Record<string, number[]> = sparkApi.data?.series || {};
  const hasSparks = Object.keys(sparkSeries).length > 0;
  const Spark = ({ symbol, up }: { symbol: string; up: boolean }) => {
    const pts = sparkSeries[String(symbol).toUpperCase()];
    if (!pts || pts.length < 2) return <span className="r" />;
    const lo = Math.min(...pts);
    const hi = Math.max(...pts);
    const span = hi - lo || 1;
    const d = pts.map((v, i) => {
      const x = (i / (pts.length - 1)) * 100;
      const y = 100 - ((v - lo) / span) * 100;
      return `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    return (
      <svg className="anp-spark r" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <path d={d} fill="none" strokeWidth={4} vectorEffect="non-scaling-stroke"
          stroke={up ? '#15c86e' : '#fe374a'} />
      </svg>
    );
  };
  const movers = useMemo(() => {
    const r = [...(moversApi.data?.rows || [])].filter((x: any) => x && x.symbol);
    if (mv === 'active') r.sort((a, b) => (b.volume || 0) - (a.volume || 0));
    else if (mv === 'gainers') r.sort((a, b) => (b.change_percent || 0) - (a.change_percent || 0));
    else r.sort((a, b) => (a.change_percent || 0) - (b.change_percent || 0));
    return r.slice(0, 6);
  }, [mv, moversApi.data]);

  const indices: any[] = (ctx.indices.data?.indices || []).slice(0, 6);
  const articles: any[] = (news.data?.articles || []).slice(0, 5);
  const earnings: any[] = (upcoming.data?.rows || []).slice(0, 5);

  const tab = (on: boolean) => `anp-tab ${on ? 'on' : ''}`;

  const moversCard = (
      <div className="anp-card">
        <div className="anp-head">
          <h3>Top Movers</h3>
          <div className="anp-tabs">
            <button className={tab(mv === 'active')} onClick={() => setMv('active')}>Most Active</button>
            <button className={tab(mv === 'gainers')} onClick={() => setMv('gainers')}>Gainers</button>
            <button className={tab(mv === 'losers')} onClick={() => setMv('losers')}>Losers</button>
          </div>
        </div>
        <div className={`anp-trow anp-m5 anp-thead${hasSparks ? ' anp-m5s' : ''}`}>
          <span>Symbol</span><span className="r">Price</span><span className="r">Change</span>
          {/* The Chart column exists only when a series does. /api/movers carries none of its own. */}
          {hasSparks && <span className="r">Chart</span>}
          <span className="r">Volume</span>
        </div>
        {!movers.length && <PanelNote loading={moversApi.loading} what="movers" />}
        {movers.map((m) => {
          const up = m.change_percent >= 0;
          return (
            <button key={m.symbol} className={`anp-trow anp-m5 anp-click${hasSparks ? ' anp-m5s' : ''}`}
              onClick={() => onPick(m.symbol)}>
              <span className="anp-sym"><Dot sym={m.symbol} /> {m.symbol}</span>
              <span className="r">{money(m.price)}</span>
              <span className={`r ${up ? 'pos' : 'neg'}`}>{signedPct(m.change_percent)}</span>
              {hasSparks && <Spark symbol={m.symbol} up={up} />}
              <span className="r anp-mute">{compact(m.volume)}</span>
              {/* The movers feed carries no price series, so this cell stays empty rather than
                  drawing a shape that is not this symbol's. */}
            </button>
          );
        })}
      </div>

  );

  const overviewCard = (
      <div className="anp-card">
        <div className="anp-head">
          <h3>Market Overview</h3>
          <div className="anp-tabs"><button className="anp-tab on">Indices</button></div>
        </div>
        <div className="anp-trow anp-thead">
          {/* No "Day Chart" column: /api/indices carries no price series, so it rendered a dash on
              every row. An empty column implies data that is coming; no column states the truth. */}
          <span>Index</span><span className="r">Price</span><span className="r">Change</span>
        </div>
        {!indices.length && <PanelNote loading={ctx.indices.loading} what="index prices" />}
        {indices.map((r) => {
          const up = (r.change_percent ?? 0) >= 0;
          return (
            <div key={r.instrument || r.label} className="anp-trow">
              <span className="anp-sym">
                {r.label}
                {/* The figure is the PROXY's price, not the index level: SPY at 710 is not the S&P
                    at 6,800. Naming the instrument is the difference between a wrong number and a
                    correct one. */}
                {r.is_proxy && !!r.instrument && <em className="anp-via">via {r.instrument}</em>}
              </span>
              <span className="r">{r.value != null ? num(r.value, 2) : <span className="anp-mute">unavailable</span>}</span>
              <span className={`r ${r.change_percent == null ? 'anp-mute' : up ? 'pos' : 'neg'}`}>
                {r.change_percent != null ? signedPct(r.change_percent) : 'no reading'}
              </span>
            </div>
          );
        })}
      </div>

  );

  const newsCard = (
      <div className="anp-card">
        <div className="anp-head">
          <h3>Earnings &amp; News</h3>
          <div className="anp-head-right">
            <div className="anp-tabs">
              <button className={tab(ev === 'news')} onClick={() => setEv('news')}>Latest News</button>
              <button className={tab(ev === 'earnings')} onClick={() => setEv('earnings')}>Earnings</button>
            </div>
            <button className="anp-viewall" onClick={() => navigate('/news')}>View All →</button>
          </div>
        </div>
        {ev === 'news' && !articles.length && <PanelNote loading={news.loading} what="headlines" />}
        {ev === 'earnings' && !earnings.length && <PanelNote loading={upcoming.loading} what="upcoming earnings" />}
        {ev === 'news'
          ? articles.map((a, i) => (
            <a key={a.id || i} className="anp-news" href={safeHref(a.url)}
              target={safeHref(a.url) ? '_blank' : undefined} rel="noopener noreferrer"
              onClick={(e) => { if (!safeHref(a.url)) e.preventDefault(); if (a.symbols?.[0]) onPick(a.symbols[0]); }}>
              <Dot sym={(a.symbols?.[0] || a.provider || '?').toString()} />
              <div className="anp-news-b">
                <div className="anp-news-h">{a.headline}</div>
                <div className="anp-news-m">{(a.symbols?.[0] || a.provider)} · {a.time_label}</div>
              </div>
              <span className={`ets-badge ${sentTone(a.sentiment)}`}>{sentWord(a.sentiment)}</span>
            </a>
          ))
          : earnings.map((r, i) => (
            <button key={r.symbol || i} className="anp-news anp-click" onClick={() => onPick(r.symbol)}>
              <Dot sym={r.symbol} />
              <div className="anp-news-b">
                <div className="anp-news-h">{r.name || r.symbol}</div>
                <div className="anp-news-m">{r.symbol} · {r.label || r.report_date || r.date || ''}</div>
              </div>
              <span className="ets-badge flat">{r.timing || r.reporting_time || r.label || 'Earnings'}</span>
            </button>
          ))}
      </div>
  );

  const parts: LandingParts = { movers: moversCard, overview: overviewCard, news: newsCard };
  if (layout) return <>{layout(parts)}</>;
  return <div className="anp-grid">{moversCard}{overviewCard}{newsCard}</div>;
}

/**
 * The controls that START an analysis: the horizon switch, the ticker box and the suggestion chips.
 *
 * They live in ONE component because two landings now show them - the old SearchScreen and the
 * redesigned NewAnalysisLanding. Copying them would mean two versions of the thing that launches a
 * run, and the second copy is the one that quietly stops matching.
 */
function LandingControls({
  draft, onDraft, onAnalyse, horizon, onHorizon, session, readOnly,
}: {
  draft: string; onDraft: (v: string) => void; onAnalyse: (s: string) => void;
  horizon: Horizon; onHorizon: (h: Horizon) => void; session?: string; readOnly?: boolean;
}) {
  const chip = horizon === 'TODAY' ? 'Today · ET'
    : horizon === 'SWING' ? 'Swing · ~5 days' : 'Next session · ET';
  return (
    <>
          {/* Chosen before the run, because it changes what the run reads. */}
          <div className="anh-horizon">
            <HorizonSwitch value={horizon} onChange={onHorizon} session={session} />
            <span className="anh-chip"><CalendarDays size={13} /> {chip}</span>
          </div>
          <p className="anh-basis">{HORIZON_COPY[horizon].basis}</p>

          <form className="anh-search"
            onSubmit={(e) => { e.preventDefault(); onAnalyse(draft); }}>
            <Search size={17} color={DIM} />
            <input value={draft} onChange={(e) => onDraft(e.target.value.toUpperCase())}
              placeholder="Search ticker (e.g. NVDA)" aria-label="Ticker"
              spellCheck={false} autoFocus
              autoComplete="off" autoCorrect="off" autoCapitalize="characters"
              name="ticker-search" />
            {draft && (
              <button type="button" className="icon-btn" onClick={() => onDraft('')}
                aria-label="Clear"><X size={14} /></button>
            )}
            <button type="submit" className="anh-go" disabled={!draft.trim() || readOnly}
              title={readOnly ? 'View-only account' : undefined}>
              Analyze <ArrowRight size={16} />
            </button>
          </form>

          {readOnly ? (
            <div className="anh-try" style={{ opacity: 0.7 }}>
              Running analysis is disabled for view-only accounts.
            </div>
          ) : (
            <div className="anh-try">
              <span>Try:</span>
              {SUGGESTED.map((s) => (
                <button key={s} className="anh-chip-try"
                  onClick={() => { onDraft(s); onAnalyse(s); }}>
                  <TickerLogo sym={s} className="anh-try-logo" /> {s}
                </button>
              ))}
            </div>
          )}
    </>
  );
}

function SearchScreen({
  draft, onDraft, onAnalyse, horizon, onHorizon, session, readOnly, ctx,
}: {
  draft: string; onDraft: (v: string) => void; onAnalyse: (s: string) => void;
  horizon: Horizon; onHorizon: (h: Horizon) => void; session?: string;
  readOnly?: boolean; ctx: PageContext;
}) {
  return (
    <div className="anh">
      <div className="anh-grid">
        <div className="anh-left">
          <div className="anh-eyebrow">Global Market Intelligence</div>
          <h1 className="anh-title">For Smarter <span>Decisions</span></h1>
          <p className="anh-sub">Real data. Deep analysis. AI-powered insights for US markets.</p>

          <LandingControls draft={draft} onDraft={onDraft} onAnalyse={onAnalyse}
            horizon={horizon} onHorizon={onHorizon} session={session} readOnly={readOnly} />
        </div>

        <div className="anh-right">
          {/* The four figures on this artwork used to be painted into it. The image is now the globe
              alone and the cards are real elements, so what a visitor reads first is measured. */}
          <img className="anh-heroimg" src={analysisHero} alt="" aria-hidden="true"
            loading="eager" />
          <HeroStatCards ctx={ctx} />
        </div>
      </div>

      <div className="anh-stats">
        {/* FOUR, not twelve: Unusual Whales, Finnhub, Finviz and SEC/EDGAR. The figure was simply
            wrong, and a wrong count is a claim about the product a reader cannot check. */}
        <div className="anh-stat"><span className="anh-sic green"><Database size={18} /></span>
          <div><b>4</b><span>Data Sources</span></div></div>
        {/* How many parameters a model scores is a model internal (B.1), so it is an administrator's
            figure, not a selling point on a landing page. */}
        {ctx.isAdmin && (
        <div className="anh-stat"><span className="anh-sic violet"><Gauge size={18} /></span>
          <div><b>20</b><span>Scored Parameters</span></div></div>
        )}
        <div className="anh-stat"><span className="anh-sic red"><BarChart3 size={18} /></span>
          <div><b>US</b><span>Market Coverage</span></div></div>
        <div className="anh-stat"><span className="anh-sic amber"><Zap size={18} /></span>
          <div><b>Live</b><span>Prices &amp; Flow</span></div></div>
      </div>

      <LandingPanels ctx={ctx} onPick={(s) => { onDraft(s); if (!readOnly) onAnalyse(s); }} />
    </div>
  );
}

/* ------------------------------------------------- 2-4. searching & scan */

/**
 * Short names for the ring.
 *
 * The full labels are sentences -- "Institutional Holdings", "IV & Options
 * Greeks" -- and twelve of them around a circle either overlap or get cut off
 * mid-word, which is worse than an abbreviation. The long name stays on the
 * node's tooltip and on the results panel, where there is room for it.
 */
// Boxes are labelled by the scored parameter each source primarily feeds, so
// the scan mirrors the Category Breakdown rather than raw provider names.
const SHORT: Record<string, string> = {
  options_flow: 'Options Flow',
  gex: 'Key Levels',
  volume: 'Volume & P/C',
  iv_greeks: 'Implied Vol',
  price_action: 'Price Action',
  relative_strength: 'EMA / RSI',
  macro: 'Exp. Move',
  sec_filings: 'SEC Filings',
  earnings: 'Earnings',
  insider: 'Insider',
  institutional: 'Fund 13F',
  news: 'Unusual Act.',
};

/**
 * One colour per source, carried by its icon, its wire and its travelling ball.
 *
 * Twelve identical grey dots converging on a globe say "something is moving"
 * and nothing else. Giving each source its own hue means the ball arriving at
 * the core is traceable to the feed that sent it, which is the only reason to
 * animate the wires at all.
 *
 * Hues are spread around the wheel in the ring's own order, so neighbours are
 * never near-duplicates of each other.
 */
const HUE: Record<string, number> = {
  sec_filings: 202,
  insider: 168,
  institutional: 142,
  options_flow: 96,
  gex: 52,
  price_action: 30,
  relative_strength: 8,
  macro: 338,
  iv_greeks: 310,
  volume: 280,
  news: 250,
  earnings: 224,
};

function hueOf(key: string, index: number, count: number): number {
  return HUE[key] ?? Math.round((index / Math.max(count, 1)) * 360);
}

/** Where each source sits on the ring, in degrees clockwise from the top. */
function nodeAt(index: number, count: number, radius: number) {
  const rad = ((index / count) * 360 - 90) * Math.PI / 180;
  return { x: 50 + Math.cos(rad) * radius, y: 50 + Math.sin(rad) * radius };
}

function ScanScreen({
  symbol, stages, state, log, elapsed, percent, done, total, running, error,
  phase, onBack,
}: {
  symbol: string; stages: Stage[]; state: Record<string, any>;
  log: any[]; elapsed: number; percent: number; done: number; total: number;
  running: boolean; error: string | null; phase: Phase; onBack: () => void;
}) {
  const connecting = phase === 'searching';
  const shown = stages.length ? stages : PLACEHOLDER;
  const count = shown.length;

  return (
    <div className="an-scan an-scan-full">
      {/* Full-screen animated globe behind the overlay. Decorative, and names
          no data source, so the parameters stay private. */}
      <video className="an-stage-vid" src={scanVideo}
        autoPlay loop muted playsInline aria-hidden="true" />
      <div className="an-stage-scrim" />

      <button className="an-back" onClick={onBack}>
        <X size={14} /> Cancel
      </button>

      <div className="an-scan-head">
        <h2>{connecting ? 'Searching' : 'Analyzing'} <span>{symbol}</span>…</h2>
        <p>{connecting
          ? 'Connecting to the analysis engine'
          : 'Our agents are gathering and processing the market data'}</p>
      </div>

      {error && <div className="an-scan-err">{error}</div>}

      <div className="an-progress">
        <div className="ap-track"><i style={{ width: `${percent}%` }} /></div>
        <div className="an-progress-foot">
          <span>Analyzing {symbol} — gathering and processing data</span>
          <b>{percent}%</b>
        </div>
        <div className="an-progress-sub">
          {done}/{total} agents complete · {elapsed.toFixed(1)}s elapsed
        </div>
      </div>
    </div>
  );
}

// Shown for the instant before the stream names the real stages, so the
// layout does not jump when they arrive.
const PLACEHOLDER: Stage[] = [
  { key: 'sec_filings', label: 'SEC Filings', sub: 'Form 4, 13D/13G', feeds: [] },
  { key: 'insider', label: 'Insider Trades', sub: 'open-market only', feeds: [] },
  { key: 'institutional', label: 'Institutional Holdings', sub: '13F', feeds: [] },
  { key: 'options_flow', label: 'Options Flow', sub: 'sweeps, blocks', feeds: [] },
  { key: 'gex', label: 'GEX / OI Data', sub: 'dealer positioning', feeds: [] },
  { key: 'price_action', label: 'Price Action', sub: 'structure, levels', feeds: [] },
  { key: 'relative_strength', label: 'Relative Strength', sub: 'vs SPY', feeds: [] },
  { key: 'macro', label: 'Market Environment', sub: 'index trend', feeds: [] },
  { key: 'iv_greeks', label: 'IV & Options Greeks', sub: 'volatility', feeds: [] },
  { key: 'volume', label: 'Volume & Liquidity', sub: 'put/call, expiry', feeds: [] },
  { key: 'news', label: 'News & Sentiment', sub: 'headline tone', feeds: [] },
  { key: 'earnings', label: 'Earnings & Estimates', sub: 'reported vs consensus', feeds: [] },
];

/**
 * The symbol's price, beside its score.
 *
 * A score with no price is a verdict without the thing it is a verdict on --
 * "BUY 66" means nothing until you know whether that is at 210 or at 260. It
 * refreshes on its own because the analysis behind it took a minute to run,
 * and a price frozen at the moment the scan started would be the one number on
 * the screen quietly going stale.
 *
 * Outside regular hours the extended print leads: the top-level fields stay on
 * the last completed session, so taking them blindly would show yesterday's
 * close as the current price.
 */
function LivePrice({ symbol }: { symbol: string }) {
  const quote = useApi<any>(
    (s) => (symbol ? api.quote(symbol, s) : Promise.resolve(null)),
    [symbol],
    { refreshMs: symbol ? 20000 : undefined },
  );

  const q = quote.data;
  const fresh = q?.freshness || null;
  // How old the shown price is, in human terms -- so a weekend close reads
  // "2 days ago" rather than looking like a live quote.
  const ageLabel = (() => {
    const iso = fresh?.as_of;
    if (!iso || fresh?.kind === 'LIVE') return null;
    const ms = Date.now() - new Date(iso).getTime();
    if (!(ms > 0)) return null;
    const mins = Math.round(ms / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 48) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  })();
  // Two different numbers, and conflating them is how a screen lies: `price`
  // is the last regular-session trade, `extended` is the pre- or post-market
  // book, quoted against the regular close. Both are shown, each labelled.
  const live = q?.extended || null;
  const regular = q?.price ?? null;
  const session = q?.market?.label || null;
  const isRegular = q?.market?.session === 'REGULAR';
  // Outside the session an extended quote only counts if it is actually a
  // different print; an echo of the close is noise, not a pre-market rate.
  const showExt =
    !!live && live.price != null && !isRegular &&
    (regular == null || Math.abs(live.price - regular) > 0.004);

  if (quote.initialLoading) {
    return <span className="an-price wait">Fetching price…</span>;
  }
  if (regular == null && !showExt) return null;

  return (
    <span className="an-price">
      {regular != null && (
        <>
          <b>{money(regular)}</b>
          <em className={(q?.change_percent ?? 0) >= 0 ? 'pos' : 'neg'}>
            {q?.change != null ? `${q.change >= 0 ? '+' : ''}${num(q.change)} ` : ''}
            ({signedPct(q?.change_percent)})
          </em>
          <i>{isRegular ? session || 'Last' : 'Close'}</i>
        </>
      )}

      {showExt && (
        <span className="an-price-ext">
          <i>{live.session_label || session || 'Extended'}</i>
          <b>{money(live.price)}</b>
          <em className={(live.change_percent ?? 0) >= 0 ? 'pos' : 'neg'}>
            {live.change != null ? `${live.change >= 0 ? '+' : ''}${num(live.change)} ` : ''}
            ({signedPct(live.change_percent)})
          </em>
        </span>
      )}

      {fresh && fresh.kind !== 'LIVE' && (ageLabel || fresh.label) && (
        <span className="an-price-fresh" title={fresh.detail || ''}>
          {fresh.label || 'Delayed'}{ageLabel ? ` · ${ageLabel}` : ''}
        </span>
      )}
    </span>
  );
}

/* ------------------------------------------------------------ 6. result */

// A.25: the tabs whose whole content is the scored parameters. Hidden from anyone the server does not send them to.
const MODEL_TABS = ['all', 'price', 'earnings', 'institutional', 'news'];

const TABS: [string, string][] = [
  ['overview', 'Overview'],
  // Every scored parameter in one list. The grouped tabs below are for
  // reading one area closely; this is for seeing the whole model at once,
  // which is what a finished run is actually being asked.
  ['all', 'All parameters'],
  // No Options tab: every option parameter is in the list above and the
  // Disparity tab reads the tape closely. A third view of the same numbers
  // was three places to check one thing.
  ['disparity', 'Disparity'],
  ['price', 'Price Action'],
  ['earnings', 'Earnings'],
  ['institutional', 'Institutional'],
  ['news', 'News'],
];

/**
 * Why this analysis came out the way it did, from the call that was saved.
 *
 * The run writes its call to storage a moment after it finishes -- pricing
 * the stock and SPY for the record happens off the stream -- so this asks
 * again for a few seconds until the fresh one lands, then stops. It shows
 * the saved call rather than re-deriving reasons here, so the explanation on
 * screen is exactly the one the scorecard will later judge.
 */
function StoredWhy({ symbol, horizon, result }: {
  symbol: string; horizon: Horizon; result: any;
}) {
  // The "Why" panel is built from the SAME live result the Score gauge shows,
  // so the two can never disagree. Previously it fetched the latest stored
  // call of any origin, which could be a newer background board scan with a
  // different score/confidence than the analysis on screen -- the app then
  // showed two numbers for one verdict. We still fetch the analysis-origin
  // stored call, but only to enrich: its id (for "Explain in plain English"),
  // any saved explanation, and its outcome once graded.
  const [found, setFound] = useState(false);
  const opened = useRef(Date.now());
  const call = useApi<any>(
    (sig) => api2.callLatest(symbol, horizon, sig, 'analysis'),
    [symbol, horizon],
    { refreshMs: found ? undefined : 2500 },
  );
  const stored = call.data?.call;
  // Only trust the stored call as this run's when it is fresh AND its score
  // matches the result on screen; otherwise it is a different run and we take
  // nothing numeric from it.
  const sameRun = !!stored?.made_at
    && new Date(stored.made_at).getTime() >= opened.current - 120000
    && stored.direction_score != null && result.direction_score != null
    && Math.abs(stored.direction_score - result.direction_score) < 0.5;

  useEffect(() => {
    if (sameRun) setFound(true);
  }, [sameRun]);
  useEffect(() => {
    const t = setTimeout(() => setFound(true), 20000);
    return () => clearTimeout(t);
  }, []);

  const built = buildWhyFromResult(symbol, horizon, result, sameRun ? stored : null);
  return <div className="an-why"><WhyCall call={built} /></div>;
}

/**
 * Assemble a WhyCall-shaped object from the live analysis result, so the
 * header figures and the toward/against split are exactly the numbers the
 * Score gauge is drawn from. The stored call, when it is the same run, lends
 * its id (for the explanation request), any saved explanation and its outcome.
 */
// Intraday dual-model weights (BUY, SELL) per parameter, mirroring the backend.
const IM_WEIGHTS: Record<string, [number, number]> = {
  vwap: [8, 10], intraday_trend: [7, 7], ema_trend: [7, 7], opening_range: [6, 7],
  price_action: [5, 6], rsi: [4, 5], options_flow: [9, 7], unusual_activity: [6, 4],
  volume_pcr: [5, 3], key_levels: [5, 4], flow_by_expiry: [3, 2], oi_positioning: [2, 0],
  relative_volume: [8, 7], market_direction: [7, 10], relative_strength_day: [5, 7],
  sector_strength: [5, 6], analyst_action: [4, 4], earnings_results: [3, 3],
  insider_activity: [2, 2],
};
function imBias(s: any): number | null {
  if (s.bias != null) return s.bias;
  if (s.points != null && s.weight) return Math.max(-1, Math.min(1, s.points / s.weight));
  return null;
}
function computeBuySell(sigs: any[]): any {
  const rows: any[] = [];
  let bn = 0, sn = 0, bd = 0, sd = 0;
  for (const s of sigs) {
    const w = IM_WEIGHTS[s.name];
    if (!w) continue;
    const [bw, sw] = w;
    const b = imBias(s);
    const avail = !!s.available && b != null;
    const bb = b || 0;
    rows.push({
      name: s.name, label: s.label || s.name, buy_weight: bw, sell_weight: sw,
      buy_points: avail ? Math.round(Math.max(0, bb) * bw * 10) / 10 : null,
      sell_points: avail ? Math.round(Math.max(0, -bb) * sw * 10) / 10 : null,
      available: avail,
    });
    if (avail) {
      if (bw) { bn += Math.max(0, bb) * bw; bd += bw; }
      if (sw) { sn += Math.max(0, -bb) * sw; sd += sw; }
    }
  }
  rows.sort((a, b) => (b.buy_weight + b.sell_weight) - (a.buy_weight + a.sell_weight));
  return {
    buy_score: bd ? Math.round(1000 * bn / bd) / 10 : null,
    sell_score: sd ? Math.round(1000 * sn / sd) / 10 : null,
    buy_points_total: Math.round(bn * 10) / 10, buy_weight_total: Math.round(bd * 10) / 10,
    sell_points_total: Math.round(sn * 10) / 10, sell_weight_total: Math.round(sd * 10) / 10,
    rows,
  };
}

function buildWhyFromResult(symbol: string, horizon: Horizon, result: any,
                            stored: any): any {
  const sigs: any[] = result.signals || [];
  const dir = sigs.filter((x) => x.directional && x.available && x.points != null);
  const item = (x: any) => ({
    label: x.label, points: x.points, points_label: x.points_label, detail: x.detail,
  });
  const toward = dir.filter((x) => x.points > 0)
    .sort((a, b) => b.points - a.points).map(item);
  const against = dir.filter((x) => x.points < 0)
    .sort((a, b) => a.points - b.points).map(item);
  const missing = sigs.filter((x) => !x.available).map((x) => x.label);
  return {
    id: stored?.id ?? 0,
    symbol,
    horizon,
    origin: 'analysis',
    made_at: stored?.made_at ?? new Date().toISOString(),
    target_date: stored?.target_date ?? null,
    decision: result.decision,
    direction_score: result.direction_score,
    confidence: result.confidence,
    agreement_pct: result.agreement_pct,
    coverage_pct: result.coverage_pct,
    price_at_call: stored?.price_at_call ?? null,
    model_version: stored?.model_version ?? null,
    why: {
      for: toward,
      against,
      missing,
      blocked: result.blocked_reasons || [],
      explanation: stored?.why?.explanation ?? null,
      buy_sell: stored?.why?.buy_sell ?? computeBuySell(sigs),
    },
    outcome: stored?.outcome,
  };
}

function ResultScreen({
  symbol, result, categories, elapsed, sources, tab, onTab, search, onBack,
  horizon,
}: {
  symbol: string; result: any; categories: Category[]; elapsed: number;
  sources: number; tab: string; onTab: (t: string) => void;
  search: string; onBack: () => void; horizon: Horizon;
}) {
  const blocked = result.actionable === false;
  const tone = blocked ? AMBER
    : result.decision?.includes('BUY') ? GREEN
      : result.decision?.includes('SELL') ? RED : AMBER;

  const signals: DirSignalLike[] = result.signals || [];
  // A.25 (owner decision 2026-11-13): the scored parameters and their weights are the model itself, and the server now
  // sends them to administrators only. With none present the screen keeps the verdict and the insights and drops the
  // working: no category breakdown, no parameter list, no parameter tabs. Nothing below this panel changes.
  const showModel = signals.length > 0;
  // The categories carry parameter names; this is how a row finds its own.
  const byName: Record<string, DirSignalLike> = Object.fromEntries(
    signals.map((s) => [s.name, s]));
  // "all" is not a group: it is every signal the model scored, heaviest
  // first, so the parameters that actually moved the number lead.
  const shown = tab === 'overview' ? []
    : tab === 'all'
      ? [...signals].sort((a, b) => (b.weight || 0) - (a.weight || 0))
      : signals.filter((x) => (TAB_PARAMS[tab] || []).includes(x.name));

  return (
    <div className="page an-result">
      <div className="an-result-head">
        <button className="an-back inline" onClick={onBack}>
          <Search size={13} /> New search
        </button>
        <h2>{symbol}</h2>
        <span className="an-hz-tag" title={HORIZON_COPY[horizon].basis}>
          {HORIZON_COPY[horizon].label} outlook
        </span>
        <LivePrice symbol={symbol} />
        <span className="an-elapsed">
          {sources} sources answered{showModel ? ` · ${signals.length} parameters` : ''} ·
          {' '}{elapsed.toFixed(1)}s
        </span>
      </div>

      <StoredWhy symbol={symbol} horizon={horizon} result={result} />

      {/* Intraday Model v2 -- the shared intraday scorer (/api/intraday/*),
          shown for the TODAY horizon when the backend flag is on (self-hides
          otherwise). Read-only; does not change the existing score above. */}
      {horizon === 'TODAY' && <IntradayV2Panel symbol={symbol} />}

      <div className="an-result-top">
        <Panel title="Score" noBody>
          <div className="an-score">
            <Ring value={result.direction_score} colour={tone} />
            <div>
              <div className="an-decision" style={{ color: tone }}>{result.decision}</div>
              <div className="an-conf">Confidence {Math.round(result.confidence)}%</div>
              <div className="an-cov">{result.coverage_pct}% of the model returned data</div>
            </div>
          </div>
          {blocked && (
            <div className="dir-block">
              <div className="db-head">Not a tradeable setup right now</div>
              <ul>
                {(result.blocked_reasons || []).map((r: string) => <li key={r}>{r}</li>)}
              </ul>
            </div>
          )}
        </Panel>

        {showModel && (
        <Panel title="Category Breakdown" noBody
          right={<span className="ac-total">100 pts</span>}>
          <div className="ai-cats">
            {/* Grouped as the model is weighted: eighty points of market and
                price, twenty of company and ownership. Without the headings a
                reader has to add twenty rows to see the split the weights
                were actually built around. */}
            {categories.map((c, index) => (
              <React.Fragment key={c.key}>
                {(index === 0
                  || (categories[index - 1].group || '') !== (c.group || '')) && (
                  <div className="ac-group">
                    <b>{c.group || 'Other'}</b>
                    <em>
                      {categories
                        .filter((x) => (x.group || '') === (c.group || ''))
                        .reduce((t, x) => t + (x.weight_possible || 0), 0)} pts
                    </em>
                  </div>
                )}
              <div className="ai-cat" title={c.detail || ''}>
                <span className="ac-label">
                  {c.label}
                  {c.weight_possible != null && (
                    <em className="ac-weight">{c.weight_possible}pt</em>
                  )}
                </span>
                <span className="ac-track">
                  <i style={{
                    width: `${c.score ?? 0}%`,
                    background: !c.available ? 'var(--panel-2)'
                      : (c.score ?? 50) >= 60 ? GREEN
                        : (c.score ?? 50) <= 40 ? RED : AMBER,
                  }} />
                </span>
                <span className="ac-val">
                  {c.available ? c.score : '--'}
                  {c.available && c.points != null && (
                    <em className={c.points >= 0 ? 'pos' : 'neg'}>
                      {c.points > 0 ? '+' : ''}{c.points}
                    </em>
                  )}
                  {/* Sizing categories have a reading but cast no vote, and
                      a signed number beside them read as one. */}
                  {c.available && c.points == null && (
                    <em className="dim">sizing only</em>
                  )}
                </span>
              </div>

              {/* The parameters underneath, with the points each one carries.
                  A category worth fifty-two points is eight readings that can
                  disagree with each other; the total alone hides which of
                  them moved it. */}
              {(c.weight_possible || 0) > 0
                // A category of one parameter is that parameter: listing it
                // underneath printed "Implied Volatility" twice, once as the
                // heading and once as its own only child.
                && ((c as any).parameters || []).filter(
                  (n: string) => (byName[n]?.weight || 0) > 0).length > 1 && (
                <div className="ac-parts">
                  {((c as any).parameters || [])
                    .map((name: string) => byName[name])
                    .filter(Boolean)
                    .filter((s: DirSignalLike) => (s.weight || 0) > 0)
                    .sort((a: DirSignalLike, b: DirSignalLike) =>
                      (b.weight || 0) - (a.weight || 0))
                    .map((s: DirSignalLike) => (
                      <div className="ac-part" key={s.name}>
                        <span>{s.label}<em>{s.weight}pt</em></span>
                        <b className={!s.available ? 'dim'
                          : (s.points || 0) > 0 ? 'pos'
                            : (s.points || 0) < 0 ? 'neg' : 'dim'}>
                          {!s.available ? 'no data'
                            : s.directional ? s.points_label : 'sizing only'}
                        </b>
                      </div>
                    ))}
                </div>
              )}
              </React.Fragment>
            ))}
          </div>
        </Panel>
        )}
      </div>

      <div className="an-tabs">
        {(showModel ? TABS : TABS.filter(([k]) => !MODEL_TABS.includes(k))).map(([k, label]) => (
          <button key={k} className={`ai-tab ${tab === k ? 'on' : ''}`}
            onClick={() => onTab(k)}>{label}</button>
        ))}
      </div>

      {/* One tab, one subject. These panels used to render on every tab, so
          Overview showed the whole page and every other tab showed Overview
          plus its own -- which made the tab strip decorative. */}
      {tab === 'disparity' && <Disparity symbol={symbol} />}

      {tab === 'earnings' && (
        <>
          <Panel title="Next Report" icon={<CalendarDays size={13} />}>
            <EarningsPreview symbol={symbol} />
          </Panel>
          <Dividends symbol={symbol} />
        </>
      )}

      {tab === 'institutional' && (
        <>
          <InstitutionalPanel symbol={symbol} />
          <FundActivity symbol={symbol} />
        </>
      )}

      {tab === 'news' && (
        <>
          <SymbolNews symbol={symbol} />
          <CorporateEvents symbol={symbol} />
        </>
      )}

      <Panel title={tab === 'overview' ? 'Key Insights' : 'Parameters'} noBody>
        {tab === 'overview' ? (
          <div className="an-insights">
            {(result.reasons || []).map((r: string) => (
              <div className="an-insight" key={r}>
                <CheckCircle2 size={12} color={GREEN} />{r}
              </div>
            ))}
            {!result.reasons?.length && (
              <div className="hint">No decisive signal in the available data.</div>
            )}
            <div className="hint">{result.note}</div>

            {showModel && (<>
            <div className="an-all-head">
              <b>All {signals.length} parameters</b>
              <span>
                {signals.filter((x) => x.available).length} measured ·
                weighted as the model scores them
              </span>
            </div>
            <div className="an-params">
              {[...signals]
                .sort((a, b) => (b.weight || 0) - (a.weight || 0))
                .map((x) => (
                  <div className="an-param" key={x.name}>
                    <div className="ap-head">
                      <b>{x.label}<em className="ap-weight">{x.weight}pt</em></b>
                      <span style={{
                        color: !x.available ? DIM
                          : x.points > 0 ? GREEN : x.points < 0 ? RED : DIM,
                      }}>
                        {x.available
                          ? (x.directional ? x.points_label : 'sizing only')
                          : 'no data'}
                      </span>
                    </div>
                    <p>{x.available ? x.detail : x.unavailable_reason}</p>
                    {/* Two different questions, answered separately: why the
                        score came out this way, and why this parameter is
                        allowed to matter as much as it does. */}
                    {(x as any).weight_reason && (
                      <div className="ap-weight-why">
                        <b>Why {x.weight} points:</b> {(x as any).weight_reason}
                      </div>
                    )}
                    {x.available && (x as any).score_reason && (
                      <div className="ap-score-why">
                        <b>Why {x.directional
                          ? `${x.points > 0 ? '+' : ''}${x.points.toFixed(1)}`
                          : 'this counts'}:</b> {(x as any).score_reason}
                      </div>
                    )}
                    <div className="ap-rule">{x.rule}</div>
                    <ParameterWhy symbol={symbol} parameter={x.name}
                      label={x.label} horizon={horizon} />
                  </div>
                ))}
            </div>
            </>)}
          </div>
        ) : !showModel ? (
          <div className="hint">The scored parameters are not shown here.</div>
        ) : (
          <div className="an-params">
            {shown.map((s) => (
              <div className="an-param" key={s.name}>
                <div className="ap-head">
                  <b>{s.label}</b>
                  <span style={{
                    color: !s.available ? DIM
                      : s.points > 0 ? GREEN : s.points < 0 ? RED : DIM,
                  }}>
                    {s.available
                      ? (s.directional ? s.points_label : 'sizing only')
                      : 'no data'}
                  </span>
                </div>
                <p>{s.available ? s.detail : s.unavailable_reason}</p>
                {(s as any).weight_reason && (
                  <div className="ap-weight-why">
                    <b>Why {s.weight} points:</b> {(s as any).weight_reason}
                  </div>
                )}
                {s.available && (s as any).score_reason && (
                  <div className="ap-score-why">
                    <b>Why {s.directional
                      ? `${s.points > 0 ? '+' : ''}${s.points.toFixed(1)}`
                      : 'this counts'}:</b> {(s as any).score_reason}
                  </div>
                )}
                <div className="ap-rule">{s.rule}</div>
                <ParameterWhy symbol={symbol} parameter={s.name}
                  label={s.label} horizon={horizon} />
              </div>
            ))}
            {!shown.length && <div className="hint">Nothing in this group.</div>}
          </div>
        )}
      </Panel>

      <div className="an-actions">
        <NavLink className="ai-cta" to={`/earnings/${symbol}${search}`}>
          Full breakdown <ArrowRight size={13} />
        </NavLink>
        <NavLink className="ai-cta ghost" to={`/watchlist${search}`}>
          <Star size={13} /> Watchlist
        </NavLink>
      </div>
    </div>
  );
}

// Which parameters each result tab shows. Grouping only -- the weights and
// scores are the model's.
const TAB_PARAMS: Record<string, string[]> = {
  disparity: ['disparity'],
  price: ['price_action', 'ema_trend', 'rsi',
          'vwap', 'opening_range', 'intraday_trend', 'gap_hold',
          'close_location', 'relative_strength_day', 'relative_volume',
          'after_hours'],
  earnings: ['earnings_results', 'dividend_trend'],
  institutional: ['insider_activity', 'fund_flows'],
  news: ['event_radar', 'merger_activity', 'funding_activity'],
};

function Ring({ value, colour }: { value: number | null; colour: string }) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const filled = Math.max(0, Math.min(100, value ?? 0)) / 100 * c;
  return (
    <div className="ai-ring">
      <svg viewBox="0 0 100 100" width="100" height="100">
        <circle cx="50" cy="50" r={r} fill="none" strokeWidth="8" stroke="var(--panel-2)" />
        <circle cx="50" cy="50" r={r} fill="none" strokeWidth="8"
          stroke={colour} strokeLinecap="round"
          strokeDasharray={`${filled} ${c - filled}`}
          transform="rotate(-90 50 50)" />
      </svg>
      <div className="ar-center">
        <b style={{ color: colour }}>{value === null ? '--' : Math.round(value)}</b>
        <span>/ 100</span>
      </div>
    </div>
  );
}
