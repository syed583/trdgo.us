import React, { useMemo, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  AlertCircle, AlertTriangle, ArrowRight, BarChart3, CalendarDays, CheckCircle2,
  ChevronRight, Grid3x3, Info, Newspaper, Sparkles, Star, Target, TrendingUp,
} from 'lucide-react';
import Freshness from '../components/Freshness';
import { NavLink } from 'react-router-dom';
import { StatusChip, Unavailable } from './shared';
import { api, api2 } from '../api/client';
import type { PageContext } from '../App';
import {
  DEMO_ACTION_PLAN, DEMO_AI_SUMMARY, DEMO_EARNINGS, DEMO_SEVERE_RISKS,
} from '../demo';
import type { EarningsOverview, IndicesPayload, WatchlistPayload } from '../api/client';
import { useApi } from '../hooks/useApi';
import InstitutionalPanel from '../components/InstitutionalPanel';
import DirectionalScorePanel from '../components/DirectionalScorePanel';
import {
  Donut, LogoMark, Panel, ScoreGauge, Sparkline, StateBlock, ratingColor,
} from '../components/common';
import PriceChart from '../components/PriceChart';
import {
  compact, compactMoney, DASH, money, num, pct, plusMinus, signed,
  signedPct, tone,
} from '../lib/format';

const RANGES = ['1D', '5D', '1M', '3M', '6M', 'YTD', '1Y', '5Y', 'ALL'];

/**
 * Stock sub-tabs.
 *
 * Some are views of this page (they change which panels render); the rest are
 * whole screens of their own and navigate there. Nothing here is decorative.
 */
const SUBTABS: { key: string; label: string; route?: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'earnings', label: 'Earnings' },
  { key: 'fundamentals', label: 'Fundamentals' },
  { key: 'estimates', label: 'Estimates' },
  { key: 'technical', label: 'Technical' },
  { key: 'options', label: 'Options', route: '/options-flow' },
  { key: 'news', label: 'News', route: '/news' },
  { key: 'ai', label: 'AI Analysis', route: '/ai-insights' },
  { key: 'plan', label: 'Trade Plan' },
];

/** Which panels each in-page view shows. */
const VIEW_PANELS: Record<string, string[]> = {
  overview: ['chart', 'score', 'history', 'bull', 'risk', 'vol', 'snapshot', 'em', 'estimates', 'ai', 'plan'],
  earnings: ['snapshot', 'em', 'history', 'score'],
  fundamentals: ['score', 'bull', 'risk'],
  estimates: ['estimates', 'score'],
  technical: ['chart', 'score'],
  plan: ['plan', 'ai', 'score', 'em'],
};

const COMPONENT_LABELS: Record<string, string> = {
  fundamentals: 'Fundamentals',
  estimates: 'Estimate Revisions',
  technicals: 'Technical',
  earnings_history: 'Earnings History',
  options: 'Options / Expected Move',
  market_environment: 'Market / Sector',
};

const COMPONENT_ORDER = [
  'fundamentals', 'estimates', 'technicals',
  'earnings_history', 'options', 'market_environment',
];

export default function EarningsPage({ ctx }: { ctx: PageContext }) {
  const { symbol, onSymbol, strip: watchlist, indices, demo } = ctx;
  const [range, setRange] = useState('6M');
  const [subtab, setSubtab] = useState('overview');
  const shows = (panel: string) =>
    (VIEW_PANELS[subtab] || VIEW_PANELS.overview).includes(panel);
  const [dateKey, setDateKey] = useState('TODAY');
  const [watchBusy, setWatchBusy] = useState(false);

  // The watchlist is server-side, so the star reflects what is actually saved.
  const saved = useApi<any>(
    (sig) => (demo ? Promise.resolve({ rows: [] }) : api2.watchlist(sig)),
    [demo],
  );
  const watchedSymbols: string[] = (saved.data?.rows || []).map((r: any) => r.symbol);

  const toggleWatch = async () => {
    if (demo) return;
    setWatchBusy(true);
    try {
      if (watchedSymbols.includes(symbol)) {
        await api2.watchlistRemove(symbol);
      } else {
        await api2.watchlistAdd(symbol);
      }
      saved.refresh();
      watchlist.refresh();
    } finally {
      setWatchBusy(false);
    }
  };

  const overview = useApi<EarningsOverview>(
    (signal) => (demo
      ? Promise.resolve(DEMO_EARNINGS)
      : api.earningsOverview(symbol, range, signal)),
    [symbol, range, demo],
  );

  const d = overview.data;

  return (
    <>
      <DateStrip
        indices={indices}
        active={dateKey}
        onPick={setDateKey}
        search={ctx.search}
      />

      <div className="page">
        <TickerRow watchlist={watchlist} symbol={symbol} onSymbol={onSymbol} />

        {overview.error ? (
          <Panel title="Earnings Intelligence">
            <StateBlock error={overview.error} />
          </Panel>
        ) : overview.initialLoading || !d ? (
          <Panel title={`${symbol} · Earnings Intelligence`}>
            <StateBlock loading />
          </Panel>
        ) : (
          <>
            <StockHeader
              data={d}
              watched={watchedSymbols.includes(symbol)}
              busy={watchBusy}
              onWatch={toggleWatch}
              search={ctx.search}
            />

            <div className="subtabs" role="tablist">
              {SUBTABS.map((t) => (t.route ? (
                <NavLink
                  key={t.key}
                  to={`${t.route}/${symbol}${ctx.search}`}
                  className="subtab"
                  role="tab"
                >
                  {t.label}
                </NavLink>
              ) : (
                <button
                  key={t.key}
                  role="tab"
                  aria-selected={subtab === t.key}
                  className={`subtab ${subtab === t.key ? 'active' : ''}`}
                  onClick={() => setSubtab(t.key)}
                >
                  {t.label}
                </button>
              )))}
            </div>

            <div className="earn-grid">
              <div className="earn-left">
                <div className="chart-score-grid">
                  {shows('chart') && (
                    <ChartPanel data={d} range={range} onRange={setRange} loading={overview.loading} />
                  )}
                  {shows('score') && <ScorePanel symbol={ctx.symbol} />}
                </div>

                <div className="lower-grid">
                  {shows('history') && <HistoryPanel data={d} />}
                  {shows('bull') && <ReasonsPanel data={d} kind="bull" />}
                  {shows('risk') && <ReasonsPanel data={d} kind="risk" demo={demo} />}
                  {shows('vol') && <VolatilityPanel data={d} search={ctx.search} />}
                </div>
              </div>

              <div className="earn-right">
                {shows('snapshot') && <SnapshotPanel data={d} />}
                {shows('estimates') && <EstimatesPanel data={d} />}
                {/* Quarterly and slow-moving, so it sits with the other
                    reference panels rather than beside live price action. */}
                {shows('estimates') && <InstitutionalPanel symbol={ctx.symbol} />}
              </div>
            </div>

            <div className="bottom-grid">
              {shows('ai') && <AISummaryPanel data={d} demo={demo} />}
              {shows('plan') && (
                <ActionPlanPanel data={d} demo={demo}
                  onOpenPlan={() => setSubtab('plan')} />
              )}
            </div>
          </>
        )}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- strip */

function DateStrip({
  indices, active, onPick, search,
}: {
  indices: ReturnType<typeof useApi<IndicesPayload>>;
  active: string;
  onPick: (k: string) => void;
  search: string;
}) {
  const data = indices.data;
  const dates = data?.dates;
  const market = data?.market;

  const order = ['today', 'tomorrow', 'this_week', 'next_week', 'this_month'];
  // Dow-30 names report in Jan/Apr/Jul/Oct, so every fixed window is often
  // legitimately empty. The backend appends a "next_up" window pointing at the
  // next real report; show it only when it adds something the others don't.
  const nextUp = dates?.next_up;
  const anyInWindows = order.some((k) => (dates?.[k]?.earnings_count || 0) > 0);

  const dotClass =
    market?.session === 'OPEN' ? 'open'
      : market?.session === 'CLOSED' ? 'closed' : 'ext';

  return (
    <div className="datestrip">
      {order.map((k) => {
        const item = dates?.[k];
        const count = item?.earnings_count;
        return (
          <NavLink
            key={k}
            to={`/earnings-calendar${search}`}
            className={`date-card ${active === (item?.key || k.toUpperCase()) ? 'active' : ''}`}
            onClick={() => onPick(item?.key || k.toUpperCase())}
            title={`Open the earnings calendar for ${item?.label || k}`}
          >
            <div className="dc-label">{item?.label || k.replace('_', ' ')}</div>
            <div className="dc-value">{item?.value || DASH}</div>
            <div className={`dc-earn${count ? '' : ' none'}`}>
              {count === undefined ? DASH
                : count === 0 ? 'No earnings'
                  : `${count} report${count === 1 ? 's' : ''}`}
            </div>
            {!!count && !!item?.earnings_symbols?.length && (
              <div className="dc-syms">
                {item.earnings_symbols.slice(0, 3).join(' · ')}
                {count > 3 ? ` +${count - 3}` : ''}
              </div>
            )}
          </NavLink>
        );
      })}

      {nextUp && !anyInWindows && (
        <NavLink
          to={`/earnings-calendar${search}`}
          className="date-card next-up"
          title={`Next tracked earnings: ${nextUp.value}`}
        >
          <div className="dc-label">{nextUp.label}</div>
          <div className="dc-value">{nextUp.value}</div>
          <div className="dc-earn">
            {nextUp.days_away === 0 ? 'today'
              : `in ${nextUp.days_away} day${nextUp.days_away === 1 ? '' : 's'}`}
          </div>
          {!!nextUp.earnings_symbols?.length && (
            <div className="dc-syms">{nextUp.earnings_symbols.slice(0, 3).join(' · ')}</div>
          )}
        </NavLink>
      )}

      <div className="market-status">
        <div className="ms-label">Market Status</div>
        <div className="ms-value">
          <i className={`dot ${dotClass}`} />
          {market ? `${market.label} · ${market.time_et}` : 'Checking…'}
        </div>
      </div>

      <div className="index-cards">
        {(data?.indices || []).map((ix) => {
          const t = tone(ix.change_percent);
          return (
            <NavLink
              className="index-card"
              key={ix.label}
              to={`/market${search}`}
              title="Open Market Overview"
            >
              <div className="ic-main">
                <div className="ic-label">
                  {ix.label}
                  {ix.is_proxy && (
                    <span className="proxy-tag" title={`Index not subscribed; showing ${ix.instrument} tracker`}>
                      {ix.instrument}
                    </span>
                  )}
                </div>
                <div className="ic-value">{num(ix.value)}</div>
                <div className={`ic-chg ${t}`}>{signedPct(ix.change_percent)}</div>
              </div>
              <div className="ic-spark">
                <Sparkline
                  points={ix.spark}
                  color={t === 'neg' ? 'var(--red)' : 'var(--green)'}
                />
              </div>
            </NavLink>
          );
        })}
        {indices.error && <div className="conn down"><i className="dot" />Indices offline</div>}
      </div>
    </div>
  );
}

/* --------------------------------------------------------- ticker cards */

function TickerRow({
  watchlist,
  symbol,
  onSymbol,
}: {
  watchlist: ReturnType<typeof useApi<WatchlistPayload>>;
  symbol: string;
  onSymbol: (s: string) => void;
}) {
  if (watchlist.error) {
    return (
      <Panel title="Watchlist">
        <StateBlock error={watchlist.error} compact />
      </Panel>
    );
  }

  if (watchlist.initialLoading || !watchlist.data) {
    return (
      <div className="ticker-row">
        {Array.from({ length: 10 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 62 }} />
        ))}
      </div>
    );
  }

  return (
    <div className="ticker-row">
      {watchlist.data.cards.map((c) => {
        const color = ratingColor(c.rating);
        return (
          <button
            key={c.symbol}
            className={`ticker-card ${c.symbol === symbol ? 'active' : ''}`}
            onClick={() => onSymbol(c.symbol)}
            title={[
              c.name,
              watchlist.data!.score_basis,
              c.earnings?.detail,
            ].filter(Boolean).join(' · ')}
          >
            <div className="tc-top">
              <LogoMark symbol={c.symbol} className="tc-logo" />
              <div style={{ minWidth: 0 }}>
                <div className="tc-sym">{c.symbol}</div>
                {/* A seed date must never read like a confirmed schedule. */}
                <div
                  className={`tc-when${
                    c.earnings?.status === 'TEST_DATA' ? ' tc-when-seed' : ''
                  }`}
                >
                  {c.earnings?.status === 'TEST_DATA'
                    ? `SEED · ${c.earnings.short_label || 'dated'}`
                    : c.earnings?.short_label || 'No date'}
                </div>
              </div>
            </div>
            <div className="tc-score" style={{ color }}>
              {c.score === null ? DASH : signed(c.score, 0)}
            </div>
            <div className="tc-rating" style={{ color }}>
              {c.rating || 'Unscored'}
            </div>
          </button>
        );
      })}
    </div>
  );
}

/* --------------------------------------------------------- stock header */

function StockHeader({
  data, watched, onWatch, busy, search,
}: {
  data: EarningsOverview;
  watched: boolean;
  onWatch: () => void;
  busy?: boolean;
  search: string;
}) {
  const q = data.quote;
  const e = data.earnings;
  const t = tone(q.change);

  return (
    <section className="panel">
      <div className="stock-head">
        <LogoMark symbol={q.symbol} className="sh-logo" />
        <div className="sh-id">
          <div className="sh-sym">{q.symbol}</div>
          <div className="sh-name">{q.name}</div>
          <div className="sh-tags">
            {(q.tags || []).map((tag) => (
              <span className="chip" key={tag}>{tag}</span>
            ))}
          </div>
        </div>

        <div className="sh-px">
          <div className="p">{money(q.price)}</div>
          <div className={`c ${t}`}>
            {signed(q.change)} ({signedPct(q.change_percent)})
          </div>
          {/* The headline figures stay on the last completed session. An
              extended print sits beneath, labelled, so the two are never
              mistaken for one another. */}
          {q.extended && (
            <div className={`sh-ext ${tone(q.extended.change)}`}>
              <b>{q.extended.session === 'PRE_MARKET' ? 'Pre-Market' : 'After Hours'}</b>
              {money(q.extended.price)}
              <span>
                {signed(q.extended.change)} ({signedPct(q.extended.change_percent)})
              </span>
            </div>
          )}
          <div className="u">
            <Freshness stamp={(q as any).freshness} compact />
            {/* A quote from a fallback provider may carry no session clock.
                Reading through it unguarded unmounted this entire page --
                chart, chain, score and all -- over a missing timestamp. */}
            {q.market
              ? `${q.market.label} · ${q.market.date_et} ${q.market.time_et}`
              : 'Session time unavailable'}
          </div>
        </div>

        <div className="sh-earn">
          <CalendarDays size={15} color="var(--text-dim)" />
          <div>
            <div className="lbl">Earnings Date</div>
            <div className="val">
              {e?.status === 'OK' ? e.date_label : DASH}
            </div>
            <div className="sub">
              {e?.status === 'OK'
                ? e.timing_label
                : e?.status === 'NOT_TRACKED'
                  ? 'Not in earnings database'
                  : 'No scheduled event'}
            </div>
          </div>
        </div>

        <button className={`btn-watch ${watched ? 'on' : ''}`}
          onClick={onWatch} disabled={busy}>
          <Star size={13} fill={watched ? 'currentColor' : 'none'} />
          {busy ? 'Saving…' : watched ? 'In Watchlist' : 'Add to Watchlist'}
        </button>
        <NavLink className="icon-btn" title="Option chain"
          to={`/options-chain/${q.symbol}${search}`}>
          <Grid3x3 size={16} />
        </NavLink>
        <NavLink className="icon-btn" title="News"
          to={`/news/${q.symbol}${search}`}>
          <Newspaper size={16} />
        </NavLink>
      </div>
    </section>
  );
}

/* ----------------------------------------------------------- chart panel */

function ChartPanel({
  data,
  range,
  onRange,
  loading,
}: {
  data: EarningsOverview;
  range: string;
  onRange: (r: string) => void;
  loading: boolean;
}) {
  const c = data.chart;
  const ok = c.status === 'OK' && c.bars.length > 0;
  const t = tone(c.change);

  return (
    <Panel noBody>
      <div className="chart-head">
        <span className="cl-title">{data.symbol} · {range}</span>
        {ok && (
          <span className="cl-ohlc">
            <span>O<b>{num(c.ohlc?.open)}</b></span>
            <span>H<b>{num(c.ohlc?.high)}</b></span>
            <span>L<b>{num(c.ohlc?.low)}</b></span>
            <span>C<b>{num(c.ohlc?.close)}</b></span>
            <span className={t}>{signed(c.change)} ({signedPct(c.change_percent)})</span>
          </span>
        )}
      </div>

      {ok ? (
        <div className="chart-stage">
          {/* Overlaid the way a trading terminal stacks its moving averages. */}
          <div className="ema-stack">
            <span className="cl-ema"><i style={{ background: '#3b82f6' }} />EMA 20 <b>{num(c.ema?.ema20)}</b></span>
            <span className="cl-ema"><i style={{ background: '#f5a524' }} />EMA 50 <b>{num(c.ema?.ema50)}</b></span>
            <span className="cl-ema"><i style={{ background: '#a78bfa' }} />EMA 200 <b>{num(c.ema?.ema200)}</b></span>
            {/* Only shown when levels were actually found: a legend entry for
                a line that is not on the chart is worse than no legend. */}
            {(c.levels?.resistance?.length || 0) > 0 && (
              <span className="cl-ema" title={c.levels?.note || ''}>
                <i style={{ background: '#fb7185' }} />Resistance
                <b>{c.levels!.resistance.length}</b>
              </span>
            )}
            {(c.levels?.support?.length || 0) > 0 && (
              <span className="cl-ema" title={c.levels?.note || ''}>
                <i style={{ background: '#2dd4bf' }} />Support
                <b>{c.levels!.support.length}</b>
              </span>
            )}
          </div>
          <PriceChart bars={c.bars} lastPrice={data.quote.price} levels={c.levels} />
        </div>
      ) : (
        <StateBlock status={c.status} error={c.error} loading={loading} />
      )}

      <div className="range-row">
        {RANGES.map((r) => (
          <button
            key={r}
            className={`range-btn ${range === r ? 'active' : ''}`}
            onClick={() => onRange(r)}
          >
            {r}
          </button>
        ))}
        <button className="adv-chart" onClick={() => onRange('ALL')}
          title="Load the full available history">
          <BarChart3 size={11} />
          Advanced Chart
        </button>
      </div>
    </Panel>
  );
}

/* ----------------------------------------------------------- score panel */

/**
 * The headline score, driven by the general directional model.
 *
 * This used to show the six earnings components (fundamentals, estimate
 * revisions, and so on). Those answer "how does this company look going into
 * a report"; the sixteen parameters here answer "which way is the stock
 * leaning now", which is what the panel is actually being read for. The
 * earnings composite still exists and still has its own model -- it is simply
 * not what this gauge reports.
 *
 * Every parameter opens its own reasoning, so a score can be audited rather
 * than trusted.
 */
function ScorePanel({ symbol }: { symbol: string }) {
  return <DirectionalScorePanel symbol={symbol} />;
}

/* -------------------------------------------------------- earnings history */

/** One bar on the earnings-history chart. */
interface QuarterBar {
  label: string;
  Estimate: number | null;
  Actual: number | null;
  surprise: number | null;
}

function HistoryPanel({ data }: { data: EarningsOverview }) {
  const h: any = data.history;
  const ok = h?.quarters?.length > 0;
  const chartable = (h?.quarters || []).some(
    (q: any) => (q.eps_estimate ?? q.estimate) != null
      || (q.eps_actual ?? q.actual) != null,
  );
  // Statistics are only real when the backend computed them from verified
  // provider rows; seed rows render as bars but never as a beat rate.
  const verified = h?.basis === 'VERIFIED';

  // Typed explicitly: `h` is `any`, so without this every callback over
  // chartData silently takes `any` parameters and stops being checked.
  const chartData = useMemo<QuarterBar[]>(
    () => ((h?.quarters || []) as any[])
      .slice()
      .reverse()                       // oldest first, left to right
      .map((q: any) => ({
        label: q.quarter_label || q.label,
        Estimate: q.eps_estimate ?? q.estimate,
        Actual: q.eps_actual ?? q.actual,
        surprise: q.eps_surprise_percent ?? q.surprise_percent,
      }))
      .filter((q: any) => q.Estimate != null || q.Actual != null),
    [h],
  );

  return (
    <Panel
      title="Earnings History"
      noBody
      right={
        <>
          {h?.basis && h.basis !== 'VERIFIED' && (
            <span className="badge amber" title={h.basis_detail}>
              {h.basis === 'TEST_DATA' ? 'SEED DATA' : 'NO DATA'}
            </span>
          )}
          <span className="legend-row">
            <span className="legend-item"><i className="legend-swatch" style={{ background: '#4a5568' }} />Estimate</span>
            <span className="legend-item"><i className="legend-swatch" style={{ background: '#21d07a' }} />Actual</span>
          </span>
        </>
      }
    >
      {ok && chartable ? (
        <>
          <div style={{ padding: '8px 6px 0' }}>
            <ResponsiveContainer width="100%" height={146}>
              <BarChart data={chartData} margin={{ top: 12, right: 6, bottom: 0, left: -18 }} barGap={2}>
                <CartesianGrid stroke="#182236" strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#5b6580', fontSize: 9 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#5b6580', fontSize: 8.5 }} axisLine={false} tickLine={false} domain={[0, 'dataMax + 0.25']} />
                <Tooltip
                  cursor={{ fill: 'rgba(59,130,246,.08)' }}
                  contentStyle={{
                    background: '#0b1322', border: '1px solid #232f49',
                    borderRadius: 6, fontSize: 11,
                  }}
                />
                <Bar dataKey="Estimate" fill="#4a5568" radius={[2, 2, 0, 0]} maxBarSize={14} isAnimationActive={false}>
                  <LabelList dataKey="Estimate" position="top" fill="#8892a8" fontSize={7.5} formatter={twoDp} />
                </Bar>
                <Bar dataKey="Actual" radius={[2, 2, 0, 0]} maxBarSize={14} isAnimationActive={false}>
                  {chartData.map((q, i) => (
                    <Cell key={i} fill={(q.Actual ?? 0) >= (q.Estimate ?? 0) ? '#21d07a' : '#f2465a'} />
                  ))}
                  <LabelList dataKey="Actual" position="top" fill="#e6ebf5" fontSize={7.5} formatter={twoDp} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="stat-row">
            <div className="stat-cell">
              <div className="st-label">Beat Rate</div>
              <div className={`st-value ${verified ? 'pos' : ''}`}>
                {h.beat_rate == null ? DASH : `${h.beat_rate}%`}
              </div>
            </div>
            <div className="stat-cell">
              <div className="st-label">Avg Surprise</div>
              <div className={`st-value ${tone(h.average_surprise)}`}>
                {signedPct(h.average_surprise, 1)}
              </div>
            </div>
            <div className="stat-cell">
              <div className="st-label">Avg Move</div>
              <div className="st-value">{plusMinus(h.average_move)}</div>
            </div>
            <div className="stat-cell">
              <div className="st-label">Quarters</div>
              <div className="st-value">{h.sample_size || DASH}</div>
            </div>
          </div>
          {!verified && h?.basis_detail && (
            <div className="hint">{h.basis_detail}</div>
          )}
        </>
      ) : (
        <Unavailable
          status={h?.status || 'DATA_UNAVAILABLE'}
          detail={h?.basis_detail}
          required={
            h?.provider && !h.provider.configured
              ? `${h.provider.name} (set ${h.provider.env_var} in backend/.env)`
              : undefined
          }
        />
      )}
    </Panel>
  );
}

/* --------------------------------------------------- bullish / risk lists */

function ReasonsPanel({
  data, kind, demo,
}: { data: EarningsOverview; kind: 'bull' | 'risk'; demo?: boolean }) {
  const list: string[] =
    (kind === 'bull'
      ? data.analysis?.bullish_reasons
      : data.analysis?.bearish_reasons) || [];

  // The reference marks the last risks with a red dot rather than an amber
  // triangle, separating structural risks from ordinary cautions.
  const severeFrom = demo ? list.length - DEMO_SEVERE_RISKS : list.length;

  return (
    <Panel title={kind === 'bull' ? 'Why Bullish?' : 'Key Risks'} noBody>
      {list.length ? (
        <div className="bullets">
          {list.slice(0, 8).map((reason, i) => (
            <div className="bullet" key={i}>
              {kind === 'bull' ? (
                <CheckCircle2 size={13} color="var(--green)" />
              ) : i >= severeFrom ? (
                <AlertCircle size={13} color="var(--red)" />
              ) : (
                <AlertTriangle size={13} color="var(--amber)" />
              )}
              <span>{reason}</span>
            </div>
          ))}
        </div>
      ) : (
        <StateBlock status="INSUFFICIENT_DATA" />
      )}
    </Panel>
  );
}

/* -------------------------------------------------------- options / vol */

function VolatilityPanel({ data, search }: { data: EarningsOverview; search: string }) {
  const o = data.options || {};
  const ok = o.status === 'OK';

  const rows: [string, string, string?][] = ok
    ? [
        ['Implied Volatility', pct(o.implied_volatility, 1)],
        ['IV Percentile (1Y)', pct(o.iv_percentile, 0)],
        ['IV Rank (1Y)', pct(o.iv_rank, 0)],
        ['ATM Call', money(o.atm_call_price)],
        ['ATM Put', money(o.atm_put_price)],
        ['Straddle', money(o.atm_straddle)],
        ['Expected Move', plusMinus(o.expected_move_percent)],
        ['Put/Call Ratio', num(o.put_call_oi), o.put_call_oi == null ? '' : o.put_call_oi < 1 ? 'pos' : 'neg'],
        ['Call OI vs Put OI', `${num(o.call_oi_vs_put_oi, 1)}x`],
      ]
    : [];

  return (
    <Panel title="Options & Volatility" noBody>
      {ok ? (
        <>
          <div className="kv">
            {rows.map(([k, v, cls]) => (
              <div className="kv-row" key={k}>
                <span className="k">{k}</span>
                <span className={`v ${cls || ''}`}>{v}</span>
              </div>
            ))}
          </div>
          <NavLink className="kv-link" to={`/options-chain/${data.symbol}${search}`}
            title={`${o.expiry_label} expiry`}>
            Options Chain
            <ChevronRight size={12} />
          </NavLink>
        </>
      ) : (
        <StateBlock status={o.status} error={o.error} />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------ earnings snapshot */

/**
 * The reference keeps the snapshot figures and the expected-move readout in a
 * single panel, so they are rendered together rather than as two cards.
 */
function SnapshotPanel({ data }: { data: EarningsOverview }) {
  const e = data.earnings || {};
  const o = data.options || {};
  const spot = data.quote.price;
  const em = o.expected_move_percent;
  const lower = o.expected_range?.lower;
  const upper = o.expected_range?.upper;

  const markPct =
    typeof spot === 'number' && typeof lower === 'number' && typeof upper === 'number' && upper > lower
      ? ((spot - lower) / (upper - lower)) * 100
      : 50;

  return (
    <Panel title="Earnings Snapshot" icon={<Info size={12} />} noBody>
      {e.status === 'OK' ? (
        <div className="snap-grid">
          <div className="snap-cell">
            <div className="sc-label">EPS (Est.)</div>
            <div className="sc-value">{e.eps_estimate ? money(e.eps_estimate) : DASH}</div>
          </div>
          <div className="snap-cell">
            <div className="sc-label">Revenue (Est.)</div>
            <div className="sc-value">
              {e.revenue_estimate ? compactMoney(e.revenue_estimate, 1) : DASH}
            </div>
          </div>
          <div className="snap-cell">
            <div className="sc-label">Report Date</div>
            <div className="sc-value" style={{ fontSize: 13 }}>{e.date_label || DASH}</div>
            <div className="sc-sub">{e.timing_label}</div>
          </div>
        </div>
      ) : (
        <StateBlock status={e.status} detail={e.detail} compact />
      )}

      <div className="em-block">
        <div className="em-label">Expected Move</div>
        {o.status === 'OK' && em != null ? (
          <>
            <div className="em-value">{plusMinus(em)}</div>
            <div className="em-bar">
              <div className="em-mark" style={{ left: `${Math.max(0, Math.min(100, markPct))}%` }} />
            </div>
            <div className="em-scale">
              <span>{money(lower)}</span>
              <span className="mid">
                <span>Current Price</span>
                <b>{money(spot)}</b>
              </span>
              <span>{money(upper)}</span>
            </div>
          </>
        ) : (
          <StateBlock status={o.status} error={o.error} compact />
        )}
      </div>
    </Panel>
  );
}

/* --------------------------------------------------------- analyst table */

/** One period's estimate row, as either provider supplies it. */
interface EstimateRow {
  label: string;
  available?: boolean;
  eps_estimate: number | null;
  revenue_estimate_b?: number | null;
  days_ago?: number;
}

/** Recharts hands labels through as RenderableText, not as a number. */
function twoDp(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(2) : '';
}

function EstimatesPanel({ data }: { data: EarningsOverview }) {
  // Alpha Vantage takes over once configured; otherwise the seed table shows,
  // still tagged, so the panel never implies a live revision trend.
  const rev: any = (data as any).revisions;
  const useProvider = rev && rev.rows?.length > 0;
  const est: any = useProvider ? rev : data.estimates;
  const ok = est && est.rows.length > 0;
  const testOnly = !useProvider && data.estimates?.status === 'TEST_DATA';
  const partial = useProvider && rev.status === 'PARTIAL_DATA';

  return (
    <Panel
      title="Analyst Estimates"
      icon={<Info size={12} />}
      noBody
      right={
        testOnly ? <span className="badge amber">SEED DATA</span>
          : partial ? <span className="badge amber">PARTIAL</span>
            : rev && !useProvider
              ? <StatusChip status={rev.status} title={rev.detail} />
              : undefined
      }
    >
      {ok ? (
        <>
          <table className="tbl">
            <thead>
              <tr>
                <th>Period</th>
                <th className="r">EPS Estimate</th>
                <th className="r">Revenue (B)</th>
              </tr>
            </thead>
            <tbody>
              {(est.rows as EstimateRow[]).map((r) => (
                <tr key={r.label} className={r.available === false ? 'row-missing' : ''}>
                  <td>{r.label}</td>
                  <td className="num r">{num(r.eps_estimate)}</td>
                  <td className="num r">{num(r.revenue_estimate_b, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(est.eps_change_90d ?? est.eps_change) != null && (
            <div className="tbl-foot">
              <TrendingUp size={12} />
              EPS estimates {signedPct(est.eps_change_90d ?? est.eps_change, 1)}
              {' over last '}{est.eps_change_window ?? 90} days
            </div>
          )}
          {partial && <div className="hint">{rev.detail}</div>}
          {!useProvider && rev?.status === 'PROVIDER_NOT_CONFIGURED' && (
            <div className="hint">{rev.detail}</div>
          )}
          {testOnly && (
            <div className="hint">
              Only seed rows are on record ({est.distinct_snapshots} distinct snapshot
              {est.distinct_snapshots === 1 ? '' : 's'}). Connect an estimates feed for a real revision trend.
            </div>
          )}
        </>
      ) : (
        <StateBlock status={est?.status} detail={est?.detail} />
      )}
    </Panel>
  );
}

/* -------------------------------------------------------------- AI blocks */

function AISummaryPanel({ data, demo }: { data: EarningsOverview; demo?: boolean }) {
  const a = data.analysis || {};
  const final = data.score?.final || {};
  const o = data.options || {};
  const h = data.history || {};

  const parts: string[] = [];
  const decision = String(final.decision || 'WAIT').replace(/_/g, ' ');
  parts.push(
    `${data.symbol} scores ${final.direction_score ?? DASH}/100 (${decision}) with ${final.confidence_label || 'gated'} confidence.`,
  );
  if (o.status === 'OK' && o.expected_move_percent != null) {
    parts.push(
      `Options price a ±${o.expected_move_percent}% move by ${o.expiry_label}, with implied volatility at the ${o.iv_percentile ?? DASH}th percentile of the past year.`,
    );
  }
  if (h.status === 'OK' && h.beat_rate != null) {
    parts.push(
      `The company has beaten EPS estimates in ${h.beat_rate}% of ${h.sample_size} recorded quarters, averaging ${signedPct(h.average_surprise, 1)} surprise.`,
    );
  }
  if (Array.isArray(final.missing_components) && final.missing_components.length) {
    parts.push(
      `Not scored for lack of verified data: ${final.missing_components.join(', ')}.`,
    );
  }

  return (
    <Panel title="AI Summary" icon={<Sparkles size={12} />} noBody>
      <div className="ai-summary">
        <div className="ai-icon"><Sparkles size={15} /></div>
        <div className="ai-text">{demo ? DEMO_AI_SUMMARY : parts.join(' ')}</div>
      </div>
    </Panel>
  );
}

function ActionPlanPanel({
  data, demo, onOpenPlan,
}: { data: EarningsOverview; demo?: boolean; onOpenPlan: () => void }) {
  const final = data.score?.final || {};
  const o = data.options || {};
  const rz = data.risk_zones || {};
  const decision = String(final.decision || 'WAIT').replace(/_/g, ' ');

  return (
    <Panel title="US-Stock Reader Action Plan" icon={<Target size={12} />} noBody className="action-panel">
      <div className="action-body">
        <div className="action-icon"><Target size={15} /></div>
        <div className="action-line">
          <span>Bias: <b>{demo ? DEMO_ACTION_PLAN.bias : decision}</b></span>
          <span className="sep">|</span>
          <span>
            Entry: <b>
              {demo
                ? DEMO_ACTION_PLAN.entry
                : final.confidence_label === 'HIGH'
                  ? 'Confirmed setup'
                  : 'Wait for confirmation'}
            </b>
          </span>
          <span className="sep">|</span>
          <span>Target: <b>
            {demo ? DEMO_ACTION_PLAN.target
              : rz.call_wall ? money(rz.call_wall, 0) : money(o.expected_range?.upper)}
          </b></span>
          <span className="sep">|</span>
          <span>Stop: <b>
            {demo ? DEMO_ACTION_PLAN.stop
              : rz.put_wall ? money(rz.put_wall, 0) : money(o.expected_range?.lower)}
          </b></span>
        </div>
        <button className="btn-plan" onClick={onOpenPlan}>
          View Trade Plan
          <ArrowRight size={13} />
        </button>
      </div>
    </Panel>
  );
}
