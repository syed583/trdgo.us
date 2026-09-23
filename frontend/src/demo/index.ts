/* ===========================================================================
 * DEMO MODE - VISUAL DEVELOPMENT ONLY
 * ---------------------------------------------------------------------------
 * Demo mode is opt-in and explicit: it turns on only when the URL carries
 * ?demo=1 (or #/page/SYM?demo=1). It is never enabled automatically, never
 * used as a fallback when a provider is down, and never blended with live
 * data. While it is on, the top bar shows a "DEMO DATA" badge so a screenshot
 * of this state can never be mistaken for the live product.
 * =========================================================================== */

import type {
  EarningsOverview, IndicesPayload, OptionsOverview, SystemStatus,
  WatchlistPayload,
} from '../api/client';
import {
  DEMO_BARS, DEMO_DE_BY_STRIKE, DEMO_INDICES, DEMO_OI_BY_STRIKE,
  DEMO_QUARTERS, DEMO_SCATTER, DEMO_STATUS, DEMO_TRADES,
  DEMO_VOLUME_BY_STRIKE, DEMO_WATCHLIST,
} from './fixtureData';

export function isDemoMode(): boolean {
  if (typeof window === 'undefined') return false;
  const search = new URLSearchParams(window.location.search);
  if (search.get('demo') === '1') return true;
  // Also accept it after the hash route, e.g. #/options/NVDA?demo=1
  const hash = window.location.hash;
  const q = hash.indexOf('?');
  if (q === -1) return false;
  return new URLSearchParams(hash.slice(q + 1)).get('demo') === '1';
}

const MARKET = DEMO_STATUS.market;

const DEMO_QUOTE = {
  symbol: 'NVDA',
  name: 'NVIDIA Corporation',
  exchange: 'NASDAQ',
  tags: ['Technology', 'Semiconductors', 'Large Cap'],
  price: 142.67,
  change: 3.21,
  change_percent: 2.30,
  previous_close: 139.46,
  bid: 142.65,
  ask: 142.69,
  open: 140.21,
  high: 143.12,
  low: 139.50,
  close: 142.67,
  volume: 212_400_000,
  market: MARKET,
  status: 'OK',
  source: 'DEMO',
};

const DEMO_OPTION_METRICS = {
  symbol: 'NVDA',
  spot: 142.67,
  expiry_label: '12/13/24',
  dte: 3,
  implied_volatility: 62.4,
  iv_percentile: 78,
  iv_rank: 81,
  historical_volatility: 54.2,
  atm_call_price: 7.20,
  atm_put_price: 6.30,
  atm_call_strike: 142.5,
  atm_put_strike: 142.5,
  atm_straddle: 13.50,
  expected_move_dollars: 10.70,
  expected_move_percent: 7.5,
  expected_range: { lower: 131.97, upper: 153.37 },
  realized_move_percent: 6.8,
  realized_windows: 4,
  skew_25d: -7.2,
  put_call_volume: 0.61,
  put_call_oi: 0.68,
  call_oi_vs_put_oi: 1.8,
  status: 'OK',
  source: 'DEMO',
};

const DEMO_RISK_ZONES = {
  symbol: 'NVDA',
  spot: 142.67,
  expiry_label: '12/13/24',
  call_wall: 150,
  call_wall_oi: 880_000,
  put_wall: 135,
  put_wall_oi: 480_000,
  max_pain: 140,
  status: 'OK',
  source: 'DEMO',
};

/* --------------------------------------------------------------- earnings */

export const DEMO_EARNINGS: EarningsOverview = {
  symbol: 'NVDA',
  quote: DEMO_QUOTE as any,
  chart: {
    symbol: 'NVDA',
    range: '6M',
    bar_size: '1 day',
    bars: DEMO_BARS,
    ohlc: { open: 140.21, high: 143.12, low: 139.50, close: 142.67 },
    ema: { ema20: 138.45, ema50: 132.21, ema200: 118.36 },
    change: 3.21,
    change_percent: 2.30,
    volume: 212_400_000,
    status: 'OK',
  },
  score: {
    symbol: 'NVDA',
    final: {
      direction_score: 82,
      decision: 'STRONG BUY',
      confidence_score: 87,
      confidence_label: 'HIGH',
      missing_components: [],
      warnings: [],
      max_score: 100,
    },
    components: {
      fundamentals: { score: 16, max_score: 20, bias: 'BULLISH', status: 'OK', confidence: 84, reasons: [] },
      estimates: { score: 21, max_score: 25, bias: 'BULLISH', status: 'OK', confidence: 88, reasons: [] },
      technicals: { score: 17, max_score: 20, bias: 'BULLISH', status: 'OK', confidence: 86, reasons: [] },
      earnings_history: { score: 12, max_score: 15, bias: 'BULLISH', status: 'OK', confidence: 90, reasons: [] },
      options: { score: 12, max_score: 15, bias: 'BULLISH', status: 'OK', confidence: 80, reasons: [] },
      market_environment: { score: 4, max_score: 5, bias: 'BULLISH', status: 'OK', confidence: 78, reasons: [] },
    },
    confidence: { confidence_score: 87, confidence_label: 'HIGH' },
    risk: { risk_level: 'MEDIUM' },
    providers: {},
    status: 'OK',
  },
  options: DEMO_OPTION_METRICS,
  risk_zones: DEMO_RISK_ZONES,
  earnings: {
    date: '2024-12-11',
    date_label: 'Dec 11, 2024',
    reporting_time: 'AMC',
    timing_label: 'After Market Close',
    short_label: 'After Close',
    eps_estimate: 0.81,
    revenue_estimate: 33_100_000_000,
    status: 'OK',
    source: 'DEMO',
  },
  estimates: {
    rows: [
      { label: 'Current', days_ago: 0, eps_estimate: 0.81, revenue_estimate_b: 33.1, analyst_count: 42, snapshot_time: '2024-12-10' },
      { label: '7 Days Ago', days_ago: 7, eps_estimate: 0.78, revenue_estimate_b: 32.8, analyst_count: 41, snapshot_time: '2024-12-03' },
      { label: '30 Days Ago', days_ago: 30, eps_estimate: 0.76, revenue_estimate_b: 32.2, analyst_count: 40, snapshot_time: '2024-11-10' },
      { label: '60 Days Ago', days_ago: 60, eps_estimate: 0.73, revenue_estimate_b: 31.5, analyst_count: 38, snapshot_time: '2024-10-11' },
      { label: '90 Days Ago', days_ago: 90, eps_estimate: 0.70, revenue_estimate_b: 30.8, analyst_count: 36, snapshot_time: '2024-09-11' },
    ],
    eps_change_90d: 15.7,
    distinct_snapshots: 5,
    sources: ['DEMO'],
    status: 'OK',
  },
  history: {
    quarters: DEMO_QUARTERS as any,
    beat_rate: 87,
    average_surprise: 6.8,
    consecutive_beats: 8,
    sample_size: 8,
    status: 'OK',
  },
  analysis: {
    bullish_reasons: [
      'EPS estimates rising (+15.7% over 90 days)',
      'Strong history of beating estimates (87%)',
      'Revenue growth expected at +78% YoY',
      'Price above key EMAs (20/50/200)',
      'Strong relative strength vs. QQQ and SPY',
      'AI demand and data center growth',
      'Options flow shows bullish positioning',
    ],
    bearish_reasons: [
      'High valuation (P/E 68)',
      'Elevated implied volatility',
      'Stock extended from EMA20',
      'Earnings gap risk',
      'Macro uncertainty',
      'US-China export restrictions',
      'Competition increasing',
    ],
    risk: { risk_level: 'MEDIUM' },
  },
  market: MARKET,
  status: 'OK',
  source: 'DEMO',
};

/** The two risks the reference marks with a red dot rather than an amber one. */
export const DEMO_SEVERE_RISKS = 2;

export const DEMO_AI_SUMMARY =
  'NVIDIA shows strong pre-earnings setup with rising EPS estimates, solid ' +
  'technical trend, and bullish options positioning. The company has a strong ' +
  'history of outperforming expectations, with 8 consecutive beats and an ' +
  'average 6.8% surprise. AI demand and data center growth remain key ' +
  'drivers. Risk factors include high valuation and elevated volatility.';

export const DEMO_ACTION_PLAN = {
  bias: 'BUY',
  entry: 'Wait for confirmation',
  target: '$153+',
  stop: '$135',
};

/* ------------------------------------------------------------ options flow */

export const DEMO_OPTIONS: OptionsOverview = {
  symbol: 'NVDA',
  spot: 142.67,
  expiry_label: '12/13/24',
  expirations: ['20241213', '20241220', '20250117', '20250221', '20250321'],
  expiration_labels: ['12/13/24', '12/20/24', '01/17/25', '02/21/25', '03/21/25'],
  dte: 3,
  market: MARKET,
  tiles: {
    call_volume: 1_240_000,
    put_volume: 760_000,
    total_volume: 2_000_000,
    call_share: 62,
    put_share: 38,
    call_oi: 11_800_000,
    put_oi: 6_600_000,
    total_oi: 18_400_000,
    notional: 286_700_000,
    put_call_volume: 0.61,
    put_call_oi: 0.68,
    call_oi_vs_put_oi: 1.8,
    call_volume_change: 42,
    put_volume_change: -18,
    total_volume_change: 15,
    volume_basis: 'SESSION',
    put_call_bias: 'Bullish',
    status: 'OK',
  },
  metrics: DEMO_OPTION_METRICS,
  volume_by_strike: { mode: 'volume', basis: 'SESSION', peak: 390_000, strikes: DEMO_VOLUME_BY_STRIKE, status: 'OK' },
  open_interest_by_strike: { peak: 880_000, strikes: DEMO_OI_BY_STRIKE, status: 'OK' },
  delta_exposure_by_strike: { peak: 53_000, strikes: DEMO_DE_BY_STRIKE, status: 'OK' },
  flow: {
    trades: DEMO_TRADES as any,
    all_count: 108,
    sweeps: 6,
    blocks: 4,
    classified: 10,
    ranking_basis: 'SESSION_VOLUME',
    contracts_sampled: 24,
    session_date: '2024-12-10',
    status: 'OK',
  },
  scatter: {
    points: DEMO_SCATTER as any,
    spot: 142.67,
    strike_min: 125,
    strike_max: 160,
    status: 'OK',
  },
  breakdown: {
    basis: 'TAPE',
    total: 2_000_000,
    total_label: 'Contracts',
    segments: [
      { label: 'Call Buys', value: 960_000, percent: 48 },
      { label: 'Call Sells', value: 280_000, percent: 14 },
      { label: 'Put Buys', value: 500_000, percent: 25 },
      { label: 'Put Sells', value: 160_000, percent: 8 },
      { label: 'Others', value: 100_000, percent: 5 },
    ],
    status: 'OK',
  },
  expiration_flow: {
    basis: 'SESSION',
    peak: 700_000,
    expirations: [
      { expiry: '20241213', label: '12/13', full_label: '12/13/24', dte: 3, calls: 700_000, puts: 250_000 },
      { expiry: '20241220', label: '12/20', full_label: '12/20/24', dte: 10, calls: 420_000, puts: 190_000 },
      { expiry: '20250117', label: '01/17', full_label: '01/17/25', dte: 38, calls: 210_000, puts: 110_000 },
      { expiry: '20250221', label: '02/21', full_label: '02/21/25', dte: 73, calls: 95_000, puts: 60_000 },
      { expiry: '20250321', label: '03/21+', full_label: '03/21/25', dte: 101, calls: 130_000, puts: 80_000 },
    ],
    status: 'OK',
  },
  risk_zones: DEMO_RISK_ZONES,
  sentiment: {
    score: 78,
    label: 'BULLISH',
    components: [
      { name: 'Call share of volume', score: 62, weight: 30 },
      { name: 'Call share of open interest', score: 64, weight: 20 },
      { name: 'Bullish premium on the tape', score: 81, weight: 35 },
      { name: '25-delta skew', score: 71, weight: 15 },
    ],
    status: 'OK',
  },
  iv_history: { iv: 62.4, iv_rank: 81, iv_percentile: 78, status: 'OK' },
  realized_move: { average: 6.8, windows: 4, status: 'OK' },
  status: 'OK',
  source: 'DEMO',
};

export const DEMO_FLOW_INSIGHTS = [
  { text: 'Large call sweep detected at 150 strike (Dec 13) — bullish positioning ahead of earnings.', good: true },
  { text: 'Call/Put ratio at 0.61 with high call volume (62%).', good: true },
  { text: 'Unusual call activity 3.2x above average in the 145–150 range.', good: true },
  { text: 'Implied volatility elevated (78th percentile) — market expects large move.', good: true },
  { text: 'Put walls at 135 indicate strong support, while call wall at 150 forms resistance.', good: true },
  { text: 'Overall options flow is bullish.', good: true },
];

export const DEMO_TRADE_IDEAS = [
  {
    kind: 'bull' as const, title: 'Bullish Call', sub: 'Dec 13, 130C',
    tag: 'High Conviction', entry: 2.35, target: 6.00, stop: 1.20, rr: 2.1,
  },
  {
    kind: 'bear' as const, title: 'Bearish Put', sub: 'Dec 13, 135P',
    tag: 'Hedge', entry: 3.15, target: 6.50, stop: 1.60, rr: 1.8,
  },
];

export { DEMO_STATUS, DEMO_INDICES, DEMO_WATCHLIST };
