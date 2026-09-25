/**
 * Typed client for the Tradgo.US backend.
 *
 * Every payload carries `status` and `source`. `status !== 'OK'` means the
 * provider could not supply the data - the UI shows that state rather than a
 * placeholder number.
 */

/**
 * Where the API lives.
 *
 * Empty string means "same origin as this page", which is what makes a single
 * shared URL work: the backend serves the built frontend, so every /api call
 * goes back to whatever host the browser loaded. A hardcoded 127.0.0.1 would
 * resolve to the *visitor's* machine once the app is reachable from anywhere.
 *
 * The dev server still needs the absolute URL because Vite runs on :5173 while
 * the API runs on :8000.
 */
export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');

export type ProviderStatus =
  | 'OK'
  | 'NO_DATA'
  | 'NO_TRADES'
  | 'NO_VOLUME'
  | 'NO_CHAIN'
  | 'NO_PRICE'
  | 'UNKNOWN_SYMBOL'
  | 'INSUFFICIENT_DATA'
  | 'TEST_DATA'
  | 'NOT_TRACKED'
  | 'NO_SCHEDULED_EVENT'
  | string;

export interface MarketClock {
  session: 'OPEN' | 'PRE_MARKET' | 'AFTER_HOURS' | 'CLOSED';
  is_open: boolean;
  label: string;
  time_et: string;
  date_et: string;
  iso: string;
}

export interface ExtendedQuote {
  price: number;
  previous_close: number;
  change: number;
  change_percent: number;
  bid: number | null;
  ask: number | null;
  session: string;
  session_label: string;
  as_of: string | null;
  source: string;
}

export interface Quote {
  symbol: string;
  name: string;
  exchange?: string;
  tags: string[];
  price: number | null;
  change: number | null;
  change_percent: number | null;
  previous_close: number | null;
  bid: number | null;
  ask: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  market: MarketClock;
  // Present only outside regular hours, and only when the extended session
  // has actually traded. The fields above stay on the last completed
  // session, so the two are never conflated.
  extended?: ExtendedQuote | null;
  status: ProviderStatus;
  source: string;
  error?: string;
}

export interface ChartBar {
  t: string;
  label: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema20: number | null;
  ema50: number | null;
  ema200: number | null;
}

/**
 * How old a payload's figures are, as the payload itself reports them.
 *
 * Carried per response rather than assumed per panel: the option chain is
 * live from the broker during regular hours and a delayed provider copy
 * outside them, and a badge written beside the panel would be wrong in
 * exactly the half of the day that matters.
 */
export interface FreshnessStamp {
  kind: 'LIVE' | 'DELAYED' | 'SNAPSHOT' | 'FILED' | 'QUARTERLY' | string;
  label: string;
  source?: string;
  detail?: string;
  delay_minutes?: number | null;
  as_of?: string | null;
}

/** One price the market has turned at more than once. */
export interface PriceLevel {
  price: number;
  kind: 'support' | 'resistance';
  touches: number;
  last_touch: string | null;
  low: number;
  high: number;
  distance_pct: number;
}

export interface ChartLevels {
  support: PriceLevel[];
  resistance: PriceLevel[];
  status: string;
  note?: string;
  basis?: Record<string, number>;
}

export interface ChartPayload {
  symbol: string;
  range: string;
  bar_size?: string;
  bars: ChartBar[];
  ohlc?: { open: number | null; high: number | null; low: number | null; close: number | null };
  ema?: { ema20: number | null; ema50: number | null; ema200: number | null };
  change: number | null;
  change_percent: number | null;
  volume?: number;
  levels?: ChartLevels;
  status: ProviderStatus;
  error?: string;
}

export interface IndexRow {
  label: string;
  value: number | null;
  change: number | null;
  change_percent: number | null;
  spark: number[];
  instrument: string | null;
  is_proxy: boolean;
  status: ProviderStatus;
}

export interface DateRange {
  key: string;
  label: string;
  value: string;
  /** How many tracked companies report inside this window. */
  earnings_count?: number;
  /** A few of them, so the card is scannable without opening the calendar. */
  earnings_symbols?: string[];
  /** Present on the synthetic "next_up" window only. */
  days_away?: number;
  range_start?: string;
  range_end?: string;
}

export interface IndicesPayload {
  indices: IndexRow[];
  market: MarketClock;
  dates: Record<string, DateRange>;
  status: ProviderStatus;
}

export interface WatchlistCard {
  symbol: string;
  name: string;
  price: number | null;
  change: number | null;
  change_percent: number | null;
  score: number | null;
  score_raw?: number;
  rating: string | null;
  score_basis: string;
  quote_status: ProviderStatus;
  earnings: {
    date?: string | null;
    date_label?: string | null;
    timing_label?: string;
    short_label?: string;
    eps_estimate?: number | null;
    revenue_estimate?: number | null;
    quarter_label?: string | null;
    date_confirmed?: boolean;
    lifecycle?: string | null;
    detail?: string;
    source?: string;
    status: ProviderStatus;
  };
}

export interface WatchlistPayload {
  cards: WatchlistCard[];
  market: MarketClock;
  score_basis: string;
  status: ProviderStatus;
}

export interface FlowTrade {
  time: string;
  timestamp: string;
  epoch: number;
  symbol: string;
  expiry: string;
  expiry_label: string;
  strike: number;
  right: 'C' | 'P';
  type: 'Call' | 'Put';
  contracts: number;
  price: number;
  notional: number;
  kind: 'SWEEP' | 'BLOCK' | 'SPLIT' | 'SINGLE';
  side: string;
  sentiment: 'Bullish' | 'Bearish';
  sentiment_confidence?: 'HIGH' | 'LOW';
  exchanges: string[];
  prints: number;
  quote_bid?: number | null;
  quote_ask?: number | null;
}

export interface StrikeRow {
  strike: number;
  calls: number;
  puts: number;
}

/**
 * One series of per-strike figures.
 *
 * All three series share a shape so a component can hold whichever the user
 * picked. `basis` says which session the numbers came from and is only set on
 * the volume series -- out of hours there is no volume today, so it falls back
 * to the last completed tape and has to say so.
 */
export interface StrikeSeries {
  mode?: string;
  basis?: string;
  peak: number;
  strikes: StrikeRow[];
  status: ProviderStatus;
}

export interface OptionsOverview {
  symbol: string;
  /** Which provider answered. Present on every overview payload. */
  source?: string;
  spot: number | null;
  expiry_label: string | null;
  expirations: string[];
  expiration_labels: string[];
  dte: number | null;
  market: MarketClock;
  tiles: {
    call_volume: number;
    put_volume: number;
    total_volume: number;
    call_share: number | null;
    put_share: number | null;
    call_oi: number;
    put_oi: number;
    total_oi: number;
    notional: number;
    put_call_volume: number | null;
    put_call_oi: number | null;
    call_oi_vs_put_oi: number | null;
    call_volume_change: number | null;
    put_volume_change: number | null;
    total_volume_change: number | null;
    volume_basis: 'SESSION' | 'LAST_SESSION_TAPE' | 'NONE';
    put_call_bias: string | null;
    status: ProviderStatus;
  };
  metrics: Record<string, any>;
  volume_by_strike: StrikeSeries;
  open_interest_by_strike: StrikeSeries;
  delta_exposure_by_strike: StrikeSeries;
  flow: {
    trades: FlowTrade[];
    all_count?: number;
    sweeps?: number;
    blocks?: number;
    classified?: number;
    ranking_basis?: string;
    contracts_sampled?: number;
    session_date?: string | null;
    status: ProviderStatus;
    note?: string;
  };
  scatter: {
    points: (FlowTrade & { is_large: boolean })[];
    spot: number | null;
    strike_min: number | null;
    strike_max: number | null;
    status: ProviderStatus;
    note?: string;
  };
  breakdown: {
    basis?: string;
    total: number;
    total_label?: string;
    segments: { label: string; value: number; percent: number | null }[];
    status: ProviderStatus;
    note?: string;
  };
  expiration_flow: {
    basis?: string;
    expirations: { expiry: string; label: string; full_label: string; dte: number; calls: number; puts: number }[];
    peak: number;
    status: ProviderStatus;
  };
  risk_zones: Record<string, any>;
  sentiment: {
    score: number | null;
    label: string;
    components?: { name: string; score: number; weight: number }[];
    status: ProviderStatus;
  };
  iv_history: Record<string, any>;
  realized_move: Record<string, any>;
  status: ProviderStatus;
  error?: string;
}

export interface EarningsOverview {
  symbol: string;
  /** Which provider answered. Present on every overview payload. */
  source?: string;
  quote: Quote;
  chart: ChartPayload;
  score: {
    symbol: string;
    final: Record<string, any>;
    components: Record<string, any>;
    confidence: Record<string, any>;
    risk: Record<string, any>;
    providers: Record<string, string>;
    status: ProviderStatus;
  };
  options: Record<string, any>;
  risk_zones: Record<string, any>;
  earnings: Record<string, any>;
  estimates: {
    rows: {
      label: string;
      days_ago: number;
      eps_estimate: number | null;
      revenue_estimate_b: number | null;
      analyst_count: number | null;
      snapshot_time: string;
    }[];
    eps_change_90d: number | null;
    distinct_snapshots?: number;
    sources?: string[];
    status: ProviderStatus;
  };
  history: {
    quarters: {
      label: string;
      date: string;
      estimate: number | null;
      actual: number | null;
      surprise_percent: number | null;
      beat: boolean;
    }[];
    beat_rate: number | null;
    average_surprise: number | null;
    consecutive_beats: number | null;
    sample_size?: number;
    status: ProviderStatus;
  };
  analysis: Record<string, any>;
  market: MarketClock;
  status: ProviderStatus;
}

export interface SystemStatus {
  feed: {
    provider: string; configured: boolean; status: string;
    app_left?: number | null; app_budget?: number | null; blocked?: boolean;
  };
  database: { connected: boolean; error: string | null };
  market: MarketClock;
  live: boolean;
}

class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: 'application/json' },
      signal,
    });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError(
      `Cannot reach the Tradgo.US backend at ${API_BASE_URL}. Is it running?`,
    );
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      body?.detail || body?.message || `Request failed (HTTP ${response.status})`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export const api = {
  status: (s?: AbortSignal) => request<SystemStatus>('/api/status', s),
  quote: (symbol: string, s?: AbortSignal) =>
    request<Quote>(`/api/quote/${encodeURIComponent(symbol)}`, s),
  chart: (symbol: string, range: string, s?: AbortSignal) =>
    request<ChartPayload>(
      `/api/chart/${encodeURIComponent(symbol)}?range=${encodeURIComponent(range)}`,
      s,
    ),
  indices: (s?: AbortSignal) => request<IndicesPayload>('/api/indices', s),
  tickerStrip: (symbols?: string[], s?: AbortSignal) =>
    request<WatchlistPayload>(
      `/api/tickers/strip${symbols?.length ? `?symbols=${symbols.join(',')}` : ''}`,
      s,
    ),
  optionsOverview: (symbol: string, s?: AbortSignal) =>
    request<OptionsOverview>(
      `/api/options/overview/${encodeURIComponent(symbol)}`,
      s,
    ),
  earningsOverview: (symbol: string, range: string, s?: AbortSignal) =>
    request<EarningsOverview>(
      `/api/earnings/overview/${encodeURIComponent(symbol)}?range=${encodeURIComponent(range)}`,
      s,
    ),
};


/* ------------------------------------------------------------------ types */

export interface ProviderEntry {
  status: ProviderStatus;
  detail?: string;
  note?: string;
  [k: string]: any;
}

export interface HealthPayload {
  providers: Record<string, ProviderEntry>;
  live: boolean;
  market: MarketClock;
  feed: ProviderStatus;
  market_data: ProviderStatus;
  options: ProviderStatus;
}

export interface SymbolMatch {
  symbol: string;
  name: string;
  exchange: string;
  currency: string;
  con_id: number;
}

/* ---------------------------------------------------------------- helpers */

async function send<T>(
  path: string,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(
      detail?.detail || detail?.message || `Request failed (HTTP ${response.status})`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

/* ------------------------------------------------------- extended surface */

export interface InstitutionalMover {
  fund: string | null;
  cik: string | null;
  state: string;
  shares: number;
  previous_shares: number;
  share_change: number;
  value: number | null;
}

export interface InstitutionalActivity {
  freshness?: FreshnessStamp;
  ticker: string;
  cusip?: string | null;
  signal?: string;
  score?: number | null;
  latest_quarter?: string | null;
  previous_quarter?: string | null;
  total_funds?: number | null;
  funds_increasing?: number | null;
  funds_decreasing?: number | null;
  funds_unchanged?: number | null;
  new_positions?: number | null;
  closed_positions?: number | null;
  total_shares_current?: number | null;
  total_shares_previous?: number | null;
  net_share_change?: number | null;
  net_share_change_pct?: number | null;
  top_buyers?: InstitutionalMover[];
  top_sellers?: InstitutionalMover[];
  staleness_note?: string;
  detail?: string;
  status: string;
  source?: string;
}

export const api2 = {
  health: (deep = false, s?: AbortSignal) =>
    request<HealthPayload>(`/api/health${deep ? '?deep=true' : ''}`, s),
  dashboard: (s?: AbortSignal) => request<any>('/api/dashboard', s),

  explainCall: (callId: number) =>
    send<any>(`/api/calls/${callId}/explain`, 'POST'),
  callLatest: (symbol: string, horizon?: string, s?: AbortSignal) =>
    request<any>(
      `/api/calls/latest/${encodeURIComponent(symbol)}`
      + (horizon ? `?horizon=${encodeURIComponent(horizon)}` : ''), s),
  aiTradeBoard: (horizon: string = 'SWING', s?: AbortSignal) =>
    request<any>(`/api/ai-trade/board?horizon=${encodeURIComponent(horizon)}`, s),
  callScorecard: (horizon?: string, days = 30, s?: AbortSignal) =>
    request<any>(
      `/api/calls/scorecard?days=${days}`
      + (horizon ? `&horizon=${encodeURIComponent(horizon)}` : ''), s),
  directional: (symbol: string, horizon?: string, s?: AbortSignal) =>
    request<any>(`/api/directional/${encodeURIComponent(symbol)}`
      + (horizon ? `?horizon=${encodeURIComponent(horizon)}` : ''), s),

  fundsMonthly: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/funds/monthly/${encodeURIComponent(symbol)}`, s),

  fundsDaily: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/funds/daily/${encodeURIComponent(symbol)}`, s),

  fundsLive: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/filings/funds-live?symbol=${encodeURIComponent(symbol)}`, s),

  dividends: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/dividends/${encodeURIComponent(symbol)}`, s),

  explainParameter: (symbol: string, parameter: string, horizon?: string) =>
    send<any>(`/api/explain/parameter/${encodeURIComponent(symbol)}`
      + `/${encodeURIComponent(parameter)}`
      + (horizon ? `?horizon=${encodeURIComponent(horizon)}` : ''), 'POST'),

  providerUsage: (s?: AbortSignal) =>
    request<any>('/api/providers/usage', s),

  disparity: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/disparity/${encodeURIComponent(symbol)}`, s),
  volatility: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/volatility/${encodeURIComponent(symbol)}`, s),


  corporateEvents: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/filings/corporate/${encodeURIComponent(symbol)}`, s),

  institutional: (symbol: string, s?: AbortSignal) =>
    request<InstitutionalActivity>(
      `/api/institutional/${encodeURIComponent(symbol)}`, s),

  searchSymbols: (q: string, s?: AbortSignal) =>
    request<{ matches: SymbolMatch[]; status: ProviderStatus }>(
      `/api/symbols/search?q=${encodeURIComponent(q)}`, s),
  validateSymbol: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/symbols/validate/${encodeURIComponent(symbol)}`, s),

  marketOverview: (s?: AbortSignal) => request<any>('/api/market/overview', s),
  marketPulse: (s?: AbortSignal) => request<any>('/api/market/pulse', s),

  earningsCalendar: (
    range: string, sort: string, q: string, s?: AbortSignal,
  ) => request<any>(
    `/api/earnings/calendar?range=${range}&sort=${sort}` +
    `${q ? `&q=${encodeURIComponent(q)}` : ''}`, s),

  // market-wide options flow
  flowMarketSummary: (s?: AbortSignal) =>
    request<any>('/api/flow/market/summary', s),
  flowMarketTape: (limit = 40, s?: AbortSignal) =>
    request<any>(`/api/flow/market/tape?limit=${limit}`, s),
  flowMarketUnusual: (limit = 15, s?: AbortSignal) =>
    request<any>(`/api/flow/market/unusual?limit=${limit}`, s),
  flowMarketComparison: (s?: AbortSignal) =>
    request<any>('/api/flow/market/comparison', s),
  flowMarketExpiries: (s?: AbortSignal) =>
    request<any>('/api/flow/market/expiries', s),
  flowMarketIntraday: (s?: AbortSignal) =>
    request<any>('/api/flow/market/intraday', s),
  flowMarketSectors: (s?: AbortSignal) =>
    request<any>('/api/flow/market/sectors', s),

  darkpoolRecent: (limit = 50, s?: AbortSignal) =>
    request<any>(`/api/darkpool/recent?limit=${limit}`, s),
  darkpoolSymbol: (symbol: string, limit = 40, s?: AbortSignal) =>
    request<any>(`/api/darkpool/${encodeURIComponent(symbol)}?limit=${limit}`, s),
  darkpoolLevels: (symbol: string, top = 12, s?: AbortSignal) =>
    request<any>(`/api/darkpool/${encodeURIComponent(symbol)}/levels?top=${top}`, s),
  insidersMarket: (days = 30, s?: AbortSignal) =>
    request<any>(`/api/insiders/market?days=${days}`, s),
  insidersTransactions: (limit = 100, buys = false, s?: AbortSignal) =>
    request<any>(`/api/insiders/transactions?limit=${limit}&buys=${buys}`, s),
  insidersSectors: (limit = 10, s?: AbortSignal) =>
    request<any>(`/api/insiders/sectors?limit=${limit}`, s),
  earningsPreview: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/earnings/preview/${encodeURIComponent(symbol)}`, s),
  optionsLevels: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/options/levels/${encodeURIComponent(symbol)}`, s),
  flowBaseline: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/flow/baseline/${encodeURIComponent(symbol)}`, s),

  earningsBrief: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/earnings/brief/${encodeURIComponent(symbol)}`, s),

  earningsCalendarContext: (s?: AbortSignal) =>
    request<any>('/api/earnings/calendar/context', s),

  optionChain: (symbol: string, expiry?: string, s?: AbortSignal) =>
    request<any>(
      `/api/options/chain/${encodeURIComponent(symbol)}` +
      `${expiry ? `?expiry=${expiry}` : ''}`, s),

  newsDesk: (s?: AbortSignal) => request<any>('/api/news/desk', s),
  news: (symbol: string, limit = 20, s?: AbortSignal) =>
    request<any>(`/api/news/${encodeURIComponent(symbol)}?limit=${limit}`, s),
  sentiment: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/sentiment/${encodeURIComponent(symbol)}`, s),


  scannerPresets: (s?: AbortSignal) => request<any>('/api/scanner/presets', s),
  scannerRun: (params: Record<string, string | number>, s?: AbortSignal) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return request<any>(`/api/scanner/run?${qs}`, s);
  },

  watchlist: (s?: AbortSignal) => request<any>('/api/watchlist', s),
  watchlistView: (s?: AbortSignal) => request<any>('/api/watchlist/view', s),
  me: (s?: AbortSignal) => request<any>('/auth/me', s),
  adminUsers: (s?: AbortSignal) => request<any>('/api/admin/users', s),
  adminLogins: (limit = 50, s?: AbortSignal) =>
    request<any>(`/api/admin/logins?limit=${limit}`, s),
  adminCreateUser: (username: string) =>
    send<any>('/api/admin/users', 'POST', { username }),
  adminResetUser: (username: string) =>
    send<any>(`/api/admin/users/${encodeURIComponent(username)}/reset`, 'POST'),
  adminSetActive: (username: string, active: boolean) =>
    send<any>(`/api/admin/users/${encodeURIComponent(username)}/active`, 'POST', { active }),
  adminDeleteUser: (username: string) =>
    send<any>(`/api/admin/users/${encodeURIComponent(username)}`, 'DELETE'),

  watchlistAdd: (symbol: string, note?: string) =>
    send<any>('/api/watchlist', 'POST', { symbol, note }),
  watchlistRemove: (symbol: string) =>
    send<any>(`/api/watchlist/${encodeURIComponent(symbol)}`, 'DELETE'),

  alerts: (s?: AbortSignal) => request<any>('/api/alerts', s),
  alertsEvaluate: (s?: AbortSignal) => request<any>('/api/alerts/evaluate', s),
  alertCreate: (payload: Record<string, unknown>) =>
    send<any>('/api/alerts', 'POST', payload),
  alertDelete: (id: number) => send<any>(`/api/alerts/${id}`, 'DELETE'),
  alertToggle: (id: number, active: boolean) =>
    send<any>(`/api/alerts/${id}`, 'PATCH', { active }),

  journal: (s?: AbortSignal) => request<any>('/api/journal', s),
  journalCreate: (payload: Record<string, unknown>) =>
    send<any>('/api/journal', 'POST', payload),
  journalDelete: (id: number) => send<any>(`/api/journal/${id}`, 'DELETE'),

  strategy: (s?: AbortSignal) => request<any>('/api/strategy', s),
  strategyUpdate: (payload: Record<string, unknown>) =>
    send<any>('/api/strategy', 'PUT', payload),
  strategyReset: () => send<any>('/api/strategy/reset', 'POST'),

  backtest: (payload: Record<string, unknown>) =>
    send<any>('/api/backtest', 'POST', payload),

  // external providers
  providers: (s?: AbortSignal) => request<any>('/api/providers', s),
  benzingaSync: (payload: Record<string, unknown> = {}) =>
    send<any>('/api/providers/benzinga/sync', 'POST', payload),
  alphaVantageSync: (payload: Record<string, unknown>) =>
    send<any>('/api/providers/alpha-vantage/sync', 'POST', payload),

  // earnings intelligence
  earningsLifecycle: (symbol: string, s?: AbortSignal) =>
    request<any>(`/api/earnings/lifecycle/${encodeURIComponent(symbol)}`, s),
};

export { ApiError };
