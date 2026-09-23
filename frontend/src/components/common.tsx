import React from 'react';
import { AlertTriangle, Loader2, PlugZap, WifiOff } from 'lucide-react';
import type { ProviderStatus } from '../api/client';

/* ------------------------------------------------------------------ panel */

export function Panel({
  title,
  icon,
  right,
  children,
  className = '',
  bodyClass = '',
  noBody = false,
}: {
  title?: React.ReactNode;
  icon?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClass?: string;
  noBody?: boolean;
}) {
  return (
    <section className={`panel ${className}`}>
      {title !== undefined && (
        <header className="panel-head">
          <div className="panel-title">
            {icon}
            {title}
          </div>
          {right && <div className="right">{right}</div>}
        </header>
      )}
      {noBody ? children : <div className={`panel-body ${bodyClass}`}>{children}</div>}
    </section>
  );
}

/* ------------------------------------------------------- status / loading */

const STATUS_COPY: Record<string, { title: string; detail: string }> = {
  IBKR_UNAVAILABLE: {
    title: 'IBKR not connected',
    detail: 'Start TWS or IB Gateway and enable API connections on port 7496.',
  },
  NO_DATA: { title: 'No data', detail: 'The provider returned nothing for this request.' },
  NO_TRADES: { title: 'No prints yet', detail: 'No qualifying option trades on the tape.' },
  NO_VOLUME: { title: 'No volume', detail: 'No option volume or open interest reported.' },
  NO_CHAIN: { title: 'No option chain', detail: 'This symbol has no listed options.' },
  NO_PRICE: { title: 'No price', detail: 'No quote is available for this symbol.' },
  UNKNOWN_SYMBOL: { title: 'Unknown symbol', detail: 'IBKR could not resolve this ticker.' },
  INSUFFICIENT_DATA: { title: 'Not enough history', detail: 'Too few observations to compute this.' },
  NOT_TRACKED: {
    title: 'Not covered',
    // "not in the database yet" read like a gap the operator could fill.
    // It is normally plan coverage, so say that instead.
    detail: 'The configured calendar provider does not cover this symbol.',
  },
  NO_SCHEDULED_EVENT: { title: 'No scheduled earnings', detail: 'No upcoming event on record.' },
  TEST_DATA: { title: 'Test data only', detail: 'The only rows on record are seed data, not a real feed.' },
};

export function StateBlock({
  status,
  error,
  loading,
  compact,
  detail,
}: {
  status?: ProviderStatus;
  error?: string | null;
  loading?: boolean;
  compact?: boolean;
  /**
   * Provider-supplied explanation. Payloads carry a specific reason (which
   * provider, and why), and showing the generic line instead threw that away.
   */
  detail?: string | null;
}) {
  if (loading) {
    return (
      <div className="state" style={compact ? { minHeight: 60, padding: 14 } : undefined}>
        <Loader2 size={17} className="spin" />
        <span>Loading live data…</span>
      </div>
    );
  }

  // "Backend unreachable" only when the request itself failed. A payload that
  // arrived carrying a provider status means the backend answered fine and it
  // is the upstream feed that is down -- saying otherwise sent people looking
  // for a dead API server when TWS was simply closed.
  if (error && !status) {
    return (
      <div className="state" style={compact ? { minHeight: 60, padding: 14 } : undefined}>
        <WifiOff size={17} />
        <span className="state-title">Backend unreachable</span>
        <span>{error}</span>
      </div>
    );
  }

  const base = STATUS_COPY[status || ''] || {
    title: status || 'Unavailable',
    detail: 'This panel has no verified data to show.',
  };
  // A provider-supplied reason always beats the generic line.
  const copy = (detail || error) ? { ...base, detail: detail || error! } : base;

  return (
    <div className="state" style={compact ? { minHeight: 60, padding: 14 } : undefined}>
      {status === 'IBKR_UNAVAILABLE' ? <PlugZap size={17} /> : <AlertTriangle size={17} />}
      <span className="state-title">{copy.title}</span>
      <span>{copy.detail}</span>
    </div>
  );
}

/* --------------------------------------------------------------- sparkline */

export function Sparkline({
  points,
  color,
  width = 54,
  height = 20,
}: {
  points: number[];
  color: string;
  width?: number;
  height?: number;
}) {
  if (!points || points.length < 2) return <svg width={width} height={height} />;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);

  const d = points
    .map((p, i) => {
      const x = i * step;
      const y = height - ((p - min) / span) * (height - 2) - 1;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.3} strokeLinejoin="round" />
    </svg>
  );
}

/* ------------------------------------------------------------ score gauge */

export function ScoreGauge({
  value,
  max = 100,
  size = 122,
  color,
}: {
  value: number | null;
  max?: number;
  size?: number;
  color: string;
}) {
  const stroke = 9;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const ratio = value === null ? 0 : Math.max(0, Math.min(1, value / max));

  return (
    <svg width={size} height={size} style={{ display: 'block', transform: 'rotate(-90deg)' }}>
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none" stroke="var(--panel-3)" strokeWidth={stroke}
      />
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${c * ratio} ${c}`}
        style={{ transition: 'stroke-dasharray .5s ease' }}
      />
    </svg>
  );
}

/* -------------------------------------------------------- sentiment gauge */

/** 180-degree arc with a needle, coloured red -> amber -> green. */
export function SentimentGauge({
  score,
  size = 118,
}: {
  score: number | null;
  size?: number;
}) {
  const w = size;
  const h = size / 2 + 12;
  const cx = w / 2;
  const cy = size / 2;
  const r = size / 2 - 9;
  const stroke = 8;

  const arc = (from: number, to: number) => {
    const p = (deg: number) => {
      const rad = (Math.PI * (180 - deg)) / 180;
      return [cx + r * Math.cos(rad), cy - r * Math.sin(rad)];
    };
    const [x1, y1] = p(from);
    const [x2, y2] = p(to);
    return `M${x1},${y1} A${r},${r} 0 0 1 ${x2},${y2}`;
  };

  const angle = score === null ? 90 : (Math.max(0, Math.min(100, score)) / 100) * 180;
  const nRad = (Math.PI * (180 - angle)) / 180;
  const nx = cx + (r - 12) * Math.cos(nRad);
  const ny = cy - (r - 12) * Math.sin(nRad);

  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <path d={arc(0, 40)} fill="none" stroke="var(--red)" strokeWidth={stroke} strokeLinecap="round" />
      <path d={arc(40, 60)} fill="none" stroke="var(--amber)" strokeWidth={stroke} />
      <path d={arc(60, 100)} fill="none" stroke="#84cc16" strokeWidth={stroke} />
      <path d={arc(100, 180)} fill="none" stroke="var(--green)" strokeWidth={stroke} strokeLinecap="round" />
      {score !== null && (
        <>
          <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="#fff" strokeWidth={2} strokeLinecap="round" />
          <circle cx={cx} cy={cy} r={4} fill="#fff" />
        </>
      )}
    </svg>
  );
}

/* ---------------------------------------------------------------- donut */

export function Donut({
  segments,
  size = 118,
  thickness = 20,
  centerTop,
  centerBottom,
}: {
  segments: { label: string; value: number; color: string }[];
  size?: number;
  thickness?: number;
  centerTop?: string;
  centerBottom?: string;
}) {
  const total = segments.reduce((a, s) => a + s.value, 0);
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;

  let offset = 0;

  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--panel-3)" strokeWidth={thickness} />
        {total > 0 &&
          segments.map((s) => {
            const len = (s.value / total) * c;
            const el = (
              <circle
                key={s.label}
                cx={size / 2} cy={size / 2} r={r}
                fill="none" stroke={s.color} strokeWidth={thickness}
                strokeDasharray={`${Math.max(len - 1.5, 0)} ${c}`}
                strokeDashoffset={-offset}
              />
            );
            offset += len;
            return el;
          })}
      </svg>
      <div
        style={{
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
        }}
      >
        <div style={{ fontSize: 17, fontWeight: 800, letterSpacing: '-.5px' }}>{centerTop}</div>
        <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>{centerBottom}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------- mini ring (tile) */

export function MiniRing({
  percent,
  color,
  size = 42,
}: {
  percent: number | null;
  color: string;
  size?: number;
}) {
  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const ratio = percent === null ? 0 : Math.max(0, Math.min(1, percent / 100));

  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--panel-3)" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r}
          fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${c * ratio} ${c}`}
        />
      </svg>
      <div
        style={{
          position: 'absolute', inset: 0, display: 'grid', placeItems: 'center',
          fontSize: 9.5, fontWeight: 700,
        }}
      >
        {percent === null ? '--' : `${Math.round(percent)}%`}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- logo mark */

const BRAND: Record<string, string> = {
  NVDA: '#76b900', ORCL: '#c74634', ADBE: '#ed2224', GME: '#e11b22',
  AVGO: '#cc092f', AMZN: '#ff9900', AAPL: '#9aa0a6', TSLA: '#e31937',
  META: '#0668e1', AMD: '#ed1c24', MSFT: '#00a4ef', GOOGL: '#4285f4',
  NFLX: '#e50914', INTC: '#0068b5', CRM: '#00a1e0', QCOM: '#3253dc',
};

export function LogoMark({ symbol, className }: { symbol: string; className?: string }) {
  const color = BRAND[symbol] || '#334165';
  return (
    <div className={className} style={{ background: color }}>
      {symbol.slice(0, 2)}
    </div>
  );
}

export function ratingColor(rating: string | null | undefined): string {
  switch (rating) {
    case 'Strong Buy': return 'var(--green)';
    case 'Buy': return 'var(--green)';
    case 'Wait': return 'var(--amber)';
    case 'Sell': return 'var(--red)';
    case 'Strong Sell': return 'var(--red)';
    default: return 'var(--text-dim)';
  }
}
