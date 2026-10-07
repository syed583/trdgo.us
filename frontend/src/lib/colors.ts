/**
 * Shared chart / JS colours.
 *
 * SVG presentation attributes (recharts `stroke`/`fill`) do NOT resolve CSS
 * `var(--token)`, so chart colours can't use the CSS tokens directly -- they
 * live here instead, mirroring the design tokens in `styles.css`. Keep the two
 * in sync. CSS-context colours (className / style background) should use the
 * CSS tokens, not these.
 */
export const COLORS = {
  accent: '#1b46dd',        // brand accent (= --accent light)
  accentBright: '#3a63f0',  // readable on dark panels (= --accent dark); use for chart lines
  accentDeep: '#1538b8',

  up: '#21d07a',            // --green
  down: '#f2465a',          // --red
  neutral: '#f5a524',       // --amber

  axis: '#5b6580',
  grid: '#8892a8',
  mute: '#8892a8',

  purple: '#7c3aed',
  cyan: '#22d3ee',
} as const;

export default COLORS;
