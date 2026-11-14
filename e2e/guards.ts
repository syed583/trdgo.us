// What the suite must never click when it runs against production, and the strings it hunts for.

export const DESTRUCTIVE = [
  'place order', 'buy', 'sell', 'close all', 'close position', 'flatten',
  'acknowledge', 'ack', 'delete', 'remove', 'reset', 'save settings', 'apply',
  'kill', 'halt', 'pause', 'resume', 'reconcile', 'evaluate', 'capture', 'scan now',
];

export function isDestructive(label: string): boolean {
  const l = (label || '').trim().toLowerCase();
  return DESTRUCTIVE.some((d) => l === d || l.startsWith(d + ' ') || l.includes(' ' + d));
}

// Kept in step with backend/model_privacy.py FORBIDDEN_STRINGS. If you add one there, add it here.
export const FORBIDDEN = [
  'Unusual Whales', 'unusualwhales', 'Finnhub', 'finnhub', 'Finviz', 'finviz',
  'Benzinga', 'Marketaux', 'mtapi', 'Intraday model',
  'buy_weight', 'sell_weight', 'points_label',
  'sec_filings', 'iv_greeks', 'options_flow', 'relative_strength',
  'Grok', 'Kimi', 'Gemini', 'ChatGPT', 'directional-',
];
