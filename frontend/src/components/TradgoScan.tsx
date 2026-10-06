import {
  Activity, BarChart3, Building2, CalendarDays, Database, Gauge, Globe,
  LineChart, TrendingUp, User, Waves, Layers,
} from 'lucide-react';

/** The data sources the Tradgo Call reads, arranged around a core. Decorative
 *  loading animation -- it loops while the single call computes. */
const NODES = [
  { key: 'flow', label: 'Flow', icon: <LineChart size={15} />, color: '#8ef04c' },
  { key: 'oi', label: 'OI', icon: <Layers size={15} />, color: '#4cf0a0' },
  { key: 'levels', label: 'Levels', icon: <Database size={15} />, color: '#4cf0d0' },
  { key: 'trend', label: 'Trend', icon: <BarChart3 size={15} />, color: '#4cd0f0' },
  { key: 'rsi', label: 'RSI', icon: <Activity size={15} />, color: '#4c9cf0' },
  { key: 'rs', label: 'RS', icon: <TrendingUp size={15} />, color: '#6c7cf0' },
  { key: 'volume', label: 'Volume', icon: <Gauge size={15} />, color: '#a06cf0' },
  { key: 'regime', label: 'Regime', icon: <Globe size={15} />, color: '#d04cf0' },
  { key: 'insider', label: 'Insider', icon: <User size={15} />, color: '#f04ca0' },
  { key: 'fund', label: '13F', icon: <Building2 size={15} />, color: '#f0714c' },
  { key: 'earn', label: 'Earnings', icon: <CalendarDays size={15} />, color: '#f0a92b' },
  { key: 'dark', label: 'Dark Pool', icon: <Waves size={15} />, color: '#f0d04c' },
];

function pos(i: number, n: number, r: number) {
  const a = ((i / n) * 360 - 90) * Math.PI / 180;
  return { x: 50 + Math.cos(a) * r, y: 50 + Math.sin(a) * r };
}

export default function TradgoScan({ symbol }: { symbol: string }) {
  const n = NODES.length;
  return (
    <div className="ts-scan">
      <div className="ts-head">
        <h2>Analyzing <b>{symbol}</b>…</h2>
        <p>Reading the options tape, trend, relative strength and ownership</p>
      </div>
      <div className="ts-orbit">
        <svg className="ts-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {NODES.map((nd, i) => {
            const p = pos(i, n, 38);
            return (
              <g key={nd.key}>
                <line x1={p.x} y1={p.y} x2={50} y2={50} className="ts-wire"
                  style={{ stroke: nd.color, opacity: 0.5 }} />
                <circle r="1.1" className="ts-pip" style={{ fill: nd.color }}>
                  <animateMotion path={`M ${p.x} ${p.y} L 50 50`} dur="1.5s"
                    begin={`${-(i % 5) * 0.3}s`} repeatCount="indefinite" />
                </circle>
              </g>
            );
          })}
        </svg>
        <div className="ts-core"><Globe size={54} /></div>
        {NODES.map((nd, i) => {
          const p = pos(i, n, 44);
          return (
            <div key={nd.key} className="ts-node"
              style={{ left: `${p.x}%`, top: `${p.y}%`, ['--c' as any]: nd.color }}>
              <span className="ts-node-ic">{nd.icon}</span>
              <span className="ts-node-lb">{nd.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
