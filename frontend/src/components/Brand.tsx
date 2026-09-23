import React from 'react';

/**
 * US-Stock Reader mark.
 *
 * A rising three-bar column chart whose tallest bar continues into an upward
 * tick — market structure rather than an illustration. Drawn as vector so it
 * stays crisp at the 26px the sidebar uses, with flat fills (no gradient
 * banding on a dark ground) and a single accent so it reads at a glance.
 */
export function BrandMark({ size = 26 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="US-Stock Reader"
      style={{ display: 'block', flexShrink: 0 }}
    >
      <rect x="0.5" y="0.5" width="31" height="31" rx="7.5"
        fill="#0E1626" stroke="#26344F" />
      {/* Ascending columns */}
      <rect x="6.5" y="19" width="4" height="7" rx="1.2" fill="#3B82F6" opacity="0.85" />
      <rect x="13" y="14" width="4" height="12" rx="1.2" fill="#3B82F6" />
      <rect x="19.5" y="9" width="4" height="17" rx="1.2" fill="#21D07A" />
      {/* Breakout tick off the tallest column */}
      <path d="M8 13.5 L14.5 9.5 L18.5 11.5 L25 5.5"
        stroke="#21D07A" strokeWidth="1.9"
        strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="25" cy="5.5" r="2.1" fill="#21D07A" />
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
          US-Stock<span className="brand-tld"> Reader</span>
        </div>
      </div>
      <div className="brand-tag">TRADE SMARTER. FASTER.</div>
    </>
  );
}

export const BRAND_NAME = 'US-Stock Reader';
export const BRAND_TAGLINE = 'TRADE SMARTER. FASTER.';
export const BRAND_QUOTE_EARNINGS = '“Data. Discipline. Edge.”';
export const BRAND_QUOTE_OPTIONS = '“Options flow turns data into opportunity.”';
