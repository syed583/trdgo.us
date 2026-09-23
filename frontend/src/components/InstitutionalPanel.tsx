import React from 'react';
import { Building2, TrendingDown, TrendingUp } from 'lucide-react';
import Freshness from './Freshness';
import { api2 } from '../api/client';
import type { InstitutionalActivity, InstitutionalMover } from '../api/client';
import { useApi } from '../hooks/useApi';
import { Panel, StateBlock } from './common';

const GREEN = '#21d07a';
const RED = '#f2465a';

function compact(value?: number | null, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(digits)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(digits)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(digits)}K`;
  return value.toFixed(0);
}

function pct(value?: number | null): string {
  if (value === null || value === undefined) return '--';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

/**
 * Quarterly institutional positioning from Form 13F.
 *
 * Kept apart from insider activity and options flow on purpose. A 13F is filed
 * up to 45 days after the quarter it describes, so the freshest reading here is
 * six weeks old -- it confirms a trend rather than starting one, and the panel
 * says so rather than letting the numbers imply they are current.
 */
export default function InstitutionalPanel({ symbol }: { symbol: string }) {
  const activity = useApi<InstitutionalActivity>(
    (signal) => api2.institutional(symbol, signal),
    [symbol],
  );

  const d = activity.data;

  if (activity.loading && !d) {
    return (
      <Panel title="Institutional Activity" icon={<Building2 size={12} />} noBody>
        <StateBlock loading />
      </Panel>
    );
  }

  if (activity.error || !d) {
    return (
      <Panel title="Institutional Activity" icon={<Building2 size={12} />} noBody>
        <StateBlock error={activity.error || 'Unavailable'} />
      </Panel>
    );
  }

  if (d.status !== 'OK') {
    return (
      <Panel title="Institutional Activity" icon={<Building2 size={12} />} noBody>
        <StateBlock status={d.status} />
        {d.detail && <div className="hint">{d.detail}</div>}
      </Panel>
    );
  }

  const signal = (d.signal || 'neutral').toUpperCase();
  const bullish = signal === 'BULLISH';
  const bearish = signal === 'BEARISH';
  const tone = bullish ? 'pos' : bearish ? 'neg' : '';

  const stats: { k: string; v: string; cls?: string }[] = [
    { k: 'Funds Increasing', v: compact(d.funds_increasing, 0), cls: 'pos' },
    { k: 'Funds Decreasing', v: compact(d.funds_decreasing, 0), cls: 'neg' },
    { k: 'New Positions', v: compact(d.new_positions, 0), cls: 'pos' },
    { k: 'Closed Positions', v: compact(d.closed_positions, 0), cls: 'neg' },
    { k: 'Funds Holding', v: compact(d.total_funds, 0) },
    {
      k: 'Net Share Change',
      v: pct(d.net_share_change_pct),
      cls: (d.net_share_change_pct ?? 0) > 0 ? 'pos'
        : (d.net_share_change_pct ?? 0) < 0 ? 'neg' : '',
    },
  ];

  return (
    <Panel
      title="Institutional Activity"
      icon={<Building2 size={12} />}
      noBody
      right={
        <span className="inst-quarter">
          <Freshness stamp={d.freshness} compact />
          {d.latest_quarter}
          {d.previous_quarter ? ` vs ${d.previous_quarter}` : ''}
        </span>
      }
    >
      <div className="inst-head">
        <div className={`inst-signal ${tone}`}>{signal}</div>
        <div className="inst-score">
          <span className={tone}>
            {d.score !== null && d.score !== undefined
              ? `${d.score > 0 ? '+' : ''}${d.score}`
              : '--'}
          </span>
          <i>Institutional Score</i>
        </div>
      </div>

      <div className="kv">
        {stats.map((s) => (
          <div className="kv-row" key={s.k}>
            <span className="k">{s.k}</span>
            <span className={`v ${s.cls || ''}`}>{s.v}</span>
          </div>
        ))}
      </div>

      {!d.previous_quarter && (
        <div className="hint">
          Only one quarter is loaded, so there is nothing to compare against
          yet. Ingest the previous quarter to see changes.
        </div>
      )}

      <MoverList title="Top Buyers" rows={d.top_buyers} colour={GREEN}
        icon={<TrendingUp size={10} color={GREEN} />} />
      <MoverList title="Top Sellers" rows={d.top_sellers} colour={RED}
        icon={<TrendingDown size={10} color={RED} />} />

      <div className="hint">{d.staleness_note}</div>
    </Panel>
  );
}

function MoverList({
  title, rows, colour, icon,
}: {
  title: string;
  rows?: InstitutionalMover[];
  colour: string;
  icon: React.ReactNode;
}) {
  if (!rows || !rows.length) return null;
  return (
    <div className="inst-movers">
      <div className="inst-movers-head">{icon}{title}</div>
      <div className="tbl-scroll" style={{ maxHeight: 190 }}>
        <table className="tbl">
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.cik}-${i}`}>
                <td title={r.fund || ''}>{(r.fund || '--').slice(0, 34)}</td>
                <td className="num r" style={{ color: colour }}>
                  {r.share_change > 0 ? '+' : ''}{compact(r.share_change)}
                </td>
                <td className="num r">{compact(r.shares)}</td>
                <td>
                  <span className="inst-state">{r.state}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
