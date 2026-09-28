import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Users } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState, Unavailable } from './shared';
import { Panel } from '../components/common';
import { money, signedPct } from '../lib/format';

/**
 * Peer comparison: a stock against the others in its sector.
 *
 * Reads a name in relatives -- score, today's move, IV rank and relative
 * strength vs SPY -- against its sector peers, so its reading has a group to
 * stand against instead of hanging in isolation.
 */

function scoreColor(v: number | null | undefined): string {
  if (typeof v !== 'number') return 'var(--text-mute)';
  if (v >= 60) return 'var(--green)';
  if (v <= 40) return 'var(--red)';
  return 'var(--amber)';
}

function ScoreBar({ value }: { value: number | null | undefined }) {
  const v = typeof value === 'number' ? Math.max(0, Math.min(100, value)) : 0;
  return (
    <div className="pr-bar">
      <div className="pr-bar-fill" style={{ width: `${v}%`, background: scoreColor(value) }} />
      <div className="pr-bar-mid" />
    </div>
  );
}

function rsColor(v: number | null | undefined): string {
  if (typeof v !== 'number') return 'var(--text-mute)';
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text-mute)';
}

export default function PeersPage({ ctx }: { ctx: PageContext }) {
  const symbol = ctx.symbol;
  const navigate = useNavigate();
  const peers = useApi<any>((s) => api2.peers(symbol, s), [symbol]);
  const d = peers.data;

  return (
    <div className="page">
      <PageHead title={`Peers · ${symbol}`}
        subtitle="How this name stacks up against its sector — score, today's move, IV rank and relative strength vs SPY." />

      {peers.error ? <ErrorState error={peers.error} />
        : peers.initialLoading || !d ? <Loading />
          : d.status !== 'OK' ? <Unavailable status={d.status} detail={d.detail} />
            : (
              <>
                <Panel title={`${d.sector} peers`} icon={<Users size={13} />} noBody>
                  <div className="pr-summary">
                    <b>{symbol}</b> ranks
                    <span className="pr-rank"> #{d.subject_rank} of {d.of}</span>
                    &nbsp;in {d.sector} by directional score.
                    {d.spy_return_1m_pct != null && (
                      <span className="pr-spy"> · SPY 1M {d.spy_return_1m_pct > 0 ? '+' : ''}{d.spy_return_1m_pct}%</span>
                    )}
                  </div>
                  <div className="table-wrap">
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>Symbol</th><th>Score</th>
                          <th className="r">Today</th>
                          <th className="r">IV rank</th>
                          <th className="r">RS vs SPY (1M)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.rows.map((r: any) => (
                          <tr key={r.symbol}
                            className={`clickable ${r.is_subject ? 'pr-subject' : ''}`}
                            onClick={() => navigate(`/peers/${r.symbol}${ctx.search}`)}>
                            <td>
                              <b>{r.symbol}</b>
                              {r.is_subject && <em className="pr-you"> this</em>}
                              <div className="pr-name">{r.name}</div>
                            </td>
                            <td>
                              <div className="pr-cell">
                                <ScoreBar value={r.score} />
                                <span style={{ color: scoreColor(r.score), minWidth: 30 }}>
                                  {r.score != null ? Math.round(r.score) : '—'}
                                </span>
                              </div>
                            </td>
                            <td className="num r" style={{ color: rsColor(r.change_percent) }}>
                              {r.change_percent != null ? signedPct(r.change_percent) : '—'}
                            </td>
                            <td className="num r">{r.iv_rank != null ? r.iv_rank.toFixed(0) : '—'}</td>
                            <td className="num r" style={{ color: rsColor(r.rs_1m) }}>
                              {r.rs_1m != null ? `${r.rs_1m > 0 ? '+' : ''}${r.rs_1m}%` : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="hint">{d.detail}</div>
                </Panel>

                <div className="hint" style={{ padding: '2px 4px' }}>
                  <b>How to read it:</b> a leader shows a higher score, positive
                  relative strength (outperforming SPY) and — depending on your
                  view — a lower IV rank (calmer) or higher (more expected
                  movement). Click any row to pivot the comparison to that name.
                </div>
              </>
            )}
    </div>
  );
}
