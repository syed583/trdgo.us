// Every client route, taken from App.tsx (99 <Route> entries), with a symbol filled in for the :symbol forms.
// `admin` marks a page only an administrator should reach; the suite asserts an ordinary user is refused there.

export type Page = { path: string; name: string; admin?: boolean };

const S = 'TSLA';

export const PAGES: Page[] = [
  // --- in the user menu
  { path: '/dashboard', name: 'Dashboard' },
  { path: '/stocks', name: 'Stocks' },
  { path: '/market', name: 'Market' },
  { path: '/news', name: 'News' },
  { path: '/watchlist', name: 'Watchlist' },
  { path: '/strategy-trades', name: 'Strategy Trades' },
  { path: '/smart-move/engine20', name: 'Smart Money Move' },
  { path: '/earnings-calendar', name: 'Earnings Calendar' },
  { path: '/earnings-signals', name: 'Earnings Signals' },
  { path: '/forward-scoreboard', name: 'Forward Scoreboard' },
  { path: '/historical-backtest', name: 'Historical Backtest' },
  { path: '/model-accuracy', name: 'Model Accuracy' },
  { path: '/pre-earnings-direction', name: 'Pre-Earnings Direction' },
  { path: '/post-earnings-forward', name: 'Post-Earnings Forward' },
  { path: '/all-models', name: 'All Models' },
  { path: '/unusual-activity', name: 'Unusual Activity' },
  { path: '/options-flow', name: 'Options Flow' },
  { path: '/option-scanner', name: 'Option Scanner' },
  { path: '/oi-buildup', name: 'OI Buildup' },
  { path: '/dark-pool', name: 'Dark Pool' },
  { path: '/volatility', name: 'Volatility' },
  { path: '/market-insiders', name: 'Market Insiders' },
  { path: '/institutional-intel', name: 'Institutional Intelligence' },
  { path: '/ai-insights', name: 'Analysis Intraday' },
  { path: `/ai-insights/${S}`, name: 'Analysis Intraday (symbol)' },
  { path: '/settings', name: 'Settings' },

  // --- symbol pages a normal user can reach
  { path: `/earnings/${S}`, name: 'Earnings (symbol)' },
  { path: `/peers/${S}`, name: 'Peers' },
  { path: `/options-chain/${S}`, name: 'Options chain' },
  { path: `/news/${S}`, name: 'News (symbol)' },
  { path: `/volatility/${S}`, name: 'Volatility (symbol)' },

  // --- B.1 / A.25 / A.26 made these administrator-only; a normal user must be refused
  { path: '/grok-intraday', name: 'Agent 1 board', admin: true },
  { path: '/meta-intraday', name: 'Agent 2 board', admin: true },
  { path: '/kimi-intraday', name: 'Agent 3 board', admin: true },
  { path: '/gemini-intraday', name: 'Agent 4 board', admin: true },
  { path: '/all-intraday-models', name: 'All Intraday', admin: true },
  { path: '/tradgo-call', name: 'Tradgo Call', admin: true },
  { path: '/trade-plan', name: 'Trade Plan', admin: true },
  { path: '/signal-history', name: 'Signal History', admin: true },
  { path: '/claude-last', name: 'Claude Last', admin: true },
  { path: '/claude-pro', name: 'Claude Pro', admin: true },
  { path: '/chatgpt', name: 'ChatGPT page', admin: true },
  { path: '/gemini', name: 'Gemini page', admin: true },
  { path: '/grok', name: 'Grok page', admin: true },
  { path: '/kimi', name: 'Kimi page', admin: true },
  { path: '/meta', name: 'Meta page', admin: true },
  { path: '/earnings-claude', name: 'Earnings Claude', admin: true },
  { path: '/earnings-equity', name: 'Earnings Equity', admin: true },
  { path: '/earnings-options', name: 'Earnings Options', admin: true },
  { path: '/earnings-trade', name: 'Earnings Trade', admin: true },
  { path: '/model-performance', name: 'Model Performance', admin: true },

  // --- admin console and MT5
  { path: '/admin', name: 'Admin dashboard', admin: true },
  { path: '/admin/users', name: 'Users', admin: true },
  { path: '/admin/audit', name: 'Audit Log', admin: true },
  { path: '/admin/workflow', name: 'Workflow', admin: true },
  { path: '/mt5', name: 'MT5 account', admin: true },
  { path: '/mt5/symbols', name: 'MT5 symbols', admin: true },
  { path: '/mt5/audit', name: 'MT5 audit', admin: true },
];
