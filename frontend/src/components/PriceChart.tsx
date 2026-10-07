import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  createChart, ColorType, CrosshairMode, LineStyle,
  type IChartApi, type ISeriesApi, type Time,
} from 'lightweight-charts';
import type { ChartBar, ChartLevels } from '../api/client';
import { money, num } from '../lib/format';

/**
 * A real trading chart, built on TradingView's lightweight-charts engine.
 *
 * Candles, a volume pane, the three EMAs, and support/resistance price lines,
 * with a crosshair that follows the cursor and scroll-to-zoom / drag-to-pan --
 * the same feel as a trading terminal. The props are unchanged from the old
 * recharts version, so every caller is a drop-in.
 */

const EMA20 = '#3a63f0';
const EMA50 = '#f0a92b';
const EMA200 = '#a855f7';

/** Read a CSS variable off :root, with a fallback, so the chart follows theme. */
function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function isDark(): boolean {
  if (typeof document === 'undefined') return false;
  const attr = document.documentElement.getAttribute('data-theme');
  if (attr === 'dark') return true;
  if (attr === 'light') return false;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
}

/** ChartBar.t is 'YYYY-MM-DD' on daily ranges and a datetime on intraday. */
function toTime(t: string): Time {
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return t as unknown as Time;
  const ms = new Date(t).getTime();
  return Math.floor((Number.isNaN(ms) ? Date.now() : ms) / 1000) as Time;
}

type Legend = {
  label: string; open: number; high: number; low: number; close: number;
  volume: number; up: boolean;
} | null;

export default function PriceChart({
  bars,
  lastPrice,
  levels,
  height = 320,
}: {
  bars: ChartBar[];
  lastPrice: number | null;
  levels?: ChartLevels | null;
  height?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const [legend, setLegend] = useState<Legend>(null);
  const [dark, setDark] = useState(isDark());

  const intraday = useMemo(
    () => bars.length > 0 && !/^\d{4}-\d{2}-\d{2}$/.test(bars[0].t),
    [bars],
  );

  // Rebuild colours when the theme flips.
  useEffect(() => {
    const obs = new MutationObserver(() => setDark(isDark()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    const onMq = () => setDark(isDark());
    mq?.addEventListener?.('change', onMq);
    return () => { obs.disconnect(); mq?.removeEventListener?.('change', onMq); };
  }, []);

  // Create the chart once.
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;

    const text = cssVar('--text-mute', dark ? '#8892a8' : '#5b6580');
    const grid = cssVar('--border-2', dark ? '#1b2438' : '#eef1f6');
    const up = cssVar('--green', '#21d07a');
    const down = cssVar('--red', '#f2465a');

    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: text,
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 11,
        // Hide the TradingView attribution logo (allowed: the library is
        // open-source/Apache-2.0, not the paid widget).
        attributionLogo: false,
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid, scaleMargins: { top: 0.08, bottom: 0.28 } },
      timeScale: {
        borderColor: grid,
        timeVisible: intraday,
        secondsVisible: false,
        rightOffset: 4,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: text, width: 1, style: LineStyle.Dashed, labelBackgroundColor: down },
        horzLine: { color: text, width: 1, style: LineStyle.Dashed, labelBackgroundColor: down },
      },
      handleScroll: true,
      handleScale: true,
    });
    chartRef.current = chart;

    const candles = chart.addCandlestickSeries({
      upColor: up, downColor: down, borderVisible: false,
      wickUpColor: up, wickDownColor: down,
    });
    candleRef.current = candles;

    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
    });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    const ema20 = chart.addLineSeries({ color: EMA20, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const ema50 = chart.addLineSeries({ color: EMA50, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const ema200 = chart.addLineSeries({ color: EMA200, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });

    // Stash series on the chart instance for the data effect.
    (chart as any).__series = { candles, volume, ema20, ema50, ema200 };

    // Crosshair legend.
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point) { setLegend(null); return; }
      const c = param.seriesData.get(candles) as any;
      const v = param.seriesData.get(volume) as any;
      if (!c) { setLegend(null); return; }
      setLegend({
        label: String(param.time), open: c.open, high: c.high, low: c.low,
        close: c.close, volume: v?.value ?? 0, up: c.close >= c.open,
      });
    });

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth });
    });
    ro.observe(el);
    chart.applyOptions({ width: el.clientWidth });

    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; candleRef.current = null; };
    // Recreate on theme flip and intraday/daily switch so axis + colours match.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dark, intraday, height]);

  // Feed data whenever the bars change.
  useEffect(() => {
    const chart = chartRef.current;
    const s = (chart as any)?.__series;
    if (!chart || !s || !bars.length) return;

    const up = cssVar('--green', '#21d07a');
    const down = cssVar('--red', '#f2465a');

    const seen = new Set<string | number>();
    const candles: any[] = [];
    const vols: any[] = [];
    const e20: any[] = []; const e50: any[] = []; const e200: any[] = [];
    for (const b of bars) {
      const time = toTime(b.t);
      const key = String(time);
      if (seen.has(key)) continue; // lightweight-charts requires unique, sorted times
      seen.add(key);
      candles.push({ time, open: b.open, high: b.high, low: b.low, close: b.close });
      vols.push({ time, value: b.volume || 0, color: (b.close >= b.open ? up : down) + '55' });
      if (b.ema20 != null) e20.push({ time, value: b.ema20 });
      if (b.ema50 != null) e50.push({ time, value: b.ema50 });
      if (b.ema200 != null) e200.push({ time, value: b.ema200 });
    }
    s.candles.setData(candles);
    s.volume.setData(vols);
    s.ema20.setData(e20);
    s.ema50.setData(e50);
    s.ema200.setData(e200);

    // Support / resistance as horizontal price lines on the candle series.
    const existing: any[] = (s.candles as any).__lines || [];
    existing.forEach((l: any) => s.candles.removePriceLine(l));
    const lines: any[] = [];
    const addLine = (price: number, color: string, title: string) => {
      lines.push(s.candles.createPriceLine({
        price, color, lineWidth: 1, lineStyle: LineStyle.Dashed,
        axisLabelVisible: true, title,
      }));
    };
    (levels?.support || []).slice(0, 2).forEach((lv, i) =>
      addLine(lv.price, up, i === 0 ? 'S' : 'S2'));
    (levels?.resistance || []).slice(0, 2).forEach((lv, i) =>
      addLine(lv.price, down, i === 0 ? 'R' : 'R2'));
    (s.candles as any).__lines = lines;

    chart.timeScale().fitContent();
  }, [bars, levels]);

  const last = bars.length ? bars[bars.length - 1] : null;
  const shown = legend || (last && {
    label: last.label, open: last.open, high: last.high, low: last.low,
    close: last.close, volume: last.volume, up: last.close >= last.open,
  });

  return (
    <div className="tv-wrap">
      {shown && (
        <div className="tv-legend">
          <span className="tv-when">{shown.label}</span>
          <span>O <b>{money(shown.open)}</b></span>
          <span>H <b>{money(shown.high)}</b></span>
          <span>L <b>{money(shown.low)}</b></span>
          <span>C <b className={shown.up ? 'tv-up' : 'tv-dn'}>{money(shown.close)}</b></span>
          <span className="tv-vol">Vol <b>{num(shown.volume, 0)}</b></span>
          <span className="tv-ema"><i style={{ background: EMA20 }} />EMA20</span>
          <span className="tv-ema"><i style={{ background: EMA50 }} />EMA50</span>
          <span className="tv-ema"><i style={{ background: EMA200 }} />EMA200</span>
        </div>
      )}
      <div ref={wrapRef} className="tv-chart" style={{ height }} />
    </div>
  );
}
