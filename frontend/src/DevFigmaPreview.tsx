import React from 'react';
import { useLocation } from 'react-router-dom';

/**
 * DEV ONLY. Shows the Figma frame for the current route inside the real application, so the design can
 * be judged with the real shell, the real menu and the real routing around it.
 *
 * WHY THIS CANNOT REACH THE SERVER. The frames carry the Figma's sample values - NVDA at $184.72, a
 * made-up account name, invented win rates - and publishing those as if they were this server's numbers
 * is exactly what the owner's rule forbids. Two things stop it:
 *
 *   1. `import.meta.env.DEV` is replaced by the literal `false` at build time, so the whole branch below
 *      is dead code in a production build and the dynamic import is never reachable.
 *   2. The frames live in frontend/figma-demo, outside src/ - and the build is verified afterwards by
 *      searching dist/ for the sample values. A claim is not proof; the grep is.
 *
 * When the design is approved these frames are thrown away, not wired up: the real pages get the design,
 * and every number on them comes from this server.
 */

const FRAME_FOR_ROUTE: Record<string, () => Promise<{ default: React.ComponentType }>> = {
  '/dashboard': () => import('../figma-demo/frames/Dashboard'),
  '/strategy-trades': () => import('../figma-demo/frames/StrategyTrades'),
  '/smart-move/engine20': () => import('../figma-demo/frames/SmartMoveLive'),
  '/pre-earnings-direction': () => import('../figma-demo/frames/PreEarningsDirection'),
  '/option-scanner': () => import('../figma-demo/frames/IVCrushCheck'),
  '/earnings-signals': () => import('../figma-demo/frames/SignalHistory'),
  '/forward-scoreboard': () => import('../figma-demo/frames/ForwardScoreboard'),
  '/unusual-activity': () => import('../figma-demo/frames/UnusualActivity'),
  '/historical-backtest': () => import('../figma-demo/frames/HistoricalBacktest'),
};

/** The canvas size each frame was drawn at, so it is shown at its own scale rather than stretched. */
const SIZE: Record<string, [number, number]> = {
  '/dashboard': [1536, 1024],
  '/strategy-trades': [1024, 1536],
  '/smart-move/engine20': [1536, 1024],
  '/pre-earnings-direction': [1536, 1024],
  '/option-scanner': [1199, 1312],
  '/earnings-signals': [1024, 1536],
  '/forward-scoreboard': [1199, 1312],
  '/unusual-activity': [1536, 1024],
  '/historical-backtest': [1199, 1312],
};

/**
 * The frame is shown only when ?design=1 is asked for, and only in development. The REAL redesigned
 * page is what `?newui=1` gets, because the real page is the thing being built - the frame is a
 * reference to hold it against, not the product.
 */
export function hasFigmaFrame(pathname: string, search: string): boolean {
  if (!import.meta.env.DEV) return false;
  if (new URLSearchParams(search).get('design') !== '1') return false;
  return pathname in FRAME_FOR_ROUTE;
}

/**
 * The frames are Tailwind, and this app has no Tailwind build - the dependency is installed but never
 * configured, so every class the frames use resolves to nothing and the design renders as bare text.
 *
 * So the preview loads Tailwind from the CDN when it first mounts, with PREFLIGHT OFF. Preflight is
 * Tailwind's global reset; left on, it would strip the app's own headings, lists and form controls on
 * every other page in this dev session. The fonts come with it for the same reason they do in the
 * standalone demo: Figma writes font-['Inter:Bold'], which is not a real family.
 */
let tailwindLoading: Promise<void> | null = null;
function ensureDesignCss(): Promise<void> {
  if (tailwindLoading) return tailwindLoading;
  tailwindLoading = new Promise<void>((resolve) => {
    const fonts = document.createElement('link');
    fonts.rel = 'stylesheet';
    fonts.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
      + '&family=Roboto:wght@400;500;700&family=Roboto+Condensed:wght@400;500;700&display=swap';
    document.head.appendChild(fonts);

    const style = document.createElement('style');
    style.textContent = `
      .dev-figma [class*="font-['Inter"] { font-family: 'Inter', sans-serif !important; }
      .dev-figma [class*="font-['Roboto"] { font-family: 'Roboto', sans-serif !important; }
      .dev-figma [class*="font-['Roboto:Condensed"] { font-family: 'Roboto Condensed', sans-serif !important; }
    `;
    document.head.appendChild(style);

    (window as any).tailwind = { config: { corePlugins: { preflight: false } } };
    const tw = document.createElement('script');
    tw.src = 'https://cdn.tailwindcss.com/3.4.16';
    tw.onload = () => resolve();
    tw.onerror = () => resolve();      // the frame still renders, just unstyled; say so rather than hang
    document.head.appendChild(tw);
  });
  return tailwindLoading;
}

export default function DevFigmaPreview() {
  const { pathname } = useLocation();
  const [Cmp, setCmp] = React.useState<React.ComponentType | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [cssReady, setCssReady] = React.useState(false);

  React.useEffect(() => { ensureDesignCss().then(() => setCssReady(true)); }, []);

  React.useEffect(() => {
    let live = true;
    setCmp(null); setErr(null);
    const load = FRAME_FOR_ROUTE[pathname];
    if (!load) return undefined;
    load().then((m) => { if (live) setCmp(() => m.default); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [pathname]);

  const [w, h] = SIZE[pathname] || [1536, 1024];

  if (err) return <div style={{ padding: 24, color: '#f87171' }}>Frame failed to load: {err}</div>;
  if (!Cmp || !cssReady) return <div style={{ padding: 24, color: '#93a0bd' }}>Loading the design…</div>;

  return (
    <div className="dev-figma">
      <div className="dev-figma-note">
        Design preview — the figures are the Figma&apos;s sample values, not this server&apos;s data.
        Development build only.
      </div>
      <div className="dev-figma-stage">
        <div style={{ width: w, height: h, position: 'relative', overflow: 'hidden' }}>
          <Cmp />
        </div>
      </div>
    </div>
  );
}
