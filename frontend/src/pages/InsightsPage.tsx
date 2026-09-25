import React, { useEffect, useRef, useState } from 'react';
import {
  Activity, ArrowRight, BarChart3, Building2, CalendarDays, CheckCircle2,
  Database, FileText, Gauge, Globe, LineChart, Loader2, Newspaper, Search,
  Star, TrendingUp, User, X, XCircle,
} from 'lucide-react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import WorldGlobe from '../components/WorldGlobe';
import { api, api2 } from '../api/client';
import WhyCall from '../components/WhyCall';
import CorporateEvents from '../components/CorporateEvents';
import Disparity from '../components/Disparity';
import ParameterWhy from '../components/ParameterWhy';
import EarningsPreview from '../components/EarningsPreview';
import InstitutionalPanel from '../components/InstitutionalPanel';
import SymbolNews from '../components/SymbolNews';
import Dividends from '../components/Dividends';
import FundActivity from '../components/FundActivity';
import HorizonSwitch, { HORIZON_COPY, defaultHorizon } from '../components/HorizonSwitch';
import type { Horizon } from '../components/HorizonSwitch';
import { useApi } from '../hooks/useApi';
import { money, num, signedPct } from '../lib/format';
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
    return (
      <SearchScreen draft={draft} onDraft={setDraft} onAnalyse={analyse}
        horizon={horizon} onHorizon={setPicked} session={session}
        readOnly={ctx.readOnly} />
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

function SearchScreen({
  draft, onDraft, onAnalyse, horizon, onHorizon, session, readOnly,
}: {
  draft: string; onDraft: (v: string) => void; onAnalyse: (s: string) => void;
  horizon: Horizon; onHorizon: (h: Horizon) => void; session?: string;
  readOnly?: boolean;
}) {
  return (
    <div className="an-hero">
      <h1>Global Market Intelligence<br />For Smarter Decisions</h1>
      <p className="an-tag">Real data. Deep analysis.</p>

      {/* Chosen before the run, because it changes what the run reads. */}
      <div className="an-horizon">
        <HorizonSwitch value={horizon} onChange={onHorizon} session={session} />
        <p>{HORIZON_COPY[horizon].basis}</p>
      </div>

      <form
        className="an-search"
        onSubmit={(e) => { e.preventDefault(); onAnalyse(draft); }}
      >
        <Search size={15} color={DIM} />
        <input
          value={draft}
          onChange={(e) => onDraft(e.target.value.toUpperCase())}
          placeholder="Search ticker (e.g. NVDA)"
          aria-label="Ticker"
          spellCheck={false}
          autoFocus
        />
        {draft && (
          <button type="button" className="icon-btn" onClick={() => onDraft('')}
            aria-label="Clear">
            <X size={14} />
          </button>
        )}
        <button type="submit" className="an-go" disabled={!draft.trim() || readOnly}
          title={readOnly ? 'View-only account' : undefined}>
          Analyze
        </button>
      </form>

      {readOnly ? (
        <div className="an-try" style={{ opacity: 0.7 }}>
          Running analysis is disabled for view-only accounts.
        </div>
      ) : (
        <div className="an-try">
          Try:
          {SUGGESTED.map((s) => (
            <button key={s} onClick={() => { onDraft(s); onAnalyse(s); }}>{s}</button>
          ))}
        </div>
      )}

      <div className="an-stats">
        <div><b>12</b><span>Data Sources</span></div>
        <div><b>20</b><span>Scored Parameters</span></div>
        <div><b>US</b><span>Market Coverage</span></div>
        <div><b>Live</b><span>Prices &amp; Flow</span></div>
      </div>
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
const SHORT: Record<string, string> = {
  sec_filings: 'SEC',
  insider: 'Insiders',
  institutional: '13F',
  options_flow: 'Flow',
  gex: 'GEX',
  price_action: 'Price',
  relative_strength: 'RS',
  macro: 'Macro',
  iv_greeks: 'IV',
  volume: 'Volume',
  news: 'News',
  earnings: 'Earnings',
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
    <div className="an-scan">
      <button className="an-back" onClick={onBack}>
        <X size={14} /> Cancel
      </button>

      <div className="an-scan-head">
        <h2>{connecting ? 'Searching' : 'Analyzing'} <span>{symbol}</span>…</h2>
        <p>{connecting
          ? 'Connecting to data sources'
          : 'Streaming live and historical data from every configured source'}</p>
      </div>

      {error && <div className="dir-block"><div className="db-head">{error}</div></div>}

      {/* Sources ring the globe with a line to the core. The line streams
          while its provider is still outstanding and settles when it answers,
          so the picture is the run rather than an animation playing over it. */}
      <div className="an-orbit">
        <svg className="an-orbit-lines" viewBox="0 0 100 100"
             preserveAspectRatio="none" aria-hidden="true">
          {shown.map((s, i) => {
            const p = nodeAt(i, count, 38);
            const st = state[s.key]?.status || 'pending';
            const path = `M ${p.x} ${p.y} L 50 50`;
            const hue = hueOf(s.key, i, count);
            const lit = `hsl(${hue} 85% 62%)`;
            return (
              <g key={s.key}>
                <line x1={p.x} y1={p.y} x2="50" y2="50"
                      className={`an-wire ${st} ${running ? 'live' : ''}`}
                      style={st === 'pending' && !running ? undefined
                        : { stroke: `hsl(${hue} 80% 58% / ${st === 'scanning' ? 0.8 : 0.38})` }} />
                {/* Pips travelling down the wire into the core. Three per
                    wire, staggered, so the flow reads as continuous without
                    turning the middle of the screen into a swarm. */}
                {st === 'scanning' && [0, 0.45, 0.9].map((delay) => (
                  <circle key={delay} r="1.2" className="an-pip"
                    style={{ fill: lit, color: lit }}>
                    <animateMotion
                      path={path}
                      dur="1.35s"
                      begin={`-${delay}s`}
                      repeatCount="indefinite"
                    />
                  </circle>
                ))}
                {/* A queued source is still part of a live run -- the agent
                    has reached out and is waiting on it -- so its wire keeps
                    a single slow pip. It stops the moment the run does, which
                    is what keeps the picture honest: motion means working. */}
                {st === 'pending' && running && (
                  <circle r="0.95" className="an-pip queued"
                    style={{ fill: `hsl(${hue} 72% 64%)`,
                             color: `hsl(${hue} 72% 64%)` }}>
                    <animateMotion path={path} dur="2.6s"
                      begin={`-${(i % 6) * 0.4}s`} repeatCount="indefinite" />
                  </circle>
                )}
                {/* One slow pip on a settled wire, so a completed source
                    still looks connected rather than switched off. */}
                {st === 'complete' && (
                  <circle r="0.85" className="an-pip done"
                    style={{ fill: lit, color: lit }}>
                    <animateMotion path={path} dur="3.4s" repeatCount="indefinite" />
                  </circle>
                )}
              </g>
            );
          })}
        </svg>

        <div className="an-orbit-core">
          <WorldGlobe size={168} spinning={running} />
        </div>

        {shown.map((s, i) => {
          const p = nodeAt(i, count, 38);
          const st = state[s.key]?.status || 'pending';
          const at = state[s.key]?.elapsed;
          const hue = hueOf(s.key, i, count);
          return (
            <div
              className={`an-node ${st}`}
              key={s.key}
              style={{
                left: `${p.x}%`,
                top: `${p.y}%`,
                // The node's own accent, so the ball arriving at the globe can
                // be traced back to the card that sent it.
                ['--src' as any]: `hsl(${hue} 82% 58%)`,
                ['--src-soft' as any]: `hsl(${hue} 82% 58% / .14)`,
              }}
              title={`${s.label} — ${s.sub}`}
            >
              <span className="as-icon">{ICONS[s.key] || <Database size={16} />}</span>
              <span className="as-body">
                <b>{SHORT[s.key] || s.label}</b>
              </span>
              <span className="an-node-state">
                {st === 'complete' ? <><CheckCircle2 size={10} color={GREEN} />
                  {at ? `${at.toFixed(1)}s` : ''}</>
                  : st === 'unavailable' ? <XCircle size={10} color={AMBER} />
                    : st === 'scanning' ? <Loader2 size={10} className="spin" />
                      : <span className="an-dot" />}
              </span>
            </div>
          );
        })}
      </div>

      <div className="an-progress">
        <div className="ap-track"><i style={{ width: `${percent}%` }} /></div>
        <div className="an-progress-foot">
          <span>Analyzing {symbol} — gathering and processing data</span>
          <b>{percent}%</b>
        </div>
        <div className="an-progress-sub">
          {done}/{total} sources · {elapsed.toFixed(1)}s elapsed
          {log.length ? ` · latest: ${log[log.length - 1].text}` : ''}
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
    </span>
  );
}

/* ------------------------------------------------------------ 6. result */

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
function StoredWhy({ symbol, horizon }: { symbol: string; horizon: Horizon }) {
  const [found, setFound] = useState(false);
  const opened = useRef(Date.now());
  const call = useApi<any>(
    (sig) => api2.callLatest(symbol, horizon, sig),
    [symbol, horizon],
    { refreshMs: found ? undefined : 2500 },
  );
  const row = call.data?.call;
  const fresh = !!row?.made_at
    && new Date(row.made_at).getTime() >= opened.current - 120000;

  useEffect(() => {
    if (fresh) setFound(true);
  }, [fresh]);
  // Give up asking after twenty seconds; a call that has not been stored by
  // then is not coming, and polling for ever would be the bug.
  useEffect(() => {
    const t = setTimeout(() => setFound(true), 20000);
    return () => clearTimeout(t);
  }, []);

  if (!fresh) {
    return (
      <div className="an-why-wait">
        {found ? 'This call could not be saved.' : 'Saving this call…'}
      </div>
    );
  }
  return <div className="an-why"><WhyCall call={row} /></div>;
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
          {sources} sources answered · {signals.length} parameters ·
          {' '}{elapsed.toFixed(1)}s
        </span>
      </div>

      <StoredWhy symbol={symbol} horizon={horizon} />

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
      </div>

      <div className="an-tabs">
        {TABS.map(([k, label]) => (
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
          </div>
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
