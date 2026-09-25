import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
  ZAxis, ReferenceLine,
} from 'recharts';
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2,
  Filter, Info, Lightbulb, Shield, Sparkles, Target, TrendingDown, TrendingUp,
} from 'lucide-react';
import Freshness from '../components/Freshness';
import { NavLink } from 'react-router-dom';
import { api, api2 } from '../api/client';
import type { PageContext } from '../App';
import {
  DEMO_EARNINGS, DEMO_FLOW_INSIGHTS, DEMO_OPTIONS, DEMO_TRADE_IDEAS,
} from '../demo';

const DEMO_EARNINGS_DATE = DEMO_EARNINGS.earnings;
import type { FlowTrade, OptionsOverview } from '../api/client';
import { useApi } from '../hooks/useApi';
import { DarkPoolTab, MarketInsidersTab } from '../components/DarkPool';
import OptionChainPage from './OptionChainPage';
import {
  EMPTY_FILTERS, ExpiryFlow, FlowFilters, FlowSummary, FlowTape, SectorFlow,
  TickerDetail, UnusualTable,
} from '../components/MarketFlow';
import type { FlowFilterState } from '../components/MarketFlow';
import { Donut, MiniRing, Panel, SentimentGauge, StateBlock } from '../components/common';
import { StatusChip } from './shared';
import {
  compact, compactMoney, DASH, int, millions, money, num, pct, plusMinus,
  signedPct, tone,
} from '../lib/format';

/**
 * Options workspace tabs. Views filter this page; routed tabs open the screen
 * that actually owns that data.
 */
const TABS: { key: string; label: string; route?: string }[] = [
  { key: 'overview', label: 'Overview', route: '/earnings' },
  { key: 'chart', label: 'Chart', route: '/earnings' },
  { key: 'flow', label: 'Options Flow' },
  { key: 'unusual', label: 'Unusual Activity' },
  { key: 'iv', label: 'IV Analysis' },
  { key: 'news', label: 'News', route: '/news' },
  { key: 'earnings', label: 'Earnings', route: '/earnings' },
  { key: 'ai', label: 'AI Insights', route: '/ai-insights' },
];

/** Panels shown by each in-page view. */
const TAB_PANELS: Record<string, string[]> = {
  flow: ['tiles', 'scatter', 'strikes', 'metrics', 'table', 'breakdown',
    'expiry', 'insights', 'ideas', 'zones'],
  unusual: ['tiles', 'table', 'breakdown', 'insights'],
  iv: ['tiles', 'metrics', 'strikes', 'zones', 'insights'],
};

const GREEN = '#21d07a';
const RED = '#f2465a';
const AMBER = '#f0a92b';

type FlowFilter = 'All' | 'Sweeps' | 'Blocks' | 'Buys' | 'Sells';
type StrikeMode = 'volume' | 'open_interest' | 'delta_exposure';

export default function OptionsFlowPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const [tab, setTab] = useState('flow');
  // The page opens on the market-wide tape: "where is the money going" is the
  // question this screen exists to answer, and a single symbol is the
  // follow-up. The route always carries a symbol -- /options-flow redirects to
  // a default -- so the URL cannot distinguish an intentional ticker from that
  // default, and defaulting to the tape is the honest reading of a bare click.
  const [mode, setMode] =
    useState<'market' | 'symbol' | 'chain' | 'dark' | 'insiders'>('market');
  const [picked, setPicked] = useState<string | null>(null);
  // The page opens on the market tape, but the moment the operator searches a
  // ticker they mean "show me THIS name", not the whole market. Switch to the
  // symbol view on any change of symbol after the first render, so a search
  // lands on that stock's flow instead of leaving the market tape up.
  const prevSymbol = useRef(symbol);
  useEffect(() => {
    if (symbol !== prevSymbol.current) {
      prevSymbol.current = symbol;
      setMode((m) => (m === 'market' ? 'symbol' : m));
    }
  }, [symbol]);
  // Filters narrow the tape the operator is already looking at rather than
  // refetching: the session is one query and already in hand, so a round trip
  // to drop rows would cost seconds to show less.
  const [filters, setFilters] = useState<FlowFilterState>(EMPTY_FILTERS);
  const [filtersOpen, setFiltersOpen] = useState(true);

  const marketSummary = useApi<any>(
    (s) => (mode === 'market' && !demo
      ? api2.flowMarketSummary(s) : Promise.resolve(null)), [mode, demo]);
  const marketTape = useApi<any>(
    (s) => (mode === 'market' && !demo
      ? api2.flowMarketTape(50, s) : Promise.resolve(null)), [mode, demo]);
  const marketUnusual = useApi<any>(
    (s) => (mode === 'market' && !demo
      ? api2.flowMarketUnusual(15, s) : Promise.resolve(null)), [mode, demo]);
  const marketSectors = useApi<any>(
    (s) => (mode === 'market' && !demo
      ? api2.flowMarketSectors(s) : Promise.resolve(null)), [mode, demo]);
  const marketComparison = useApi<any>(
    (s) => (mode === 'market' && !demo
      ? api2.flowMarketComparison(s) : Promise.resolve(null)), [mode, demo]);
  const marketExpiries = useApi<any>(
    (s) => (mode === 'market' && !demo
      ? api2.flowMarketExpiries(s) : Promise.resolve(null)), [mode, demo]);
  const marketIntraday = useApi<any>(
    (s) => (mode === 'market' && !demo
      ? api2.flowMarketIntraday(s) : Promise.resolve(null)), [mode, demo]);
  const shows = (panel: string) =>
    (TAB_PANELS[tab] || TAB_PANELS.flow).includes(panel);

  const overview = useApi<OptionsOverview>(
    (signal) => (demo
      ? Promise.resolve(DEMO_OPTIONS)
      : api.optionsOverview(symbol, signal)),
    [symbol, demo],
  );

  // The header's earnings cell needs the real event, not the options chain.
  const earnings = useApi<any>(
    (signal) => (demo
      ? Promise.resolve({ event: DEMO_EARNINGS_DATE })
      : api2.earningsLifecycle(symbol, signal)),
    [symbol, demo],
  );

  const d = overview.data;

  return (
    <>
      <div className="opt-tabbar">
        {/* One row for the whole section: the market tape, this symbol's
            flow, and its chain. They share a symbol and a provider call, so
            moving between them should never mean leaving the page. */}
        <div className="mf-mode" role="tablist">
          <button role="tab" aria-selected={mode === 'market'}
            className={`mf-mode-btn ${mode === 'market' ? 'active' : ''}`}
            onClick={() => setMode('market')}>Market Flow</button>
          <button role="tab" aria-selected={mode === 'symbol'}
            className={`mf-mode-btn ${mode === 'symbol' ? 'active' : ''}`}
            onClick={() => setMode('symbol')}>{symbol} Flow</button>
          <button role="tab" aria-selected={mode === 'chain'}
            className={`mf-mode-btn ${mode === 'chain' ? 'active' : ''}`}
            onClick={() => setMode('chain')}>Option Chain</button>
          {/* Neither of these is an options reading, which is why each gets
              its own tab rather than another panel on the tape. */}
          <button role="tab" aria-selected={mode === 'dark'}
            className={`mf-mode-btn ${mode === 'dark' ? 'active' : ''}`}
            onClick={() => setMode('dark')}>Dark Pool</button>
          <button role="tab" aria-selected={mode === 'insiders'}
            className={`mf-mode-btn ${mode === 'insiders' ? 'active' : ''}`}
            onClick={() => setMode('insiders')}>Market Insiders</button>
        </div>
        <div className="opt-tabs" role="tablist"
          style={mode === 'symbol' ? undefined : { display: 'none' }}>
          {TABS.map((t) => (t.route ? (
            <NavLink
              key={t.key}
              to={`${t.route}/${symbol}${ctx.search}`}
              className="opt-tab"
              role="tab"
            >
              {t.label}
            </NavLink>
          ) : (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={`opt-tab ${tab === t.key ? 'active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          )))}
        </div>
        {mode !== 'market' && (
          <HeadInfo data={d}
            earnings={demo ? DEMO_EARNINGS_DATE : earnings.data?.event} />
        )}
      </div>

      {mode === 'chain' ? (
        <div className="page">
          <div className="mf-head">
            <div>
              <h1>Option Chain</h1>
              <p>
                {symbol} — the full calls | strike | puts ladder for one
                expiry, with the Greeks the provider publishes.
              </p>
            </div>
          </div>
          <OptionChainPage ctx={ctx} embedded />
        </div>
      ) : mode === 'dark' ? (
        <DarkPoolTab symbol={symbol} />
      ) : mode === 'insiders' ? (
        <MarketInsidersTab />
      ) : mode === 'market' ? (
        <div className="page">
          <div className="mf-head">
            <div>
              <h1>Options Flow</h1>
              <p>
                Options activity, unusual flow and institutional moves across
                the watched tape. OPRA prints arrive on the data plan's
                delay — the badge says how far behind.
              </p>
            </div>
            <div className="mf-head-right">
              {/* Read from the payload, never asserted here. This said
                  "Live data" over a fifteen-minute-delayed tape, which is the
                  one thing a flow screen must not get wrong. */}
              {marketTape.data?.freshness
                ? <Freshness stamp={marketTape.data.freshness} />
                : (
                  <span className={`mf-live ${marketTape.data?.status === 'OK' ? 'on' : ''}`}>
                    <i />{marketTape.data?.status === 'OK'
                      ? 'Tape connected' : 'Waiting for data'}
                  </span>
                )}
              {marketSummary.data?.session && (
                <span className="mf-head-stamp">
                  Session {marketSummary.data.session}
                </span>
              )}
            </div>
          </div>

          {filtersOpen && (
            <FlowFilters filters={filters} onChange={setFilters} />
          )}

          <FlowSummary
            summary={marketSummary.data}
            comparison={marketComparison.data}
            intraday={marketIntraday.data}
            unusualCount={marketUnusual.data?.count ?? null}
            loading={marketSummary.loading || marketTape.loading}
            onRefresh={() => {
              marketSummary.refresh(); marketTape.refresh();
              marketUnusual.refresh(); marketSectors.refresh();
              marketComparison.refresh(); marketExpiries.refresh();
              marketIntraday.refresh();
            }}
          />

          {marketTape.initialLoading ? (
            <Panel title="Live Options Flow">
              <StateBlock loading />
              <div className="hint" style={{ textAlign: 'center' }}>
                Aggregating every watched ticker for the session. The first
                pass takes a moment; later reads are cached.
              </div>
            </Panel>
          ) : (
            <div className="mf-split">
              <FlowTape tape={marketTape.data} picked={picked}
                onPick={setPicked} filters={filters} filtersOpen={filtersOpen}
                onToggleFilters={() => setFiltersOpen((v) => !v)} />
              <TickerDetail symbol={picked} tape={marketTape.data}
                summary={marketSummary.data}
                onClose={() => setPicked(null)}
                onOpen={(sym) => { ctx.onSymbol(sym); setMode('symbol'); }} />
            </div>
          )}

          <div className="mf-bottom">
            <ExpiryFlow expiries={marketExpiries.data} />
            <UnusualTable unusual={marketUnusual.data} onPick={setPicked} />
            <SectorFlow sectors={marketSectors.data} />
          </div>
        </div>
      ) : (
      <div className="page">
        {overview.error ? (
          <Panel title="Options Flow"><StateBlock error={overview.error} /></Panel>
        ) : overview.initialLoading || !d ? (
          <Panel title={`${symbol} · Options Flow`}>
            <StateBlock loading />
            <div className="hint" style={{ textAlign: 'center' }}>
              Building the chain, tape and analytics. The first
              load takes a moment; later refreshes are cached.
            </div>
          </Panel>
        ) : d.status !== 'OK' ? (
          <Panel title="Options Flow"><StateBlock status={d.status} error={d.error} /></Panel>
        ) : (
          <>
            {shows('tiles') && <Tiles data={d} />}

            {(shows('scatter') || shows('strikes') || shows('metrics')) && (
              <div className="opt-grid-1">
                {shows('scatter') && <FlowScatterPanel data={d} />}
                {shows('strikes') && <StrikePanel data={d} />}
                {shows('metrics') && <KeyMetricsPanel data={d} />}
              </div>
            )}

            {(shows('table') || shows('breakdown') || shows('expiry')) && (
              <div className="opt-grid-2">
                {shows('table') && <FlowTablePanel data={d} />}
                {shows('breakdown') && <BreakdownPanel data={d} />}
                {shows('expiry') && <ExpirationPanel data={d} />}
              </div>
            )}

            {(shows('insights') || shows('ideas') || shows('zones')) && (
              <div className="opt-grid-3">
                {shows('insights') && <InsightsPanel data={d} demo={demo} />}
                {shows('ideas') && <TradeIdeasPanel data={d} demo={demo} />}
                {shows('zones') && <RiskZonesPanel data={d} />}
              </div>
            )}

            {shows('zones') && (
              <div className="opt-grid-2">
                <UnusualActivityPanel data={d} />
                <PositioningPanel data={d} />
              </div>
            )}

            {shows('zones') && !demo && <AdvancedDataPanel data={d} />}
          </>
        )}
      </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------- head info */

function HeadInfo({ data, earnings }: { data: OptionsOverview | null; earnings?: any }) {
  const m = data?.metrics || {};
  const spot = data?.spot;
  const lower = m.expected_range?.lower;
  const upper = m.expected_range?.upper;

  const markPct =
    typeof spot === 'number' && typeof lower === 'number' && typeof upper === 'number' && upper > lower
      ? ((spot - lower) / (upper - lower)) * 100
      : 50;

  const ivp = m.iv_percentile;
  const ivLabel = ivp == null ? DASH : ivp >= 70 ? 'High' : ivp <= 30 ? 'Low' : 'Average';
  const ivColor = ivp == null ? 'var(--text-dim)' : ivp >= 70 ? 'var(--red)' : ivp <= 30 ? 'var(--green)' : 'var(--amber)';

  return (
    <div className="opt-headinfo">
      <div className="hi-cell">
        <div className="hi-label">Earnings</div>
        <div className="hi-value" style={{ fontSize: 12 }}>
          {earnings?.date_label || DASH}
        </div>
        <div className="hi-sub">
          {earnings?.timing_label || earnings?.short_label || ''}
        </div>
      </div>
      <div className="hi-cell" style={{ minWidth: 168 }}>
        <div className="hi-label">Expected Move</div>
        <div className="hi-value">{plusMinus(m.expected_move_percent)}</div>
        <div className="hi-bar">
          <div className="em-mark" style={{ left: `${Math.max(0, Math.min(100, markPct))}%` }} />
        </div>
        <div className="hi-scale">
          <span>{money(lower)}</span>
          <span>{money(spot)}</span>
          <span>{money(upper)}</span>
        </div>
      </div>
      <div className="hi-cell">
        <div className="hi-label">IV Percentile</div>
        <div className="hi-value">{pct(ivp, 0)}</div>
        <div className="hi-sub" style={{ color: ivColor, fontWeight: 600 }}>{ivLabel}</div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- tiles */

function Tiles({ data }: { data: OptionsOverview }) {
  const t = data.tiles;
  const s = data.sentiment;
  const tapeBasis = t.volume_basis === 'LAST_SESSION_TAPE';

  const basisNote = tapeBasis
    ? 'Last completed session (from the trade tape) - no volume is reported outside market hours'
    : 'Current session volume';

  const sentColor =
    s.label === 'BULLISH' ? GREEN : s.label === 'BEARISH' ? RED : 'var(--amber)';

  return (
    <div className="tiles">
      <Tile
        label="Call Volume"
        value={millions(t.call_volume)}
        delta={t.call_volume_change}
        ring={t.call_share}
        ringColor={GREEN}
        title={basisNote}
      />
      <Tile
        label="Put Volume"
        value={millions(t.put_volume)}
        delta={t.put_volume_change}
        ring={t.put_share}
        ringColor={RED}
        title={basisNote}
      />
      <Tile
        label="Put/Call Ratio"
        value={num(t.put_call_volume)}
        sub={t.put_call_bias}
        subColor={t.put_call_bias === 'Bullish' ? GREEN : t.put_call_bias === 'Bearish' ? RED : 'var(--amber)'}
        title="Put volume divided by call volume"
      />
      <Tile
        label="Total Volume"
        value={millions(t.total_volume)}
        delta={t.total_volume_change}
        title={basisNote}
      />
      <Tile
        label="Total Open Interest"
        value={compact(t.total_oi, 1)}
        sub={`${compact(t.call_oi, 1)} C / ${compact(t.put_oi, 1)} P`}
        subColor="var(--text-dim)"
        title="Open interest across the quoted strikes"
      />
      <Tile
        label="Notional Flow"
        value={compactMoney(t.notional)}
        sub={t.put_call_bias}
        subColor={t.put_call_bias === 'Bullish' ? GREEN : t.put_call_bias === 'Bearish' ? RED : 'var(--amber)'}
        title="Contracts x price x 100"
      />

      <div className="sentiment-tile">
        <div className="st-title">Options Flow Sentiment</div>
        <div className="st-body">
          <SentimentGauge score={s.score} size={126} />
          <div className="sent-right">
            <div className="sent-label" style={{ color: sentColor }}>{s.label}</div>
            <div className="sent-score">
              <b>{s.score ?? DASH}</b>/100
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Tile({
  label, value, delta, sub, subColor, ring, ringColor, title,
}: {
  label: string;
  value: string;
  delta?: number | null;
  sub?: string | null;
  subColor?: string;
  ring?: number | null;
  ringColor?: string;
  title?: string;
}) {
  const t = tone(delta);
  return (
    <div className="tile" title={title}>
      <div className="tile-main">
        <div className="tile-label">{label}</div>
        <div className="tile-value">{value}</div>
        {delta != null && (
          <div className={`tile-delta ${t}`}>
            {t === 'neg' ? <ArrowDownRight size={11} /> : <ArrowUpRight size={11} />}
            {signedPct(delta, 0)}
          </div>
        )}
        {sub && <div className="tile-sub" style={{ color: subColor }}>{sub}</div>}
      </div>
      {ring != null && ringColor && <MiniRing percent={ring} color={ringColor} />}
    </div>
  );
}

/* --------------------------------------------------------------- scatter */

function FlowTip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p: FlowTrade = payload[0].payload;
  const bull = p.sentiment === 'Bullish';

  return (
    <div className="flow-tip">
      <div className="ft-head">
        {p.symbol} {p.expiry_label} {p.strike}{p.right}
      </div>
      <div className="ft-row"><span>Time</span><b>{p.time}</b></div>
      <div className="ft-row"><span>Size</span><b>{compact(p.contracts, 0)} contracts</b></div>
      <div className="ft-row"><span>Price</span><b>{money(p.price)}</b></div>
      <div className="ft-row"><span>Notional</span><b>{compactMoney(p.notional)}</b></div>
      <div className="ft-row"><span>Side</span><b>{p.side}</b></div>
      {p.exchanges?.length > 1 && (
        <div className="ft-row"><span>Venues</span><b>{p.exchanges.length}</b></div>
      )}
      <div className="ft-tag" style={{ color: bull ? GREEN : RED }}>
        {bull ? 'BULLISH' : 'BEARISH'} {p.kind}
      </div>
    </div>
  );
}

function FlowScatterPanel({ data }: { data: OptionsOverview }) {
  const sc = data.scatter;
  const points = sc.points || [];

  const [side, setSide] = useState<'all' | 'C' | 'P'>('all');

  const filtered = useMemo(
    () => (side === 'all' ? points : points.filter((p) => p.right === side)),
    [points, side],
  );

  const calls = filtered.filter((p) => p.right === 'C');
  const puts = filtered.filter((p) => p.right === 'P');

  const domain = useMemo<[number, number]>(() => {
    if (!points.length) return [0, 1];
    const epochs = points.map((p) => p.epoch);
    return [Math.min(...epochs), Math.max(...epochs)];
  }, [points]);

  // Whole hours on the time axis and a round 5-point strike ladder, matching
  // the reference rather than following the raw data extremes.
  const timeTicks = useMemo(() => {
    if (!points.length) return [];
    const [lo, hi] = domain;
    const first = Math.ceil(lo / 3600) * 3600;
    const out: number[] = [];
    for (let v = first; v <= hi; v += 3600) out.push(v);
    return out;
  }, [points, domain]);

  const strikeTicks = useMemo(() => {
    if (!points.length) return [];
    const strikes = points.map((p) => p.strike);
    const lo = Math.floor(Math.min(...strikes) / 5) * 5;
    const hi = Math.ceil(Math.max(...strikes) / 5) * 5;
    const out: number[] = [];
    for (let v = lo; v <= hi; v += 5) out.push(v);
    return out;
  }, [points]);

  // The tape is timestamped in US market time; formatting with the viewer's
  // locale would relabel a 15:59 print as whatever it was locally.
  const fmtTime = (v: number) =>
    new Date(v * 1000).toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', hour12: false,
      timeZone: 'America/New_York',
    });

  return (
    <Panel
      title="Options Flow (Real-Time)"
      icon={<Info size={12} />}
      noBody
      right={
        <>
          <div className="legend-row">
            <span className="legend-item"><i className="legend-dot" style={{ background: GREEN }} />Calls</span>
            <span className="legend-item"><i className="legend-dot" style={{ background: RED }} />Puts</span>
            <span className="legend-item"><i className="legend-ring" />Large Trade</span>
          </div>
          <div className="seg">
            {(['all', 'C', 'P'] as const).map((s) => (
              <button key={s} className={side === s ? 'active' : ''} onClick={() => setSide(s)}>
                {s === 'all' ? 'All' : s === 'C' ? 'Calls' : 'Puts'}
              </button>
            ))}
          </div>
        </>
      }
    >
      {points.length ? (
        <>
          <div style={{ padding: '10px 6px 0' }}>
            <ResponsiveContainer width="100%" height={238}>
              <ScatterChart margin={{ top: 8, right: 54, bottom: 22, left: 12 }}>
                <CartesianGrid stroke="#182236" strokeDasharray="2 4" />
                <XAxis
                  type="number"
                  dataKey="epoch"
                  domain={domain}
                  ticks={timeTicks.length ? timeTicks : undefined}
                  tickFormatter={fmtTime}
                  tick={{ fill: '#5b6580', fontSize: 9.5 }}
                  axisLine={{ stroke: '#182236' }}
                  tickLine={false}
                  label={{ value: 'Time (ET)', position: 'insideBottom', offset: -8, fill: '#5b6580', fontSize: 10 }}
                />
                <YAxis
                  type="number"
                  dataKey="strike"
                  domain={[
                    strikeTicks.length ? strikeTicks[0] : 'dataMin - 3',
                    strikeTicks.length ? strikeTicks[strikeTicks.length - 1] : 'dataMax + 3',
                  ]}
                  ticks={strikeTicks.length ? strikeTicks : undefined}
                  tick={{ fill: '#5b6580', fontSize: 9.5, fontFamily: 'monospace' }}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                  label={{
                    value: 'Strike Price', angle: -90,
                    position: 'insideLeft', offset: 14,
                    fill: '#5b6580', fontSize: 10,
                  }}
                />
                <ZAxis type="number" dataKey="notional" range={[24, 420]} />
                <Tooltip content={<FlowTip />} cursor={{ strokeDasharray: '3 3', stroke: '#2b3958' }} />
                {data.spot != null && (
                  <ReferenceLine
                    y={data.spot}
                    stroke={GREEN}
                    strokeDasharray="5 4"
                    label={{
                      value: num(data.spot),
                      position: 'right',
                      fill: GREEN,
                      fontSize: 10,
                      fontWeight: 700,
                    }}
                  />
                )}
                <Scatter data={calls} fill={GREEN} fillOpacity={0.62} isAnimationActive={false}>
                  {calls.map((p, i) => (
                    <Cell key={i} stroke={p.is_large ? GREEN : 'none'} strokeWidth={p.is_large ? 1.6 : 0} />
                  ))}
                </Scatter>
                <Scatter data={puts} fill={RED} fillOpacity={0.62} isAnimationActive={false}>
                  {puts.map((p, i) => (
                    <Cell key={i} stroke={p.is_large ? RED : 'none'} strokeWidth={p.is_large ? 1.6 : 0} />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <div className="hint">
            {filtered.length} clustered prints · bubble size = notional · sourced from
            Provider tape{data.flow.session_date ? ` for ${data.flow.session_date}` : ''}.
          </div>
        </>
      ) : (
        <StateBlock status={sc.status} />
      )}
    </Panel>
  );
}

/* --------------------------------------------------- volume by strike */

function StrikePanel({ data }: { data: OptionsOverview }) {
  const [mode, setMode] = useState<StrikeMode>(
    data.tiles.volume_basis === 'NONE' ? 'open_interest' : 'volume',
  );

  const set =
    mode === 'open_interest' ? data.open_interest_by_strike
      : mode === 'delta_exposure' ? data.delta_exposure_by_strike
        : data.volume_by_strike;

  // The reference lists strikes high-to-low, the way an option chain ladder
  // is quoted.
  const rows = (set?.strikes || [])
    .filter((s) => s.calls > 0 || s.puts > 0)
    .slice()
    .reverse();
  const rawPeak = set?.peak || 1;
  const spot = data.spot;

  const atmStrike = useMemo(() => {
    if (!rows.length || spot == null) return null;
    return rows.reduce((best, r) =>
      Math.abs(r.strike - spot) < Math.abs(best.strike - spot) ? r : best,
    ).strike;
  }, [rows, spot]);

  const fmt = mode === 'delta_exposure' ? (v: number) => compactMoney(v, 0) : (v: number) => compact(v, 0);

  // Four evenly spaced gridline values either side of the centre column.
  const peak = useMemo(() => {
    if (!rawPeak) return 1;
    const rough = rawPeak / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= rough) || mag * 10;
    return step * 4;
  }, [rawPeak]);

  const axisTicks = useMemo(() => {
    if (!peak) return [];
    // Round the top of the scale up to a clean step so the axis reads
    // 400K / 300K / 200K / 100K rather than 390K / 293K / 195K.
    const rough = peak / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= rough) || mag * 10;
    return [4, 3, 2, 1].map((n) => step * n);
  }, [peak]);

  return (
    <Panel
      title="Options Volume by Strike"
      noBody
      right={
        <div className="seg">
          <button className={mode === 'volume' ? 'active' : ''} onClick={() => setMode('volume')}>Volume</button>
          <button className={mode === 'open_interest' ? 'active' : ''} onClick={() => setMode('open_interest')}>Open Interest</button>
          <button className={mode === 'delta_exposure' ? 'active' : ''} onClick={() => setMode('delta_exposure')}>Delta Exp.</button>
        </div>
      }
    >
      {rows.length ? (
        <>
          <div className="vbs-head">
            <span className="c">Calls</span>
            <span className="k">Strike</span>
            <span className="p">Puts</span>
          </div>
          <div className="vbs-rows">
            {rows.map((r) => (
              <div className={`vbs-row ${r.strike === atmStrike ? 'atm' : ''}`} key={r.strike}>
                <div className="vbs-bar left" title={`${fmt(r.calls)} calls @ ${r.strike}`}>
                  <i style={{ width: `${(r.calls / peak) * 100}%` }} />
                </div>
                <div className="vbs-strike">{r.strike}</div>
                <div className="vbs-bar right" title={`${fmt(r.puts)} puts @ ${r.strike}`}>
                  <i style={{ width: `${(r.puts / peak) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
          <div className="vbs-axis">
            <span className="vbs-side">
              {axisTicks.map((v) => <span key={`c${v}`}>{fmt(v)}</span>)}
            </span>
            <span className="vbs-zero">0</span>
            <span className="vbs-side right">
              {axisTicks.slice().reverse().map((v) => <span key={`p${v}`}>{fmt(v)}</span>)}
            </span>
          </div>
          <div className="hint">
            {data.expiry_label} expiry
            {mode === 'volume' && set?.basis === 'LAST_SESSION_TAPE' && ' · last completed session'}
          </div>
        </>
      ) : (
        <StateBlock status={set?.status} />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------- key metrics */

function KeyMetricsPanel({ data }: { data: OptionsOverview }) {
  const m = data.metrics || {};
  if (m.status !== 'OK') {
    return <Panel title="Key Options Metrics" noBody><StateBlock status={m.status} /></Panel>;
  }

  const rows: { k: string; v: string; cls?: string; title?: string }[] = [
    { k: 'Implied Volatility (IV)', v: pct(m.implied_volatility, 1), title: 'Solved from real ATM bid/ask' },
    { k: 'IV Percentile (1Y)', v: pct(m.iv_percentile, 0), title: 'Share of the last year below today' },
    { k: 'IV Rank (1Y)', v: pct(m.iv_rank, 0), title: 'Position within the 1-year IV range' },
    { k: 'ATM Call Price', v: money(m.atm_call_price), title: `Strike ${m.atm_call_strike}` },
    { k: 'ATM Put Price', v: money(m.atm_put_price), title: `Strike ${m.atm_put_strike}` },
    { k: 'ATM Straddle', v: money(m.atm_straddle) },
    { k: 'Expected Move', v: plusMinus(m.expected_move_percent) },
    {
      k: `Realized Move (Last ${m.realized_windows ?? 4})`,
      v: plusMinus(m.realized_move_percent),
      title: 'Average absolute move over the same horizon',
    },
    {
      k: 'Skew (25D)',
      v: pct(m.skew_25d, 1),
      cls: tone(m.skew_25d),
      title: m.skew_source
        ? `25-delta call IV minus 25-delta put IV (${m.skew_source})`
        : '25-delta call IV minus 25-delta put IV',
    },
    {
      k: 'Historical Volatility',
      v: pct(m.historical_volatility, 1),
      title: 'Realized volatility over the trailing year, for comparison with IV',
    },
    { k: 'Put/Call Ratio (Vol)', v: num(m.put_call_volume), cls: m.put_call_volume == null ? '' : m.put_call_volume < 1 ? 'pos' : 'neg' },
    { k: 'Put/Call Ratio (OI)', v: num(m.put_call_oi), cls: m.put_call_oi == null ? '' : m.put_call_oi < 1 ? 'pos' : 'neg' },
    { k: 'Call OI vs Put OI', v: `${num(m.call_oi_vs_put_oi, 1)}x`, cls: 'pos' },
  ];

  return (
    <Panel title="Key Options Metrics" noBody>
      <div className="kv">
        {rows.map((r) => (
          <div className="kv-row" key={r.k} title={r.title}>
            <span className="k">{r.k}</span>
            <span className={`v ${r.cls || ''}`}>{r.v}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* --------------------------------------------------------- flow table */

function FlowTablePanel({ data }: { data: OptionsOverview }) {
  const [filter, setFilter] = useState<FlowFilter>('All');
  const [showFilters, setShowFilters] = useState(false);
  const [minNotional, setMinNotional] = useState(0);

  const trades = data.flow.trades || [];

  const rows = useMemo(() => {
    let out = trades;
    if (filter === 'Sweeps') out = out.filter((t) => t.kind === 'SWEEP');
    if (filter === 'Blocks') out = out.filter((t) => t.kind === 'BLOCK');
    if (filter === 'Buys') out = out.filter((t) => t.side.toUpperCase() === 'BUY');
    if (filter === 'Sells') out = out.filter((t) => t.side.toUpperCase() === 'SELL');
    if (minNotional > 0) out = out.filter((t) => t.notional >= minNotional);
    return out;
  }, [trades, filter, minNotional]);

  const flowAny: any = data.flow || {};
  const delay: number | undefined = flowAny.delay_minutes;

  return (
    <Panel
      title="Recent Large Option Trades (Unusual Flow)"
      noBody
      right={
        <>
          {/* A delayed tape presented without a label reads as real-time,
              which is the one thing it must not do. */}
          {delay ? (
            <span className="conn warn" title={flowAny.note || ''}>
              <i className="dot" />
              {flowAny.source} · {delay}m delayed
            </span>
          ) : null}
          <div className="filter-pills">
            {(['All', 'Sweeps', 'Blocks', 'Buys', 'Sells'] as FlowFilter[]).map((f) => (
              <button
                key={f}
                className={`filter-pill ${filter === f ? 'active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>
          <button
            className={`ghost-btn ${showFilters ? 'on' : ''}`}
            onClick={() => setShowFilters((s) => !s)}
          >
            <Filter size={11} />
            {showFilters ? 'Hide Filters' : 'Show Filters'}
          </button>
        </>
      }
    >
      {showFilters && (
        <div className="filter-bar">
          <label>
            Min notional
            <input
              type="number"
              step={10000}
              min={0}
              value={minNotional}
              onChange={(e) => setMinNotional(Number(e.target.value) || 0)}
            />
          </label>
          <span style={{ fontSize: 10, color: 'var(--text-mute)' }}>
            Showing {rows.length} of {trades.length} clustered prints
            {data.flow.all_count ? ` (${data.flow.all_count} total)` : ''}
          </span>
          <button className="ghost-btn" style={{ marginLeft: 'auto' }} onClick={() => { setMinNotional(0); setFilter('All'); }}>
            Reset
          </button>
        </div>
      )}

      {rows.length ? (
        <div className="tbl-scroll" style={{ maxHeight: 268 }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Time (ET)</th>
                <th>Ticker</th>
                <th>Expiration</th>
                <th className="r">Strike</th>
                <th>Type</th>
                <th className="r">Contracts</th>
                <th className="r">Price</th>
                <th className="r">Notional</th>
                <th>Side</th>
                <th>Sentiment</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t, i) => {
                const bull = t.sentiment === 'Bullish';
                return (
                  <tr key={`${t.epoch}-${t.strike}-${t.right}-${i}`}>
                    <td className="num" style={{ color: bull ? GREEN : RED }}>{t.time}</td>
                    <td>{t.symbol}</td>
                    <td className="num">{t.expiry_label}</td>
                    <td className="num r">{t.strike}</td>
                    <td style={{ color: t.right === 'C' ? GREEN : RED, fontWeight: 600 }}>{t.type}</td>
                    <td className="num r">{int(t.contracts)}</td>
                    <td className="num r">{money(t.price)}</td>
                    <td className="num r">{compactMoney(t.notional, 2)}</td>
                    <td
                      title={
                        t.exchanges?.length
                          ? `${t.prints} print(s) across ${t.exchanges.length} venue(s): ${t.exchanges.join(', ')}`
                          : undefined
                      }
                    >
                      {t.kind.charAt(0) + t.kind.slice(1).toLowerCase()}
                    </td>
                    <td style={{ color: bull ? GREEN : RED, fontWeight: 600 }}>
                      {t.sentiment}
                      {t.sentiment_confidence === 'LOW' && (
                        <span title="Side unclassified; direction inferred from contract type only"
                          style={{ color: 'var(--text-mute)', marginLeft: 3 }}>?</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <StateBlock status={trades.length ? 'NO_DATA' : data.flow.status} />
      )}


    </Panel>
  );
}

/* ------------------------------------------------------- flow breakdown */

const SEG_COLORS: Record<string, string> = {
  'Call Buys': GREEN,
  'Call Sells': '#0e7a4a',
  'Put Buys': RED,
  'Put Sells': '#8c2233',
  'Others': '#4a5568',
  'Mid / Unclassified': '#4a5568',
  'Call OI': GREEN,
  'Put OI': RED,
};

function BreakdownPanel({ data }: { data: OptionsOverview }) {
  const b = data.breakdown;
  const segments = (b.segments || []).map((s) => ({
    label: s.label,
    value: s.value,
    color: SEG_COLORS[s.label] || '#4a5568',
    percent: s.percent,
  }));

  return (
    <Panel title="Order Flow Breakdown" noBody>
      {segments.length ? (
        <>
          <div className="donut-wrap">
            <Donut
              segments={segments}
              centerTop={compact(b.total, b.total > 1e6 ? 2 : 0)}
              centerBottom={b.total_label}
            />
            <div className="donut-legend">
              {segments.map((s) => (
                <div className="dl-row" key={s.label}>
                  <i style={{ background: s.color }} />
                  <span className="dl-name">{s.label}</span>
                  <span className="dl-pct">{s.percent === null ? DASH : `${s.percent}%`}</span>
                </div>
              ))}
            </div>
          </div>
          {b.note && <div className="hint">{b.note}</div>}
        </>
      ) : (
        <StateBlock status={b.status} />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------ flow by expiry */

function ExpirationPanel({ data }: { data: OptionsOverview }) {
  const e = data.expiration_flow;
  const rows = (e.expirations || []).filter((x) => x.calls > 0 || x.puts > 0);

  return (
    <Panel title="Flow by Expiration" noBody>
      {rows.length ? (
        <>
          <div style={{ padding: '10px 6px 0' }}>
            <ResponsiveContainer width="100%" height={182}>
              <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 16, left: 6 }} barGap={3}>
                <CartesianGrid stroke="#182236" strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#5b6580', fontSize: 9 }} axisLine={false} tickLine={false}
                  label={{ value: 'Expiration', position: 'insideBottom',
                           offset: -2, fill: '#5b6580', fontSize: 9 }} />
                <YAxis
                  tick={{ fill: '#5b6580', fontSize: 9 }}
                  axisLine={false} tickLine={false}
                  tickFormatter={(v) => compact(v, 0)}
                  label={{ value: 'Volume', angle: -90, position: 'insideLeft',
                           fill: '#5b6580', fontSize: 9, offset: 16 }}
                />
                <Tooltip
                  cursor={{ fill: 'rgba(59,130,246,.08)' }}
                  formatter={(v) => compact(typeof v === 'number' ? v : 0, 0)}
                  contentStyle={{
                    background: '#0b1322', border: '1px solid #232f49',
                    borderRadius: 6, fontSize: 11,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 9, paddingTop: 6 }} iconSize={7} verticalAlign="top" align="right" />
                <Bar dataKey="calls" name="Calls" fill={GREEN} radius={[2, 2, 0, 0]} maxBarSize={22} isAnimationActive={false} />
                <Bar dataKey="puts" name="Puts" fill={RED} radius={[2, 2, 0, 0]} maxBarSize={22} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="hint">
            {e.basis === 'OPEN_INTEREST'
              ? 'Open interest per expiry — traded volume covered only the front expiry'
              : e.basis === 'LAST_SESSION_TAPE'
                ? 'Contracts traded per expiry · last completed session'
                : 'Contracts traded per expiry · current session'}
          </div>
        </>
      ) : (
        <StateBlock status={e.status} />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------ insights */

function InsightsPanel({ data, demo }: { data: OptionsOverview; demo?: boolean }) {
  const t = data.tiles;
  const m = data.metrics || {};
  const rz = data.risk_zones || {};
  const f = data.flow;
  const s = data.sentiment;

  const biggest = (f.trades || [])
    .slice()
    .sort((a, b) => b.notional - a.notional)[0];

  const items: { text: string; good: boolean }[] = [];

  if (biggest) {
    items.push({
      text: `Largest print: ${compact(biggest.contracts, 0)} ${biggest.type.toLowerCase()}s at the ${biggest.strike} strike (${biggest.expiry_label}) for ${compactMoney(biggest.notional)} — a ${biggest.kind.toLowerCase()}.`,
      good: biggest.sentiment === 'Bullish',
    });
  }
  if (t.put_call_volume != null) {
    items.push({
      text: `Put/call ratio of ${num(t.put_call_volume)} with calls at ${pct(t.call_share, 0)} of traded volume.`,
      good: t.put_call_volume < 1,
    });
  }
  if (f.sweeps) {
    items.push({
      text: `${f.sweeps} multi-venue sweeps and ${f.blocks} blocks detected across ${f.contracts_sampled} sampled contracts.`,
      good: true,
    });
  }
  if (m.iv_percentile != null) {
    items.push({
      text: `Implied volatility sits in the ${num(m.iv_percentile, 0)}th percentile of the past year (IV rank ${num(m.iv_rank, 0)}).`,
      good: m.iv_percentile <= 50,
    });
  }
  if (rz.put_wall && rz.call_wall) {
    items.push({
      text: `Open interest concentrates support at ${money(rz.put_wall, 0)} and resistance at ${money(rz.call_wall, 0)}, with max pain at ${money(rz.max_pain, 0)}.`,
      good: true,
    });
  }
  if (m.expected_move_percent != null && m.realized_move_percent != null) {
    items.push({
      text: `Options imply ±${num(m.expected_move_percent)}% by ${data.expiry_label} versus ${num(m.realized_move_percent)}% realised over comparable windows.`,
      good: m.realized_move_percent <= m.expected_move_percent,
    });
  }
  if (s.score != null) {
    items.push({
      text: `Composite flow sentiment reads ${s.score}/100 — ${s.label.toLowerCase()}.`,
      good: s.label === 'BULLISH',
    });
  }

  const shown = demo ? DEMO_FLOW_INSIGHTS : items;

  return (
    <Panel title="Options Flow Insights (AI)" icon={<Sparkles size={12} />} noBody>
      {shown.length ? (
        <div className="bullets">
          {shown.map((it, i) => (
            <div className="bullet" key={i}>
              {it.good
                ? <CheckCircle2 size={13} color={GREEN} />
                : <AlertTriangle size={13} color="var(--amber)" />}
              <span>{it.text}</span>
            </div>
          ))}
        </div>
      ) : (
        <StateBlock status="INSUFFICIENT_DATA" />
      )}
    </Panel>
  );
}

/* --------------------------------------------------------- trade ideas */

function TradeIdeasPanel({ data, demo }: { data: OptionsOverview; demo?: boolean }) {
  const [tab, setTab] = useState<'Directional' | 'Volatility' | 'Income'>('Directional');
  const m = data.metrics || {};
  const rz = data.risk_zones || {};
  const trades = data.flow.trades || [];

  // A directional idea wants the busiest out-of-the-money strike. Ranking the
  // whole tape surfaces deep-ITM prints, where "double the premium" is not a
  // realistic target. Fall back to any strike if nothing OTM traded.
  const spot = data.spot ?? 0;
  const byNotional = (a: FlowTrade, b: FlowTrade) => b.notional - a.notional;
  const calls = trades.filter((t) => t.right === 'C').sort(byNotional);
  const puts = trades.filter((t) => t.right === 'P').sort(byNotional);
  const topCall = calls.find((t) => t.strike > spot) ?? calls[0];
  const topPut = puts.find((t) => t.strike < spot) ?? puts[0];

  const ideas = useMemo(() => {
    if (tab === 'Directional') {
      return [
        topCall && {
          kind: 'bull' as const,
          title: 'Bullish Call',
          sub: `${topCall.expiry_label} · ${topCall.strike}C`,
          tag: topCall.kind === 'SWEEP' ? 'High Conviction' : 'Flow Follow',
          entry: topCall.price,
          target: topCall.price * 2,
          stop: topCall.price * 0.5,
        },
        topPut && {
          kind: 'bear' as const,
          title: 'Bearish Put',
          sub: `${topPut.expiry_label} · ${topPut.strike}P`,
          tag: 'Hedge',
          entry: topPut.price,
          target: topPut.price * 2,
          stop: topPut.price * 0.5,
        },
      ].filter(Boolean);
    }
    if (tab === 'Volatility') {
      const cheap = (m.iv_percentile ?? 50) <= 40;
      return [
        m.atm_straddle && {
          kind: cheap ? ('bull' as const) : ('bear' as const),
          title: cheap ? 'Long ATM Straddle' : 'Short ATM Straddle',
          sub: `${data.expiry_label} · ${m.atm_call_strike} straddle`,
          tag: cheap ? `IV ${pct(m.iv_percentile, 0)} — cheap` : `IV ${pct(m.iv_percentile, 0)} — rich`,
          entry: m.atm_straddle,
          target: m.atm_straddle * (cheap ? 1.8 : 0.4),
          stop: m.atm_straddle * (cheap ? 0.55 : 1.6),
        },
      ].filter(Boolean);
    }
    return [
      rz.call_wall && m.atm_call_price && {
        kind: 'bull' as const,
        title: 'Covered Call',
        sub: `Sell ${rz.call_wall}C · ${data.expiry_label}`,
        tag: 'Income',
        entry: m.atm_call_price,
        target: 0,
        stop: m.atm_call_price * 2,
      },
      rz.put_wall && m.atm_put_price && {
        kind: 'bear' as const,
        title: 'Cash-Secured Put',
        sub: `Sell ${rz.put_wall}P · ${data.expiry_label}`,
        tag: 'Income',
        entry: m.atm_put_price,
        target: 0,
        stop: m.atm_put_price * 2,
      },
    ].filter(Boolean);
  }, [tab, topCall, topPut, m, rz, data.expiry_label]);

  const shown = demo && tab === 'Directional' ? DEMO_TRADE_IDEAS : ideas;

  return (
    <Panel
      title="AI Trade Ideas"
      icon={<Target size={12} />}
      noBody
      right={
        <div className="seg">
          {(['Directional', 'Volatility', 'Income'] as const).map((t) => (
            <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>
      }
    >
      {shown.length ? (
        <>
          <div className="idea-grid" style={shown.length === 1 ? { gridTemplateColumns: '1fr' } : undefined}>
            {(shown as any[]).map((idea, i) => {
              const rr = idea.rr ?? (
                idea.target && idea.stop && idea.entry
                  ? Math.abs(idea.target - idea.entry) / Math.abs(idea.entry - idea.stop)
                  : null);
              return (
                <div className={`idea ${idea.kind}`} key={i}>
                  <div className="idea-head">
                    {idea.kind === 'bull'
                      ? <TrendingUp size={14} color={GREEN} />
                      : <TrendingDown size={14} color={RED} />}
                    <div style={{ minWidth: 0 }}>
                      <div className="idea-title" style={{ color: idea.kind === 'bull' ? GREEN : RED }}>
                        {idea.title}
                      </div>
                      <div className="idea-sub">{idea.sub}</div>
                    </div>
                    <span className="badge gray" style={{ marginLeft: 'auto' }}>{idea.tag}</span>
                  </div>
                  <div className="idea-legs">
                    <div className="idea-leg">
                      <div className="il-label">Entry</div>
                      <div className="il-value">{money(idea.entry)}</div>
                    </div>
                    <div className="idea-leg">
                      <div className="il-label">Target</div>
                      <div className="il-value">{idea.target ? money(idea.target) : 'Expiry'}</div>
                    </div>
                    <div className="idea-leg">
                      <div className="il-label">Stop</div>
                      <div className="il-value">{money(idea.stop)}</div>
                    </div>
                  </div>
                  {rr !== null && Number.isFinite(rr) && (
                    <div className="idea-rr">R/R <b>{num(rr, 1)} : 1</b></div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="hint">
            Built from the live chain and the largest prints on the tape. Targets use
            the open-interest walls, stops use 50% of premium. Not investment advice.
          </div>
        </>
      ) : (
        <StateBlock status="INSUFFICIENT_DATA" />
      )}
    </Panel>
  );
}

/* ------------------------------------------- advanced options data (gaps) */

/**
 * The metrics this pipeline cannot produce, and why.
 *
 * Kept as a visible card rather than silently omitted: knowing a number is
 * absent - and what would be needed to get it - matters more than the number.
 */
function AdvancedDataPanel({ data }: { data: OptionsOverview }) {
  const basis: any = (data as any).data_basis;
  if (!basis) return null;

  const unsupported = Object.values(basis.unsupported || {}) as any[];
  const supported = Object.entries(basis.supported || {}) as [string, any][];

  return (
    <Panel
      title="Data Provenance"
      icon={<Info size={12} />}
      noBody
      right={<span className="badge blue">
        {unsupported.length} require advanced data
      </span>}
    >
      <div className="prov-list">
        {unsupported.map((u) => (
          <div className="prov-row unsupported" key={u.label}>
            <div className="prov-row-head">
              <b>{u.label}</b>
              <span className="badge blue">REQUIRES ADVANCED OPTIONS DATA</span>
            </div>
            <div className="prov-reason">{u.reason}</div>
            <div className="need-provider">Needed: {u.needs}</div>
          </div>
        ))}
      </div>

      <details className="prov-details">
        <summary>What every shown metric is computed from ({supported.length})</summary>
        <div className="prov-list">
          {supported.map(([key, v]) => (
            <div className="prov-row" key={key}>
              <div className="prov-row-head">
                <b>{key.replace(/_/g, ' ')}</b>
                <StatusChip status={v.available ? 'OK' : v.status} />
              </div>
              <div className="prov-reason">{v.basis}</div>
              {v.note && <div className="prov-note">{v.note}</div>}
            </div>
          ))}
        </div>
      </details>
    </Panel>
  );
}

/* ------------------------------------------------------------ risk zones */

function UnusualActivityPanel({ data }: { data: OptionsOverview }) {
  const rows: any[] = ((data as any).unusual || []);
  if (!rows.length) {
    return (
      <Panel title="Unusual Options Activity" noBody>
        <StateBlock status="NO_DATA" />
      </Panel>
    );
  }

  return (
    <Panel title="Unusual Options Activity" icon={<AlertTriangle size={12} />} noBody>
      <div className="tbl-scroll" style={{ maxHeight: 268 }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>Type</th>
              <th className="r">Strike</th>
              <th>Expiration</th>
              <th className="r">Volume</th>
              <th className="r">Avg</th>
              <th className="r">vs Avg</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((u, i) => {
              const call = u.right === 'C';
              return (
                <tr key={`${u.right}-${u.strike}-${u.expiry_label}-${i}`}>
                  <td style={{ color: call ? GREEN : RED, fontWeight: 600 }}>
                    {call ? 'Call' : 'Put'}
                  </td>
                  <td className="num r">{u.strike}</td>
                  <td className="num">{u.expiry_label}</td>
                  <td className="num r">{int(u.volume)}</td>
                  <td className="num r">{int(u.average_volume)}</td>
                  <td className="num r" style={{ color: AMBER, fontWeight: 700 }}>
                    {num(u.ratio, 0)}x
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="hint">
        Session volume against each contract's own trailing average. Contracts
        with no meaningful baseline are excluded rather than shown with an
        inflated multiple.
      </div>
    </Panel>
  );
}

function PositioningPanel({ data }: { data: OptionsOverview }) {
  const p: any = (data as any).positioning || {};
  if (p.status !== 'OK') {
    return (
      <Panel title="Dealer Positioning" noBody>
        <StateBlock status={p.status || 'NO_DATA'} />
      </Panel>
    );
  }

  const long = p.gamma_regime === 'LONG_GAMMA';
  const bullish = p.premium_bias === 'Bullish';

  const rows: { k: string; v: string; cls?: string; title?: string }[] = [
    {
      k: 'Net Gamma Exposure',
      v: compact(p.net_gex, 1),
      cls: long ? 'pos' : 'neg',
      title: 'Call GEX plus put GEX, as published by the provider',
    },
    {
      k: 'Gamma Regime',
      v: long ? 'Long gamma' : 'Short gamma',
      cls: long ? 'pos' : 'neg',
      title: long
        ? 'Dealers hedge against the move, which tends to dampen volatility'
        : 'Dealers hedge with the move, which tends to amplify volatility',
    },
    { k: 'Call Premium', v: `$${compact(p.call_premium, 1)}` },
    { k: 'Put Premium', v: `$${compact(p.put_premium, 1)}` },
    {
      k: 'Call Share of Premium',
      v: pct(p.call_premium_share, 1),
      cls: bullish ? 'pos' : 'neg',
      title: 'Premium weighs conviction better than contract counts do',
    },
    { k: 'Premium Bias', v: p.premium_bias || '--', cls: bullish ? 'pos' : 'neg' },
    { k: 'Trades', v: compact(p.trade_count, 0) },
  ];

  return (
    <Panel title="Dealer Positioning" icon={<Info size={12} />} noBody>
      <div className="kv">
        {rows.map((r) => (
          <div className="kv-row" key={r.k} title={r.title}>
            <span className="k">{r.k}</span>
            <span className={`v ${r.cls || ''}`}>{r.v}</span>
          </div>
        ))}
      </div>
      <div className="hint">
        Gamma exposure and premium totals are published by OptionData, not
        derived here. Gamma is a volatility read, not a direction call, so it
        sits beside the bias score rather than inside it.
        {p.as_of ? ` As of ${p.as_of}.` : ''}
      </div>
    </Panel>
  );
}

/**
 * Aggregate payout across strikes, trimmed to the band around spot.
 *
 * The single max-pain number hides how sharply it is defined: a deep, narrow
 * trough is a meaningful level, a shallow one is barely a preference. Payouts
 * are plotted relative to the minimum because the absolute figures run to tens
 * of billions and would otherwise flatten the curve into a line.
 */
function MaxPainCurve({
  rows, maxPain, spot,
}: { rows?: any[]; maxPain?: number | null; spot?: number | null }) {
  if (!rows || rows.length < 5) return null;

  return (
    <div style={{ height: 132, margin: '4px 0 2px' }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="strike" type="number" domain={['dataMin', 'dataMax']}
            tick={{ fontSize: 9, fill: 'var(--text-mute)' }}
            tickLine={false} axisLine={false}
          />
          <YAxis hide domain={['dataMin', 'dataMax']} />
          <Tooltip
            contentStyle={{
              background: 'var(--panel)', border: '1px solid var(--border-2)',
              borderRadius: 6, fontSize: 11,
            }}
            labelFormatter={(v) => `Strike ${v}`}
            formatter={(v: any) => [compact(v, 1), 'Payout above minimum']}
          />
          {spot ? (
            <ReferenceLine x={spot} stroke="var(--text-mute)" strokeDasharray="3 3"
              label={{ value: 'Spot', fontSize: 9, fill: 'var(--text-mute)', position: 'top' }} />
          ) : null}
          {maxPain ? (
            <ReferenceLine x={maxPain} stroke={AMBER} strokeWidth={1.5}
              label={{ value: 'Max pain', fontSize: 9, fill: AMBER, position: 'insideTopRight' }} />
          ) : null}
          <Line type="monotone" dataKey="relative" stroke="#3b82f6"
            strokeWidth={1.6} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function RiskZonesPanel({ data }: { data: OptionsOverview }) {
  const rz = data.risk_zones || {};
  if (rz.status !== 'OK') {
    return <Panel title="Risk Zones" noBody><StateBlock status={rz.status} /></Panel>;
  }

  const zones = [
    {
      color: RED, name: 'Call Wall (Resistance)', value: rz.call_wall,
      sub: rz.call_wall_oi ? `${compact(rz.call_wall_oi, 0)} contracts open` : '',
      icon: <TrendingUp size={11} color={RED} />,
    },
    {
      color: GREEN, name: 'Put Wall (Support)', value: rz.put_wall,
      sub: rz.put_wall_oi ? `${compact(rz.put_wall_oi, 0)} contracts open` : '',
      icon: <TrendingDown size={11} color={GREEN} />,
    },
    {
      color: '#8892a8', name: 'Max Pain', value: rz.max_pain,
      sub: 'Least aggregate payout at expiry',
      icon: <Shield size={11} color="#8892a8" />,
    },
  ];

  return (
    <Panel title="Risk Zones" icon={<Info size={12} />} noBody>
      <div>
        {zones.map((z) => (
          <div className="zone-row" key={z.name}>
            <i className="zi" style={{ background: z.color }} />
            <div style={{ minWidth: 0 }}>
              <div className="zone-name">{z.name}</div>
              <div className="zone-sub">{z.sub}</div>
            </div>
            <div className="zone-value" style={{ color: z.color }}>{money(z.value, 0)}</div>
          </div>
        ))}
      </div>
      <MaxPainCurve rows={(rz as any).max_pain_curve} maxPain={rz.max_pain} spot={data.spot} />
      <div className="hint">
        Computed from quoted open interest on the {data.expiry_label} expiry —
        the walls are the largest-OI strikes, max pain the strike minimising
        aggregate intrinsic payout. Not a dealer-positioning model.
      </div>
    </Panel>
  );
}
