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


// Model names that a NEWS feed may legitimately print: a company shipped a product by that name, and a real
// headline about it is content, not a leak. The server scrubs a model name out of OUR attribution fields
// (source, provider, note, model...) and leaves headline text alone, so checking for the bare word here only
// produces false alarms. Vendor names are still checked everywhere, including here.
const MODEL_WORDS = ['Grok', 'Kimi', 'Gemini', 'ChatGPT'];
const NEWS_ROUTES = [/\/api\/news\//];

/** The words that count as a leak for THIS url. */
export function forbiddenFor(url: string): string[] {
  const isNews = NEWS_ROUTES.some((re) => re.test(url));
  return isNews ? FORBIDDEN.filter((w) => !MODEL_WORDS.includes(w)) : FORBIDDEN;
}
