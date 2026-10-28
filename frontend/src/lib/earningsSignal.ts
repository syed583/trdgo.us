// Display of an earnings model's call on its detail page.
//
// The backend is the single source of truth: each model's scorer returns the
// FINAL `signal` (BUY / SELL / NEUTRAL / LOW_DATA) after its own cutoffs and
// gates, plus a `tier` for the models with strong bands. The page must show
// those, never re-derive a call from the score with its own thresholds.

export type Tone = 'buy' | 'sell' | 'flat';

export interface SignalView {
  label: string;          // badge text
  tone: Tone;             // ets-badge class
  color: string;          // gauge / number / setup colour
  setup: string;          // line under the gauge
  note: string | null;    // why a raw BUY/SELL was withheld, if it was
}

interface Scored {
  score?: number | null;
  signal?: string | null;
  tier?: string | null;
  raw_signal?: string | null;
  im_gate?: string | null;
  priced_in?: boolean | null;
}

const GREEN = 'var(--green)';
const RED = 'var(--red)';
const AMBER = 'var(--amber)';
const MUTE = 'var(--text-mute)';

export function signalView(d: Scored | null | undefined): SignalView {
  const score = d?.score;
  const signal = (d?.signal || '').toUpperCase();
  if (score == null || !signal || signal === 'PENDING') {
    return { label: '--', tone: 'flat', color: MUTE, setup: '--', note: null };
  }
  if (signal === 'LOW_DATA') {
    return { label: 'LOW DATA', tone: 'flat', color: MUTE, setup: 'Low data',
      note: 'Too little of the model\'s evidence is available for a BUY or SELL.' };
  }

  const tier = (d?.tier || '').toUpperCase();
  const label = tier && tier !== 'LOW DATA' ? tier : signal;   // e.g. STRONG BUY, SPECULATIVE SHORT
  const strong = label.startsWith('STRONG');
  const buy = signal === 'BUY';
  const sell = signal === 'SELL';

  let note: string | null = null;
  const raw = (d?.raw_signal || '').toUpperCase();
  if (signal === 'NEUTRAL' && (raw === 'BUY' || raw === 'SELL')) {
    note = d?.im_gate === 'no_trade' || d?.priced_in
      ? `${raw} withheld by the Implied-Move gate (NO TRADE).`
      : `${raw} withheld by the model's confirmation rule.`;
  }

  return {
    label,
    tone: buy ? 'buy' : sell ? 'sell' : 'flat',
    color: buy ? GREEN : sell ? RED : AMBER,
    setup: buy ? (strong ? 'Strong Setup' : 'Good Setup')
      : sell ? (strong ? 'Weak Setup' : 'Soft Setup') : 'Neutral',
    note,
  };
}

// The intraday models share the detail component and keep their own fixed
// BUY >= 55 / SELL <= 45 reading of the score; unchanged.
export function legacySignalView(score: number | null | undefined): SignalView {
  if (score == null) return { label: '--', tone: 'flat', color: MUTE, setup: '--', note: null };
  const buy = score >= 55;
  const sell = score <= 45;
  return {
    label: buy ? 'BUY' : sell ? 'SELL' : 'NEUTRAL',
    tone: buy ? 'buy' : sell ? 'sell' : 'flat',
    color: buy ? GREEN : sell ? RED : AMBER,
    setup: score >= 65 ? 'Strong Setup' : buy ? 'Good Setup'
      : score <= 35 ? 'Weak Setup' : sell ? 'Soft Setup' : 'Neutral',
    note: null,
  };
}

// Band text per earnings detail route, mirroring tab_settings (verified by the
// 2026-10-28 audit). Display only; the call itself always comes from the backend.
const BANDS: Record<string, string> = {
  '/claude-last': 'BUY > 70 · NEUTRAL 50–70 · SELL < 50, subject to the Implied-Move gate.',
  '/claude-new-pz': 'BUY > 70 · NEUTRAL 50–70 · SELL < 50, subject to the Implied-Move gate.',
  '/claude-pro': 'BUY ≥ 55 · NEUTRAL 46–54 · SELL ≤ 45.',
  '/grok': 'BUY ≥ 55 · NEUTRAL 46–54 · SELL ≤ 45.',
  '/meta': 'BUY ≥ 55 · NEUTRAL 46–54 · SELL ≤ 45.',
  '/chatgpt': 'STRONG BUY ≥ 80 · BUY ≥ 65 · NEUTRAL 40–64 · SELL 21–39 · STRONG SELL < 21.',
  '/kimi': 'STRONG BUY ≥ 65 · BUY ≥ 58 · NEUTRAL 43–57 · SELL ≤ 42 · STRONG SELL ≤ 35, '
    + 'and at least 3 of 6 evidence groups must agree with options not opposing.',
  '/gemini': 'STRONG BUY ≥ 68 · SPECULATIVE BUY ≥ 58 · NEUTRAL 43–57 · SPECULATIVE SHORT ≤ 42 · STRONG SHORT ≤ 32.',
};

export function bandsFor(base: string): string | null {
  return BANDS[base] ?? null;
}
