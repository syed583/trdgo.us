import React, { useMemo } from 'react';
import {
  Bar, BarChart, CartesianGrid, ComposedChart, Line, ReferenceArea,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { ChartBar, ChartLevels } from '../api/client';
import { compact, money, num } from '../lib/format';

const GREEN = '#21d07a';
const RED = '#f2465a';
const EMA20 = '#3b82f6';
const EMA50 = '#f5a524';
const EMA200 = '#a78bfa';
// Distinct from the bright green last-price marker on purpose: two green
// dashed lines on one chart is one line too many to tell apart at a glance.
const SUPPORT = '#2dd4bf';
const RESIST = '#fb7185';

// Grid, axis text and the axis rule were hardcoded dark hexes, which is a
// dark grid drawn on a white panel in light mode. They come from the theme.
const GRID = 'var(--border)';
const AXIS_TEXT = 'var(--text-mute)';

// The right margin is the price axis gutter. TradingView puts a value tag for
// every plotted series there, so it has to be wide enough for one.
const AXIS_W = 52;
const MARGIN = { top: 16, right: AXIS_W + 4, bottom: 0, left: 16 };

// Tag geometry, and the closest two tags may sit before one is nudged clear.
const TAG_H = 12;
const TAG_MIN_GAP = 12.5;
// How far a tag may be nudged from its own price before it is dropped
// instead. Half a row: beyond that it starts naming the wrong gridline.
const MAX_DRIFT = 6;

/**
 * Candle body + wick drawn inside the space a Bar reserves for the [low, high]
 * range. Recharts has no candlestick series, but giving a Bar an array value
 * hands the shape the pixel box for that range, which is all a candle needs.
 */
function Candle(props: any) {
  const { x, y, width, height, payload } = props;
  const { open, close, high, low } = payload as ChartBar;

  const span = high - low;
  const up = close >= open;
  const color = up ? GREEN : RED;

  const bodyW = Math.max(1, Math.min(width * 0.66, 11));
  const cx = x + width / 2;

  if (span <= 0) {
    return <rect x={cx - bodyW / 2} y={y} width={bodyW} height={1} fill={color} />;
  }

  const toY = (v: number) => y + ((high - v) / span) * height;
  const top = toY(Math.max(open, close));
  const bottom = toY(Math.min(open, close));
  const bodyH = Math.max(1, bottom - top);

  return (
    <g>
      <line x1={cx} y1={y} x2={cx} y2={y + height} stroke={color} strokeWidth={1} />
      <rect x={cx - bodyW / 2} y={top} width={bodyW} height={bodyH} fill={color} />
    </g>
  );
}

function ChartTip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const b: ChartBar = payload[0].payload;
  const up = b.close >= b.open;

  return (
    <div className="flow-tip ft-compact">
      <div className="ft-head">{b.label}</div>
      <div className="ft-row"><span>Open</span><b>{num(b.open)}</b></div>
      <div className="ft-row"><span>High</span><b>{num(b.high)}</b></div>
      <div className="ft-row"><span>Low</span><b>{num(b.low)}</b></div>
      <div className="ft-row">
        <span>Close</span>
        <b style={{ color: up ? GREEN : RED }}>{num(b.close)}</b>
      </div>
      {b.volume > 0 && (
        <div className="ft-row"><span>Volume</span><b>{compact(b.volume)}</b></div>
      )}
      {b.ema20 !== null && (
        <div className="ft-row"><span style={{ color: EMA20 }}>EMA 20</span><b>{num(b.ema20)}</b></div>
      )}
      {b.ema50 !== null && (
        <div className="ft-row"><span style={{ color: EMA50 }}>EMA 50</span><b>{num(b.ema50)}</b></div>
      )}
      {b.ema200 !== null && (
        <div className="ft-row"><span style={{ color: EMA200 }}>EMA 200</span><b>{num(b.ema200)}</b></div>
      )}
    </div>
  );
}

/**
 * One value tag on the price axis, the way a trading terminal draws them.
 *
 * Every plotted series -- each EMA, each level, the last price -- gets a
 * filled pill on the axis at its own value. Reading "where is the 200 EMA
 * right now" then costs a glance at the axis instead of tracing a line to the
 * edge and guessing between two gridlines.
 */
interface Tag {
  key: string;
  value: number;
  fill: string;
  text: string;
  /** Lower sorts first when two tags collide and one has to give way. */
  rank: number;
}

/**
 * Place tags at their true price, then push apart the ones that overlap.
 *
 * Without this, an EMA at 214.71 and a resistance at 213.72 print on top of
 * each other on a 180px chart and neither is readable. Tags are nudged in
 * rank order -- the live price never moves, because it is the one value on
 * the axis that must stay exactly where the price is.
 */
function spread(tags: Tag[], yFor: (v: number) => number, top: number,
                bottom: number): (Tag & { y: number; want: number })[] {
  const placed = tags
    .map((t) => ({ ...t, y: yFor(t.value), want: yFor(t.value) }))
    .filter((t) => Number.isFinite(t.y) && t.y >= top - 2 && t.y <= bottom + 2)
    .sort((a, b) => a.y - b.y);
  if (!placed.length) return placed;

  for (let i = 1; i < placed.length; i += 1) {
    const gap = placed[i].y - placed[i - 1].y;
    if (gap < TAG_MIN_GAP) placed[i].y = placed[i - 1].y + TAG_MIN_GAP;
  }
  // Share the error rather than pushing the whole cluster one way.
  const drift = placed.reduce((sum, t) => sum + (t.y - t.want), 0) / placed.length;
  placed.forEach((t) => { t.y -= drift; });

  if (placed[0].y < top) {
    const push = top - placed[0].y;
    placed.forEach((t) => { t.y += push; });
  }
  const lastTag = placed[placed.length - 1];
  if (lastTag.y > bottom) {
    const pull = lastTag.y - bottom;
    placed.forEach((t) => { t.y -= pull; });
  }
  return placed;
}

/**
 * Place tags, dropping the least important ones rather than lying about
 * where a line is.
 *
 * Values genuinely bunch: an EMA at 214.70, a resistance at 213.72 and the
 * last price at 212.15 cannot all have their own 12px row inside the 19px of
 * axis they actually occupy. Nudging them apart puts a tag a dozen pixels
 * from the line it names, which on a price axis is simply a wrong number in
 * the wrong place.
 *
 * So when a tag cannot sit within MAX_DRIFT of its own price, the lowest
 * priority tag in the crowd is dropped and the rest are placed again. The
 * live price and the levels keep their tags; an EMA gives way, and its value
 * is still on the legend at the top left where nothing is competing for the
 * space.
 */
function placeTags(tags: Tag[], yFor: (v: number) => number, top: number,
                   bottom: number): (Tag & { y: number })[] {
  let pool = [...tags];
  for (let attempt = 0; attempt < tags.length; attempt += 1) {
    const placed = spread(pool, yFor, top, bottom);
    const worst = placed
      .filter((t) => Math.abs(t.y - t.want) > MAX_DRIFT)
      .sort((a, b) => b.rank - a.rank || Math.abs(b.y - b.want) - Math.abs(a.y - a.want))[0];

    // Nothing is badly placed, or the only badly placed tags are ones that
    // must be shown regardless -- the live price and the levels.
    if (!worst || worst.rank === 0) return placed;
    pool = pool.filter((t) => t.key !== worst.key);
  }
  return spread(pool, yFor, top, bottom);
}

export default function PriceChart({
  bars,
  lastPrice,
  levels,
  // Tall enough that the axis can carry a tag per series without the tags
  // shoving each other off their own price. At 178 the eight values on a
  // typical chart needed 98px of axis and had 74px to put them in.
  height = 268,
}: {
  bars: ChartBar[];
  lastPrice: number | null;
  /** Support and resistance as the chart payload carries them. */
  levels?: ChartLevels | null;
  height?: number;
}) {
  const data = useMemo(() => {
    // On daily/weekly ranges the reference labels the axis by month, once per
    // month, rather than repeating every bar's date.
    //
    // Whether a series is daily is decided by the bars, not by how the
    // provider spells its timestamps. The old test was `t.length <= 10`,
    // which assumed a bare "YYYY-MM-DD"; the providers actually send
    // "2026-03-17T00:00:00", so every daily chart failed the test, kept a
    // label on all ~128 bars, and printed them on top of each other.
    const days = new Set(bars.map((b) => b.t.slice(0, 10)));
    const daily = bars.length > 0 && days.size === bars.length;

    return bars.map((b, i) => {
      let xLabel = b.label;
      if (daily) {
        const month = b.t.slice(0, 7);
        const prevMonth = i > 0 ? bars[i - 1].t.slice(0, 7) : null;
        xLabel = month !== prevMonth
          ? new Date(`${b.t.slice(0, 10)}T00:00:00`)
            .toLocaleString('en-US', { month: 'short' })
          : '';
      }
      return { ...b, xLabel, range: [b.low, b.high] as [number, number] };
    });
  }, [bars]);

  const monthTicks = useMemo(
    () => data.filter((d) => d.xLabel).map((d) => d.xLabel),
    [data],
  );

  const { domain, ticks } = useMemo(() => {
    if (!bars.length) return { domain: [0, 1] as [number, number], ticks: [] };
    const lows = bars.map((b) => b.low);
    const highs = bars.map((b) => b.high);
    const emas = bars
      .flatMap((b) => [b.ema20, b.ema50, b.ema200])
      .filter((v): v is number => v !== null);
    const lo = Math.min(...lows, ...emas);
    const hi = Math.max(...highs, ...emas);
    const pad = (hi - lo) * 0.06 || 1;

    // Snap the gridlines to a round step so the axis reads 80/100/120/...
    const span = hi - lo + pad * 2;
    const raw = span / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= raw) || mag * 10;
    const bottom = Math.floor((lo - pad) / step) * step;
    const top = Math.ceil((hi + pad) / step) * step;

    const out: number[] = [];
    for (let v = bottom; v <= top + 1e-9; v += step) out.push(Number(v.toFixed(4)));

    return { domain: [bottom, top] as [number, number], ticks: out };
  }, [bars]);

  const volMax = useMemo(
    () => Math.max(...bars.map((b) => b.volume), 1),
    [bars],
  );

  // Intraday has a label on every bar, so thin them to about eight across.
  // Daily already carries a label only on the first bar of each month, and
  // those must all be drawn -- hence interval 0 on that path.
  const tickInterval = Math.max(0, Math.floor(data.length / 8) - 1);

  const last = bars.length ? bars[bars.length - 1] : null;

  // The axis gutter, in the same pixel space recharts lays the plot out in:
  // the price area starts at the top margin and runs to the bottom of the
  // box, because the x-axis is hidden on this chart and takes no height.
  const plotTop = MARGIN.top;
  const plotBottom = height;
  const yFor = (v: number) => {
    const [lo, hi] = domain;
    if (hi === lo) return plotTop;
    return plotTop + ((hi - v) / (hi - lo)) * (plotBottom - plotTop);
  };

  const axisTags = useMemo(() => {
    const out: Tag[] = [];
    if (lastPrice !== null) {
      out.push({
        key: 'last', value: lastPrice, fill: GREEN,
        text: num(lastPrice, 2), rank: 0,
      });
    }
    (levels?.resistance || []).forEach((lv) => out.push({
      key: `r${lv.price}`, value: lv.price, fill: RESIST,
      text: num(lv.price, 2), rank: 1,
    }));
    (levels?.support || []).forEach((lv) => out.push({
      key: `s${lv.price}`, value: lv.price, fill: SUPPORT,
      text: num(lv.price, 2), rank: 1,
    }));
    if (last?.ema20 != null) out.push({ key: 'e20', value: last.ema20, fill: EMA20, text: num(last.ema20, 2), rank: 2 });
    if (last?.ema50 != null) out.push({ key: 'e50', value: last.ema50, fill: EMA50, text: num(last.ema50, 2), rank: 2 });
    if (last?.ema200 != null) out.push({ key: 'e200', value: last.ema200, fill: EMA200, text: num(last.ema200, 2), rank: 2 });
    return placeTags(out, yFor, plotTop + TAG_H / 2, plotBottom - TAG_H / 2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [levels, lastPrice, last, domain, height]);

  return (
    <div style={{ width: '100%' }}>
      <div style={{ position: 'relative' }}>
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart data={data} margin={MARGIN}>
            <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="label" hide />
            <YAxis
              orientation="right"
              domain={domain}
              width={AXIS_W}
              tick={{ fill: AXIS_TEXT, fontSize: 9.5, fontFamily: 'monospace' }}
              axisLine={false}
              tickLine={false}
              ticks={ticks.length ? ticks : undefined}
              tickFormatter={(v) => num(v, 2)}
            />
            <Tooltip
              content={<ChartTip />}
              cursor={{ stroke: AXIS_TEXT, strokeWidth: 1, strokeDasharray: '3 3' }}
            />

            {/* Zones under everything. A level found by clustering pivots has
                a width -- price turned between 212.71 and 214.39, not at one
                exact number -- and drawing only the midpoint claims a
                precision the method does not have. */}
            {(levels?.support || []).map((lv) => (
              <ReferenceArea
                key={`sz-${lv.price}`}
                y1={lv.low} y2={lv.high}
                fill={SUPPORT} fillOpacity={0.16} stroke="none"
                ifOverflow="hidden"
              />
            ))}
            {(levels?.resistance || []).map((lv) => (
              <ReferenceArea
                key={`rz-${lv.price}`}
                y1={lv.low} y2={lv.high}
                fill={RESIST} fillOpacity={0.16} stroke="none"
                ifOverflow="hidden"
              />
            ))}
            {(levels?.support || []).map((lv) => (
              <ReferenceLine key={`s-${lv.price}`} y={lv.price}
                stroke={SUPPORT} strokeDasharray="6 4" strokeWidth={1} />
            ))}
            {(levels?.resistance || []).map((lv) => (
              <ReferenceLine key={`r-${lv.price}`} y={lv.price}
                stroke={RESIST} strokeDasharray="6 4" strokeWidth={1} />
            ))}

            <Bar dataKey="range" shape={<Candle />} isAnimationActive={false} barSize={999} />

            <Line dataKey="ema20" stroke={EMA20} dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
            <Line dataKey="ema50" stroke={EMA50} dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
            <Line dataKey="ema200" stroke={EMA200} dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />

            {lastPrice !== null && (
              <ReferenceLine y={lastPrice} stroke={GREEN}
                strokeDasharray="4 3" strokeWidth={1} />
            )}
          </ComposedChart>
        </ResponsiveContainer>

        {/* The axis tags, drawn over the gutter the axis numbers live in --
            which is exactly what a terminal does: the live values cover the
            static scale, because the values are what you are reading. */}
        <div className="pc-tags" style={{ width: AXIS_W }}>
          {axisTags.map((t) => (
            <span
              key={t.key}
              className="pc-tag"
              style={{ top: t.y - TAG_H / 2, background: t.fill }}
            >
              {t.text}
            </span>
          ))}
        </div>
      </div>

      <div style={{ position: 'relative' }}>
        <div
          style={{
            position: 'absolute', left: 10, top: 2, zIndex: 2,
            fontSize: 9.5, color: 'var(--text-dim)',
          }}
        >
          Volume <b style={{ color: 'var(--text)', fontFamily: 'monospace' }}>
            {compact(bars.length ? bars[bars.length - 1].volume : null)}
          </b>
        </div>
        <ResponsiveContainer width="100%" height={52}>
          <BarChart data={data} margin={{ ...MARGIN, top: 14 }}>
            <XAxis
              dataKey="xLabel"
              interval={monthTicks.length ? 0 : tickInterval}
              tick={{ fill: AXIS_TEXT, fontSize: 9.5 }}
              axisLine={{ stroke: GRID }}
              tickLine={false}
            />
            <YAxis
              orientation="right"
              domain={[0, volMax]}
              width={AXIS_W}
              tick={{ fill: AXIS_TEXT, fontSize: 9 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => compact(v, 0)}
              ticks={[volMax * 0.5, volMax]}
            />
            <Tooltip
              cursor={{ fill: 'rgba(59,130,246,.08)' }}
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const b: ChartBar = payload[0].payload;
                return (
                  <div className="flow-tip">
                    <div className="ft-head">{b.label}</div>
                    <div className="ft-row"><span>Volume</span><b>{compact(b.volume)}</b></div>
                    <div className="ft-row"><span>Close</span><b>{money(b.close)}</b></div>
                  </div>
                );
              }}
            />
            <Bar dataKey="volume" isAnimationActive={false} barSize={999} shape={(p: any) => {
              const { x, y, width, height, payload } = p;
              const up = payload.close >= payload.open;
              const w = Math.max(1, Math.min(width * 0.66, 11));
              return (
                <rect
                  x={x + width / 2 - w / 2}
                  y={y}
                  width={w}
                  height={Math.max(height, 0)}
                  fill={up ? GREEN : RED}
                  opacity={0.55}
                />
              );
            }} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
