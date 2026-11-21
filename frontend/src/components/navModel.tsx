import React from 'react';
import {
  Activity, Brain, Building2, CalendarDays, Compass, Crosshair, FlaskConical, Flame, Gauge, History, Layers, LineChart, Receipt, Users,
  LayoutDashboard, Link2, ListOrdered, Menu, Newspaper, PanelLeftClose, PanelLeftOpen,
  Radar, Scale, ScrollText, Settings, ShieldCheck, Sparkles, Star, TrendingUp, UserCog, Waves, Waypoints, X, Zap,
} from 'lucide-react';

/**
 * THE MENU - the single source for every shell.
 *
 * Both the existing sidebar and the redesigned one read this file. That is deliberate: a menu declared
 * twice is a menu that drifts, and the way it drifts here is that a page removed by A.25 or A.26 comes
 * back in one shell while staying hidden in the other. Gate a link once, and both obey.
 *
 * Nothing in this file was rewritten when it moved - the lists are byte-identical to the ones that were
 * in Sidebar.tsx, so lifting them out cannot have changed a single route.
 */

export const S = 15;

export interface NavEntry {
  to: string;
  label: string;
  icon: React.ReactNode;
  /** The section keeps the ticker you are viewing when you switch to it. */
  symbolAware?: boolean;
}

export interface NavGroup {
  heading: string;
  items: NavEntry[];
}

// MT5 auto-trading (operator only): shown next to Strategy Trades for the operator, in the admin panel for the admin.
export const MT5_ITEMS: NavEntry[] = [
  { to: '/mt5', label: 'MT5 Account', icon: <Settings size={S} /> },
  { to: '/mt5/symbols', label: 'MT5 Symbol Mapping', icon: <ListOrdered size={S} /> },
  { to: '/mt5/audit', label: 'MT5 Audit Report', icon: <ScrollText size={S} /> },
  // C.2: who is copying our trades into their own account, and the pause.
  { to: '/mt5/copy-trading', label: 'Copy Trading', icon: <Users size={S} /> },
];

const NAV_GROUPS: NavGroup[] = [
  { heading: 'Analysis', items: [
    { to: '/ai-insights', label: 'Analysis Intraday', icon: <Brain size={S} /> },
    // A.26 (owner decision 2026-11-14): the four per-model intraday boards and All Intraday left the sidebar. They KEEP
    // RUNNING - the intraday feed, the paper tracker and the MT5 mirror are untouched - and their routes still answer, so
    // an administrator reaches them by URL (/grok-intraday, /meta-intraday, /kimi-intraday, /gemini-intraday,
    // /all-intraday-models). NAV_GROUPS is the non-admin menu; an admin's sidebar shows only the admin links anyway.
    { to: '/strategy-trades', label: 'Strategy Trades', icon: <Receipt size={S} /> },
    // A.25 (owner decision 2026-11-13, after the intraday audit): Tradgo Call, Trade Plan, Signal History and Sector
    // Comparison left the menu. Their ROUTES still work (/tradgo-call, /trade-plan, /signal-history, /peers) and every
    // module stays on disk, because the earnings pages, the signal-colouring route and the snapshot warmer import them.
    // Nothing was deleted. Trade Plan's background scanner and its Claude read are off (main.py, trade_plan_service).
    { to: '/smart-move/engine20', label: 'Smart Money (LIVE)', icon: <Gauge size={S} /> },
  ] },
  { heading: 'Earnings & news', items: [
    { to: '/earnings-calendar', label: 'Earnings Calendar', icon: <CalendarDays size={S} /> },
    // A.25 (owner decision 2026-11-13): the seven per-model boards left the menu. Earnings calls are made by Smart Money
    // Move now, and these seven were one component over the same cached data. The ROUTES still work (/claude-last, /grok,
    // /meta, /chatgpt, /kimi, /gemini, /claude-pro) and every module stays on disk, because All Models, the backtests, the
    // monitor registry and the four intraday boards import them. Nothing was deleted.
    // B.1a (owner decision 2026-11-15): All Models and Model Accuracy are the model's own scoreboard - which
    // engines exist and how each one scores - so they are for an administrator only. The routes refuse a
    // non-admin, and an administrator reaches both by URL (/all-models, /model-accuracy), exactly as A.26 left
    // the four boards. Nothing is deleted and no data is lost.
    { to: '/option-scanner', label: 'IV Crush Check', icon: <Flame size={S} /> },
    { to: '/unusual-activity', label: 'Unusual Activity', icon: <Zap size={S} /> },
    { to: '/pre-earnings-direction', label: 'Pre-Earnings Direction', icon: <Compass size={S} /> },
    { to: '/earnings-signals', label: 'Signal History', icon: <History size={S} /> },
    { to: '/forward-scoreboard', label: 'Forward Scoreboard', icon: <ShieldCheck size={S} /> },
    { to: '/historical-backtest', label: 'Historical Backtest', icon: <FlaskConical size={S} /> },
    { to: '/post-earnings-forward', label: 'Post-Earnings AI', icon: <Radar size={S} /> },
    { to: '/news', label: 'News & Sentiment', icon: <Newspaper size={S} /> },
  ] },
  { heading: 'Markets', items: [
    { to: '/market', label: 'Market Overview', icon: <TrendingUp size={S} /> },
    { to: '/dashboard', label: 'Overview', icon: <LayoutDashboard size={S} /> },
    { to: '/stocks', label: 'Stocks', icon: <ListOrdered size={S} /> },
    { to: '/options-flow', label: 'Options', icon: <Activity size={S} /> },
    { to: '/oi-buildup', label: 'OI Build-Up', icon: <Layers size={S} /> },
    { to: '/volatility', label: 'Volatility', icon: <LineChart size={S} /> },
    { to: '/dark-pool', label: 'Dark Pool', icon: <Waves size={S} /> },
    { to: '/market-insiders', label: 'Market Insiders', icon: <UserCog size={S} /> },
    { to: '/institutional-intel', label: 'Institutional Intelligence', icon: <Building2 size={S} /> },
  ] },
  { heading: 'Lists', items: [
    { to: '/watchlist', label: 'Watchlist', icon: <Star size={S} /> },
  ] },
  // C.1a: a user's own broker account. This belongs to NAV_GROUPS (the NON-admin menu). It was first added
  // beside /settings, which lives in the admin branch - so ordinary users, the only people the page is for,
  // never saw it.
  { heading: 'Account', items: [
    { to: '/my-broker', label: 'My Broker', icon: <Link2 size={S} /> },
  ] },
];

export const SYMBOL_SECTIONS = new Set([
  '/earnings', '/options-flow', '/volatility', '/news',
  '/ai-insights', '/peers', '/dark-pool', '/dashboard', '/model-performance',
]);


/** The admin's menu: the operator's console, not the data pages. */
export const ADMIN_GROUP = (mt5Operator?: boolean): NavGroup[] => [{
  heading: '', items: [
    { to: '/admin', label: 'Dashboard', icon: <LayoutDashboard size={S} /> },
    { to: '/admin/users', label: 'Users', icon: <Users size={S} /> },
    { to: '/admin/audit', label: 'Audit Log', icon: <ScrollText size={S} /> },
    { to: '/admin/workflow', label: 'Workflow', icon: <Waypoints size={S} /> },
    { to: '/my-broker', label: 'My Broker', icon: <Link2 size={S} /> },
    { to: '/settings', label: 'Settings', icon: <Settings size={S} /> },
    ...(mt5Operator ? MT5_ITEMS : [])],
}];

/** The non-admin menu, with the operator's MT5 items folded into Analysis exactly as before. */
export const USER_GROUPS = (mt5Operator?: boolean): NavGroup[] => (
  mt5Operator
    ? NAV_GROUPS.map((g) => (g.heading === 'Analysis' ? { ...g, items: [...g.items, ...MT5_ITEMS] } : g))
    : NAV_GROUPS
);
