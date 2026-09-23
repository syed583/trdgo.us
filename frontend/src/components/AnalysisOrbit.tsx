import React, { useEffect } from 'react';
import {
  Activity, BarChart3, Building2, CalendarDays, CheckCircle2, Database,
  FileText, Gauge, Globe, LineChart, Loader2, Newspaper, TrendingUp, User, X,
} from 'lucide-react';
import type { AnalysisStream, Stage } from '../hooks/useAnalysisStream';

const GREEN = '#21d07a';
const AMBER = '#f0a92b';
const BLUE = '#3b82f6';

const ICONS: Record<string, React.ReactNode> = {
  sec_filings: <FileText size={16} />,
  insider: <User size={16} />,
  institutional: <Building2 size={16} />,
  options_flow: <LineChart size={16} />,
  gex: <Database size={16} />,
  price_action: <BarChart3 size={16} />,
  relative_strength: <TrendingUp size={16} />,
  macro: <Globe size={16} />,
  iv_greeks: <Activity size={16} />,
  volume: <Gauge size={16} />,
  news: <Newspaper size={16} />,
  earnings: <CalendarDays size={16} />,
};

/** Where each node sits on the ring, in degrees clockwise from the top. */
function angleFor(index: number, count: number): number {
  return (index / count) * 360 - 90;
}

function position(index: number, count: number, radius: number) {
  const radians = angleFor(index, count) * Math.PI / 180;
  return {
    x: 50 + Math.cos(radians) * radius,
    y: 50 + Math.sin(radians) * radius,
  };
}

/**
 * Fullscreen analysis view: sources arranged around a core, lighting up as
 * each provider answers.
 *
 * The arrangement is decorative; the states are not. A node turns green when
 * its provider actually returned and amber when it returned nothing, and the
 * connecting line only animates while that source is still outstanding. So
 * the picture always describes the real run rather than a scripted sequence.
 */
export default function AnalysisOrbit({
  symbol, stream, onClose,
}: { symbol: string; stream: AnalysisStream; onClose: () => void }) {
  const { stages, state, result, elapsed, running, done, total, percent } = stream;

  // Escape closes, as with any overlay. Registered once so it does not stack
  // listeners across re-renders.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const shown: Stage[] = stages.length ? stages : [];
  const count = shown.length || 12;

  return (
    <div className="orbit-overlay" role="dialog" aria-label="Live analysis">
      <button className="orbit-close" onClick={onClose} aria-label="Close">
        <X size={18} />
      </button>

      <div className="orbit-head">
        <h2>Analyzing <span>{symbol}</span></h2>
        <p>Gathering live market intelligence from every configured source</p>
      </div>

      <div className="orbit-stage">
        <svg className="orbit-lines" viewBox="0 0 100 100" preserveAspectRatio="none">
          {shown.map((s, i) => {
            const p = position(i, count, 38);
            const status = state[s.key]?.status || 'pending';
            const colour = status === 'complete' ? GREEN
              : status === 'unavailable' ? AMBER : BLUE;
            return (
              <line
                key={s.key}
                x1="50" y1="50" x2={p.x} y2={p.y}
                stroke={colour}
                strokeWidth="0.25"
                opacity={status === 'pending' ? 0.15
                  : status === 'scanning' ? 0.45 : 0.7}
                className={status === 'scanning' ? 'orbit-line-active' : undefined}
              />
            );
          })}
        </svg>

        <div className="orbit-core">
          <div className={`oc-globe ${running ? 'spinning' : ''}`}>
            <i /><i /><i />
          </div>
          <div className="oc-text">
            <b>US-STOCK READER</b>
            <span>INTELLIGENCE CORE</span>
            <em>{running ? 'SCANNING LIVE SOURCES' : 'ANALYSIS COMPLETE'}</em>
          </div>
        </div>

        {shown.map((s, i) => {
          const p = position(i, count, 38);
          const status = state[s.key]?.status || 'pending';
          return (
            <div
              className={`orbit-node ${status}`}
              key={s.key}
              style={{ left: `${p.x}%`, top: `${p.y}%` }}
              title={s.sub}
            >
              <span className="on-icon">{ICONS[s.key] || <Database size={16} />}</span>
              <span className="on-text">
                <b>{s.label}</b>
                <i>{s.sub}</i>
                <u>
                  {status === 'complete' ? 'Complete'
                    : status === 'unavailable' ? 'No data'
                      : status === 'scanning' ? 'Scanning…' : 'Pending'}
                </u>
              </span>
            </div>
          );
        })}
      </div>

      <div className="orbit-footer">
        <div className="of-bar">
          <div className="ofb-top">
            <span>{running ? 'Scanning market data…' : 'Analysis complete'}</span>
            <b>{percent}%</b>
          </div>
          <div className="ap-track"><i style={{ width: `${percent}%` }} /></div>
        </div>

        <div className="of-stats">
          <div><b>{done}/{total}</b><span>Data Sources</span></div>
          <div><b>{elapsed.toFixed(1)}s</b><span>Elapsed</span></div>
          <div>
            <b className={running ? 'live' : ''}>{running ? 'Live' : 'Done'}</b>
            <span>{running ? 'In progress' : 'Finished'}</span>
          </div>
          {result && (
            <div>
              <b style={{
                color: result.actionable === false ? AMBER
                  : result.decision?.includes('BUY') ? GREEN : undefined,
              }}>
                {Math.round(result.direction_score)}
              </b>
              <span>{result.decision}</span>
            </div>
          )}
        </div>

        {result ? (
          <button className="ai-cta" onClick={onClose}>
            View the full breakdown <TrendingUp size={13} />
          </button>
        ) : (
          <div className="of-waiting">
            <Loader2 size={13} className="spin" />
            Sources answer at their own pace — a symbol nobody has opened yet
            takes longer than a cached one.
          </div>
        )}
      </div>
    </div>
  );
}
