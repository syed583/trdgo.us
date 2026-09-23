import React from 'react';
import { Gauge } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { money } from '../lib/format';

/**
 * What the options market expects from a company's next report, against what
 * that company's reports have actually done.
 *
 * The comparison is the whole panel. An expected move of 7% says nothing on
 * its own; it says something next to a stock that moved 3% on each of its
 * last four reports, and something else next to one that moved 12%. Both
 * numbers are measurements -- theirs for the priced move, the stock's own
 * closes for the history -- and the sentence between them stops at the
 * observation. What to do about it is not this app's call.
 */
export default function EarningsPreview({ symbol }: { symbol: string }) {
  const preview = useApi<any>(
    (s) => api2.earningsPreview(symbol, s), [symbol]);
  const p = preview.data;

  if (preview.initialLoading) {
    return <div className="ep-note">Reading this company's reports…</div>;
  }
  if (!p || p.status !== 'OK') {
    return (
      <div className="ep-note">
        {p?.detail || 'No earnings preview available for this symbol.'}
      </div>
    );
  }

  const expected = p.expected_move_percent;
  const typical = p.typical_move_percent;
  const wide = expected != null && typical ? expected / typical : null;

  return (
    <div className="ep">
      <div className="ep-head">
        <Gauge size={13} />
        <b>{p.next_report_label || 'Date not scheduled'}</b>
        <span className="ep-when">{p.next_report_time}</span>
        {p.quarter_ending && (
          <span className="ep-q">quarter ending {p.quarter_ending}</span>
        )}
      </div>

      <div className="ep-tiles">
        <div className="ep-tile">
          <span>MARKET IS PRICING</span>
          <b>{expected == null ? 'not yet' : `±${expected}%`}</b>
          {p.expected_move != null && (
            <em>{money(p.expected_move)} either way</em>
          )}
        </div>
        <div className="ep-tile">
          <span>TYPICALLY MOVES</span>
          <b>{typical == null ? '--' : `${typical}%`}</b>
          <em>{p.reports_measured} reports measured</em>
        </div>
        <div className="ep-tile">
          <span>STREET ESTIMATE</span>
          <b>{p.street_estimate != null ? money(p.street_estimate) : '--'}</b>
          <em>EPS</em>
        </div>
        <div className="ep-tile">
          <span>BEAT RATE</span>
          <b>{p.beat_rate == null ? '--' : `${p.beat_rate}%`}</b>
          <em>{p.beat_count} of {p.reports_measured}</em>
        </div>
      </div>

      {p.expectation_vs_history && (
        <p className={`ep-verdict ${wide == null ? ''
          : wide >= 1.25 ? 'ep-wide' : wide <= 0.8 ? 'ep-tight' : ''}`}>
          {p.expectation_vs_history}
        </p>
      )}

      {p.history?.length > 0 && (
        <div className="ep-table-wrap">
          <table className="ep-table">
            <thead>
              <tr>
                <th>Reported</th>
                <th className="r">EPS</th>
                <th className="r">Est.</th>
                <th className="r">Next day</th>
                <th className="r">3 days</th>
                <th className="r">1 week</th>
              </tr>
            </thead>
            <tbody>
              {p.history.map((h: any) => (
                <tr key={h.date}>
                  <td>{h.date}</td>
                  <td className="r">
                    {h.eps_actual != null ? money(h.eps_actual) : '--'}
                  </td>
                  <td className="r ep-dim">
                    {h.eps_estimate != null ? money(h.eps_estimate) : '--'}
                  </td>
                  <td className={`r ${tone(h.move_1d)}`}>{pct(h.move_1d)}</td>
                  <td className={`r ${tone(h.move_3d)}`}>{pct(h.move_3d)}</td>
                  <td className={`r ${tone(h.move_1w)}`}>{pct(h.move_1w)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="ep-foot">{p.detail}</p>
    </div>
  );
}

function pct(value: number | null | undefined): string {
  return value == null ? '--' : `${value > 0 ? '+' : ''}${value}%`;
}

function tone(value: number | null | undefined): string {
  if (value == null) return 'ep-dim';
  return value > 0 ? 'ep-pos' : value < 0 ? 'ep-neg' : 'ep-dim';
}
