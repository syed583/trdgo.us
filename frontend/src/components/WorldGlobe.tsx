import React from 'react';

/**
 * The spinning globe on the analysis screen.
 *
 * Drawn inline rather than loaded as an image: the app's content policy blocks
 * external assets, and an SVG scales cleanly and inherits the theme.
 *
 * The rotation is real rather than a sweep played over a still picture -- the
 * landmasses are drawn twice, side by side, and the pair is translated by
 * exactly one world-width on a loop. When the second copy reaches where the
 * first began the animation restarts, so the seam never shows and the earth
 * simply keeps turning. A clip path hides everything outside the sphere.
 */
export default function WorldGlobe({
  size = 128, spinning = true, seconds = 28,
}: { size?: number; spinning?: boolean; seconds?: number }) {
  // One world is 200 wide; the twin sits beside it and the pair slides left.
  const W = 200;

  const land = (offset: number) => (
    <g transform={`translate(${offset} 0)`}>
      {/* North America */}
      <path d="M30 44 L58 38 L74 46 L70 58 L78 62 L72 74 L58 80
               L52 94 L44 92 L40 78 L28 70 L24 56 Z" />
      {/* Central America */}
      <path d="M52 96 L60 100 L66 110 L60 112 L54 104 Z" />
      {/* South America */}
      <path d="M66 114 L78 112 L84 124 L80 142 L72 160 L64 168
               L58 158 L60 138 L62 124 Z" />
      {/* Greenland */}
      <path d="M86 28 L100 26 L104 36 L96 44 L86 40 Z" />
      {/* Europe */}
      <path d="M112 46 L134 42 L142 50 L136 58 L126 60 L118 56 Z" />
      {/* Africa */}
      <path d="M116 66 L140 62 L150 74 L146 92 L136 112 L126 128
               L118 122 L116 104 L110 86 Z" />
      {/* Western Asia */}
      <path d="M146 44 L172 40 L186 52 L180 66 L162 70 L150 60 Z" />
      {/* Madagascar */}
      <path d="M152 108 L158 106 L160 118 L154 120 Z" />
    </g>
  );

  return (
    <div className="wg" style={{ width: size, height: size }}>
      <svg viewBox="0 0 200 200" width={size} height={size} aria-hidden="true">
        <defs>
          <radialGradient id="wg-ocean" cx="34%" cy="28%" r="80%">
            <stop offset="0%" stopColor="#5ec8ff" />
            <stop offset="30%" stopColor="#2b86d8" />
            <stop offset="62%" stopColor="#14459a" />
            <stop offset="88%" stopColor="#0a1f5c" />
            <stop offset="100%" stopColor="#050c26" />
          </radialGradient>
          <linearGradient id="wg-land" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#7df0c0" />
            <stop offset="55%" stopColor="#35c98a" />
            <stop offset="100%" stopColor="#1d8f68" />
          </linearGradient>
          <radialGradient id="wg-shade" cx="32%" cy="26%" r="78%">
            <stop offset="52%" stopColor="rgba(0,0,0,0)" />
            <stop offset="100%" stopColor="rgba(0,0,0,.66)" />
          </radialGradient>
          <radialGradient id="wg-glow" cx="50%" cy="50%" r="50%">
            <stop offset="72%" stopColor="rgba(80,170,255,0)" />
            <stop offset="100%" stopColor="rgba(80,170,255,.55)" />
          </radialGradient>
          {/* Sun on the upper left, tight and bright. */}
          <radialGradient id="wg-spec" cx="30%" cy="24%" r="34%">
            <stop offset="0%" stopColor="rgba(255,255,255,.75)" />
            <stop offset="45%" stopColor="rgba(255,255,255,.18)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0)" />
          </radialGradient>
          {/* Limb darkening: the edge of a lit sphere is always darker than
              its middle, and without it the disc looks like a sticker. */}
          <radialGradient id="wg-limb" cx="50%" cy="50%" r="50%">
            <stop offset="78%" stopColor="rgba(0,0,0,0)" />
            <stop offset="96%" stopColor="rgba(2,10,30,.45)" />
            <stop offset="100%" stopColor="rgba(2,10,30,.7)" />
          </radialGradient>
          <clipPath id="wg-clip">
            <circle cx="100" cy="100" r="95" />
          </clipPath>
        </defs>

        {/* Atmosphere */}
        <circle cx="100" cy="100" r="99" fill="url(#wg-glow)" />
        <circle cx="100" cy="100" r="95" fill="url(#wg-ocean)" />

        <g clipPath="url(#wg-clip)">
          {/* Graticule. Meridians are ellipses of decreasing width, parallels
              horizontal ones -- cheaper than projecting a real grid and
              indistinguishable at this size. */}
          <g fill="none" stroke="rgba(150,215,255,.24)" strokeWidth="0.7">
            <ellipse cx="100" cy="100" rx="95" ry="95" />
            <ellipse cx="100" cy="100" rx="72" ry="95" />
            <ellipse cx="100" cy="100" rx="44" ry="95" />
            <ellipse cx="100" cy="100" rx="14" ry="95" />
            <ellipse cx="100" cy="100" rx="95" ry="30" />
            <ellipse cx="100" cy="100" rx="95" ry="60" />
            <line x1="5" y1="100" x2="195" y2="100" />
          </g>

          <g className="wg-land" fill="url(#wg-land)"
             stroke="rgba(190,255,230,.55)" strokeWidth="0.6">
            {land(0)}
            {land(W)}
            {spinning && (
              <animateTransform
                attributeName="transform"
                type="translate"
                from="0 0"
                to={`${-W} 0`}
                dur={`${seconds}s`}
                repeatCount="indefinite"
              />
            )}
          </g>

          {/* Terminator, so the sphere reads as lit from one side. */}
          <circle cx="100" cy="100" r="95" fill="url(#wg-shade)" />
        </g>

        {/* Specular highlight, then limb darkening: both sit above the land
            so the continents are lit by the same source as the ocean. */}
        <circle cx="100" cy="100" r="95" fill="url(#wg-spec)" />
        <circle cx="100" cy="100" r="95" fill="url(#wg-limb)" />

        {/* Rim light */}
        <circle cx="100" cy="100" r="95" fill="none"
                stroke="rgba(160,225,255,.5)" strokeWidth="1.2" />
      </svg>
      {spinning && <span className="wg-sweep" />}
    </div>
  );
}
