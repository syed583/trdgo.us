import React from 'react';
import {
  Activity, BarChart3, Building2, CalendarDays, Database, Gauge, Globe,
  LineChart, Loader2, TrendingUp, User, Waves, Layers,
} from 'lucide-react';
import WorldGlobe from './WorldGlobe';

/** The data sources the Tradgo Call reads, around the globe. Decorative --
 *  it loops while the single call computes, mirroring the Analysis scan. */
const NODES: { key: string; label: string; icon: React.ReactNode }[] = [
  { key: 'flow', label: 'Flow', icon: <LineChart size={16} /> },
  { key: 'oi', label: 'OI', icon: <Layers size={16} /> },
  { key: 'levels', label: 'Levels', icon: <Database size={16} /> },
  { key: 'trend', label: 'Trend', icon: <BarChart3 size={16} /> },
  { key: 'rsi', label: 'RSI', icon: <Activity size={16} /> },
  { key: 'rs', label: 'RS', icon: <TrendingUp size={16} /> },
  { key: 'volume', label: 'Volume', icon: <Gauge size={16} /> },
  { key: 'regime', label: 'Regime', icon: <Globe size={16} /> },
  { key: 'insider', label: 'Insider', icon: <User size={16} /> },
  { key: 'fund', label: '13F', icon: <Building2 size={16} /> },
  { key: 'earn', label: 'Earnings', icon: <CalendarDays size={16} /> },
  { key: 'dark', label: 'Dark Pool', icon: <Waves size={16} /> },
];

function hueOf(index: number, count: number): number {
  return Math.round((index / count) * 320);
}
function nodeAt(index: number, count: number, radius: number) {
  const a = ((index / count) * 360 - 90) * Math.PI / 180;
  return { x: 50 + Math.cos(a) * radius, y: 50 + Math.sin(a) * radius };
}

export default function TradgoScan({ symbol }: { symbol: string }) {
  const count = NODES.length;
  return (
    <div className="an-scan">
      <div className="an-scan-head">
        <h2>Analyzing <span>{symbol}</span>…</h2>
        <p>Streaming live and historical data from every configured source</p>
      </div>

      <div className="an-orbit">
        <svg className="an-orbit-lines" viewBox="0 0 100 100"
          preserveAspectRatio="none" aria-hidden="true">
          {NODES.map((s, i) => {
            const p = nodeAt(i, count, 38);
            const hue = hueOf(i, count);
            const lit = `hsl(${hue} 85% 62%)`;
            const path = `M ${p.x} ${p.y} L 50 50`;
            return (
              <g key={s.key}>
                <line x1={p.x} y1={p.y} x2="50" y2="50" className="an-wire scanning live"
                  style={{ stroke: `hsl(${hue} 80% 58% / 0.8)` }} />
                {[0, 0.45, 0.9].map((delay) => (
                  <circle key={delay} r="1.2" className="an-pip" style={{ fill: lit, color: lit }}>
                    <animateMotion path={path} dur="1.35s" begin={`-${delay}s`} repeatCount="indefinite" />
                  </circle>
                ))}
              </g>
            );
          })}
        </svg>

        <div className="an-orbit-core">
          <WorldGlobe size={168} spinning />
        </div>

        {NODES.map((s, i) => {
          const p = nodeAt(i, count, 38);
          const hue = hueOf(i, count);
          return (
            <div className="an-node scanning" key={s.key}
              style={{
                left: `${p.x}%`, top: `${p.y}%`,
                ['--src' as any]: `hsl(${hue} 82% 58%)`,
                ['--src-soft' as any]: `hsl(${hue} 82% 58% / .14)`,
              }}>
              <span className="as-icon">{s.icon}</span>
              <span className="as-body"><b>{s.label}</b></span>
              <span className="an-node-state"><Loader2 size={10} className="spin" /></span>
            </div>
          );
        })}
      </div>

      <div className="an-progress">
        <div className="ap-track ts-indeterminate"><i /></div>
        <div className="an-progress-foot">
          <span>Analyzing {symbol} — gathering and processing data</span>
        </div>
      </div>
    </div>
  );
}
