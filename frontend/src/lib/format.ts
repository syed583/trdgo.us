/**
 * Display formatters.
 *
 * Every one of these returns a dash for null/undefined rather than "0" or
 * "NaN". The backend deliberately sends null when a provider cannot supply a
 * value, and showing a zero there would read as real data.
 */

export const DASH = '--';

const isNum = (v: unknown): v is number =>
  typeof v === 'number' && Number.isFinite(v);

export function num(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return DASH;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function money(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return DASH;
  return `$${num(value, digits)}`;
}

export function signed(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return DASH;
  return `${value >= 0 ? '+' : ''}${num(value, digits)}`;
}

export function pct(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return DASH;
  return `${num(value, digits)}%`;
}

export function signedPct(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return DASH;
  return `${value >= 0 ? '+' : ''}${num(value, digits)}%`;
}

/** 1_240_000 -> "1.24M". Used for volume, open interest and notional. */
export function compact(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return DASH;
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${num(value / 1e12, digits)}T`;
  if (abs >= 1e9) return `${num(value / 1e9, digits)}B`;
  if (abs >= 1e6) return `${num(value / 1e6, digits)}M`;
  if (abs >= 1e3) return `${num(value / 1e3, abs >= 1e5 ? 0 : digits)}K`;
  return num(value, 0);
}

export function compactMoney(value: number | null | undefined, digits = 1): string {
  if (!isNum(value)) return DASH;
  return `$${compact(value, digits)}`;
}

/** Integer with thousands separators; blank-safe. */
export function int(value: number | null | undefined): string {
  if (!isNum(value)) return DASH;
  return Math.round(value).toLocaleString('en-US');
}

export function plusMinus(value: number | null | undefined, digits = 1): string {
  if (!isNum(value)) return DASH;
  return `±${num(value, digits)}%`;
}

/** Sign class for colouring: positive green, negative red, neutral muted. */
export function tone(value: number | null | undefined): 'pos' | 'neg' | 'flat' {
  if (!isNum(value) || value === 0) return 'flat';
  return value > 0 ? 'pos' : 'neg';
}

/** 220 -> "220", 222.5 -> "222.5" (strikes should not carry trailing zeros). */
export function strike(value: number | null | undefined): string {
  if (!isNum(value)) return DASH;
  return Number.isInteger(value) ? String(value) : String(value);
}

/**
 * Force millions, the way an options dashboard quotes contract volume
 * (0.76M rather than 760K) so a row of tiles shares one unit.
 */
export function millions(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH;
  // Below a million, "0.01M" hides the number it is meant to show, so fall
  // back to the natural unit rather than forcing the shared one.
  if (Math.abs(value) < 1e6) return compact(value, 0);
  return `${num(value / 1e6, digits)}M`;
}

/**
 * A link target that is safe to render, or undefined.
 *
 * News and filing URLs arrive from upstream feeds and are rendered as
 * clickable links. A `javascript:` or `data:` URL from a hostile or
 * compromised feed would run in this app's origin the moment it is clicked,
 * so only http(s) links are allowed through; anything else returns undefined
 * and the caller renders plain text instead of a link.
 */
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  const trimmed = String(url).trim();
  return /^https?:\/\//i.test(trimmed) ? trimmed : undefined;
}
