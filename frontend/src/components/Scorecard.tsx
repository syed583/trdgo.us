import React from 'react';
import { ClipboardCheck } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { Panel } from './common';

/**
 * How often this outlook's calls have actually been right.
 *
 * "Right" means beating SPY in the called direction by the end of the call's
 * horizon, measured from the price when the call was made. Split by score
 * band because that is the question the weights have to answer: if a score
 * of 75 is right no more often than a score of 62, the number is not
 * measuring anything, and the screen should not be trusted until it is.
 *
 * Says plainly when there is not yet enough to judge, rather than showing a
 * hit rate built on four calls as if it meant something.
 */
export default function Scorecard({ horizon }: { horizon: string }) {
  const card = useApi<any>((s) => api2.callScorecard(horizon, 30, s), [horizon],
    { refreshMs: 300000 });
  const d = card.data;

  const pct = (v: number | null | undefined) => (v == null ? '--' : `${v.toFixed(0)}%`);
  const ex = (v: number | null | undefined) =>
    (v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`);

  return (
    <Panel
      title={<span className="sc-title"><ClipboardCheck size={14} /> Scorecard · last 30 days</span>}
      right={d && <span className="at-chip">{d.overall?.calls ?? 0} judged · {d.pending ?? 0} pending</span>}
    >
      {!d ? <p className="at-note">Loading…</p> : (
        <>
          {!d.enough_data && (
            <p className="sc-warn">
              Not enough judged calls yet to trust these rates
              ({d.overall?.calls ?? 0} of the 30 needed). Calls are judged
              after their session closes, so this fills in over the coming
              days.
            </p>
          )}
          <div className="sc-grid">
            <div className="sc-big">
              <span>Right</span>
              <b>{pct(d.overall?.hit_rate)}</b>
              <em>{ex(d.overall?.avg_excess_pct)} vs SPY on average</em>
            </div>
            <table className="sc-tbl">
              <thead><tr><th>Decision</th><th className="r">Calls</th><th className="r">Right</th><th className="r">vs SPY</th></tr></thead>
              <tbody>
                {Object.entries(d.by_decision || {}).map(([k, v]: [string, any]) => (
                  <tr key={k}><td>{k}</td><td className="r">{v.calls}</td>
                    <td className="r">{pct(v.hit_rate)}</td><td className="r">{ex(v.avg_excess_pct)}</td></tr>
                ))}
              </tbody>
            </table>
            <table className="sc-tbl">
              <thead><tr><th>Score</th><th className="r">Calls</th><th className="r">Right</th><th className="r">vs SPY</th></tr></thead>
              <tbody>
                {Object.entries(d.by_score || {}).map(([k, v]: [string, any]) => (
                  <tr key={k}><td>{k}</td><td className="r">{v.calls}</td>
                    <td className="r">{pct(v.hit_rate)}</td><td className="r">{ex(v.avg_excess_pct)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="at-foot">{d.note}</p>
        </>
      )}
    </Panel>
  );
}
