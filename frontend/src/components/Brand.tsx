import React from 'react';

/**
 * Tradgo.US mark: the tape -- four columns, one of them unusual.
 *
 * What this app actually does, in four shapes: a field of ordinary volume
 * and one print that is not. The green column is the reading; the rest are
 * the context that makes it one.
 *
 * No plate behind it and no border, so it sits on whatever surface it is
 * given. That costs the contrast a dark plate used to guarantee, so the
 * quiet columns are drawn in a mid slate that holds against both the white
 * sidebar and the dark one -- a colour chosen to survive both themes rather
 * than to look best in either.
 *
 * Filled rectangles rather than strokes: at 16px a stroke thins to grey,
 * while a filled bar keeps its weight.
 */
export function BrandMark({ size = 26 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="Tradgo.US"
      style={{ display: 'block', flexShrink: 0 }}
    >
      {/* Ordinary volume. */}
      <rect x="1" y="17" width="5.4" height="14" rx="1.8" fill="#64748B" />
      <rect x="9" y="11" width="5.4" height="20" rx="1.8" fill="#3B82F6" />
      <rect x="17" y="14.5" width="5.4" height="16.5" rx="1.8" fill="#64748B" />
      {/* The print that is not. */}
      <rect x="25" y="1" width="5.4" height="30" rx="1.8" fill="#21D07A" />
    </svg>
  );
}

/** Sidebar lockup: mark + wordmark + tagline. */
export function BrandLockup() {
  return (
    <>
      <div className="brand">
        <BrandMark size={26} />
        <div className="brand-name">
          Tradgo<span className="brand-tld">.US</span>
        </div>
      </div>
      <div className="brand-tag">TRADE SMARTER. FASTER.</div>
    </>
  );
}

export const BRAND_NAME = 'Tradgo.US';
export const BRAND_TAGLINE = 'TRADE SMARTER. FASTER.';
export const BRAND_QUOTE_EARNINGS = '“Data. Discipline. Edge.”';
export const BRAND_QUOTE_OPTIONS = '“Options flow turns data into opportunity.”';
