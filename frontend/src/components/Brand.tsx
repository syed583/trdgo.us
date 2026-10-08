import React from 'react';
import logoImg from '../assets/logo.png';

/**
 * Trdgo.us mark: a bull's head on a black disc, ringed in copper.
 *
 * The bull is the brand -- drawn in the copper/gold gradient from the logo on a
 * black plate so it reads on either theme's surface, with an open copper arc
 * sweeping the lower-left the way the logo's ring does. Filled shapes rather
 * than fine strokes, so it keeps its weight down to sidebar size (~26px).
 */
export function BrandMark({ size = 26 }: { size?: number }) {
  const gid = React.useId();
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      role="img"
      aria-label="Trdgo.us"
      style={{ display: 'block', flexShrink: 0 }}
    >
      <defs>
        <linearGradient id={gid} x1="18" y1="20" x2="50" y2="50"
          gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#ecc389" />
          <stop offset="0.55" stopColor="#c88a45" />
          <stop offset="1" stopColor="#a4692c" />
        </linearGradient>
      </defs>

      {/* Black disc. */}
      <circle cx="32" cy="32" r="24" fill="#111318" />
      {/* Open copper ring sweeping the lower-left, as in the logo. */}
      <path d="M 23 11 A 23 23 0 1 0 53 43" fill="none"
        stroke={`url(#${gid})`}
        strokeWidth="2.2" strokeLinecap="round" />

      {/* Bull's head, facing forward: two swept horns, a broad brow and a
          tapering muzzle. Drawn as filled shapes so it holds at small sizes. */}
      <g fill={`url(#${gid})`}>
        {/* Left horn. */}
        <path d="M25 24C19 22 14 17 13 12c-1-2 2-3 3-1 3 5 8 8 12 9 2 1 0 5-3 4z" />
        {/* Right horn. */}
        <path d="M39 24c6-2 11-7 12-12 1-2-2-3-3-1-3 5-8 8-12 9-2 1 0 5 3 4z" />
        {/* Head: broad brow tapering to a muzzle. */}
        <path d="M32 20c7 0 13 4 13 11 0 5-3 9-6 12-2 2-4 4-7 5-3-1-5-3-7-5-3-3-6-7-6-12 0-7 6-11 13-11z" />
      </g>
      {/* Eyes and nostrils punched out of the head in the plate colour. */}
      <g fill="#111318">
        <ellipse cx="26.5" cy="30" rx="2.1" ry="2.6" />
        <ellipse cx="37.5" cy="30" rx="2.1" ry="2.6" />
        <ellipse cx="29.7" cy="41" rx="1.5" ry="2" />
        <ellipse cx="34.3" cy="41" rx="1.5" ry="2" />
      </g>
    </svg>
  );
}

/** Sidebar lockup: mark + wordmark + tagline. */
export function BrandLockup() {
  return (
    <>
      <div className="brand-logo">
        <img src={logoImg} alt="Tradgo.us" />
      </div>
      <div className="brand-tag">TRADE SMARTER. FASTER.</div>
    </>
  );
}

// Shown in the sidebar footer. The other brand strings that used to live
// here (name, tagline, an options variant) had no readers and were removed.
export const BRAND_QUOTE_EARNINGS = '“Data. Discipline. Edge.”';
