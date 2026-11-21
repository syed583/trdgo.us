/* Standalone demo entry. DEV ONLY.
 *
 * This folder is not imported by the app and is not in its build (the installer ships frontend/dist),
 * so the Figma sample values in these frames cannot reach the server. That is the point of keeping the
 * demo outside src/ and outside public/ rather than behind a flag.
 *
 * Each frame is the Figma component UNCHANGED apart from its asset paths. Nothing is re-laid-out,
 * because re-laying it out is exactly what would cost the pixel fidelity this demo exists to show.
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import Dashboard from './frames/Dashboard';
import StrategyTrades from './frames/StrategyTrades';
import SmartMoveLive from './frames/SmartMoveLive';
import PreEarningsDirection from './frames/PreEarningsDirection';
import IVCrushCheck from './frames/IVCrushCheck';
import SignalHistory from './frames/SignalHistory';
import ForwardScoreboard from './frames/ForwardScoreboard';
import UnusualActivity from './frames/UnusualActivity';
import HistoricalBacktest from './frames/HistoricalBacktest';

type Frame = { el: React.ReactNode; w: number; h: number; label: string };

/* Sizes are the frames' own canvas sizes, from the Figma file. */
const FRAMES: Record<string, Frame> = {
  dashboard:   { el: <Dashboard />,            w: 1536, h: 1024, label: 'Market intelligence dashboard' },
  strategy:    { el: <StrategyTrades />,       w: 1024, h: 1536, label: 'Strategy trades' },
  smartmove:   { el: <SmartMoveLive />,        w: 1536, h: 1024, label: 'Smart Money Move live scanner' },
  preearnings: { el: <PreEarningsDirection />, w: 1536, h: 1024, label: 'Pre-earnings direction' },
  ivcrush:     { el: <IVCrushCheck />,         w: 1199, h: 1312, label: 'IV Crush Check' },
  signals:     { el: <SignalHistory />,        w: 1024, h: 1536, label: 'Signal History' },
  forward:     { el: <ForwardScoreboard />,    w: 1199, h: 1312, label: 'Genuine Forward Scoreboard' },
  unusual:     { el: <UnusualActivity />,      w: 1536, h: 1024, label: 'Unusual Activity' },
  backtest:    { el: <HistoricalBacktest />,   w: 1199, h: 1312, label: 'Historical Backtest (Track B)' },
};

function App() {
  const [key, setKey] = React.useState(() => location.hash.replace('#', '') || 'dashboard');
  React.useEffect(() => {
    const on = () => setKey(location.hash.replace('#', '') || 'dashboard');
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  const f = FRAMES[key] || FRAMES.dashboard;
  return (
    <>
      <nav className="demo-nav">
        <b>Figma demo — sample values, dev only</b>
        {Object.entries(FRAMES).map(([k, v]) => (
          <a key={k} href={`#${k}`} className={k === key ? 'on' : ''}>{v.label}</a>
        ))}
        <span className="demo-size">{f.w}×{f.h}</span>
      </nav>
      <div className="frame-wrap">
        <div style={{ width: f.w, height: f.h, position: 'relative', overflow: 'hidden' }}>{f.el}</div>
      </div>
    </>
  );
}
createRoot(document.getElementById('root')!).render(<App />);
