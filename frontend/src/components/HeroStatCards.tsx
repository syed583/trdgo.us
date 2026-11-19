import React from 'react';
import { Activity, CalendarDays, BarChart3, Zap } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';

/**
 * The four cards on the Analysis hero.
 *
 * They used to be PAINTED INTO the hero image: "$2.4B Options Flow", "128 Earnings This Week",
 * "72 Bullish". A picture of invented figures is still a claim, and it was the first thing a visitor
 * read. Because they were pixels, no code change could correct them - the artwork had to be cleaned and
 * the cards rebuilt as real elements.
 *
 * Each card now shows a figure this server measured, or an em dash. None of them is ever a stand-in,
 * and none fetches anything the page was not already loading except the two cheap market-wide summaries
 * below, which are cached server-side.
 */

const money = (v: any): string => {
  const n = Number(v);
  if (v === null || v === undefined || Number.isNaN(n)) return '—';
  if (Math.abs(n) >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
};

type CardProps = {
  icon: React.ReactNode; tone: string; title: string;
  value: string; sub: string; badge?: string | null; badgeTone?: string;
};

function Card({ icon, tone, title, value, sub, badge, badgeTone = 'up' }: CardProps) {
  return (
    <div className="anh-hc">
      <span className={`anh-hc-ico ${tone}`}>{icon}</span>
      <div className="anh-hc-b">
        <div className="anh-hc-t">{title}</div>
        <div className="anh-hc-v">
          <b>{value}</b>
          {badge && <span className={`anh-hc-badge ${badgeTone}`}>{badge}</span>}
        </div>
        <div className="anh-hc-s">{sub}</div>
      </div>
    </div>
  );
}

export default function HeroStatCards({ ctx }: { ctx: PageContext }) {
  // Both are market-wide summaries the server already caches; neither is a per-symbol call.
  const flow = useApi<any>((s) => api2.flowMarketSummary(s), [], { refreshMs: 300_000 });
  const pulse = useApi<any>((s) => api2.marketPulse(s), [], { refreshMs: 300_000 });

  const prem = flow.data?.status === 'OK' ? flow.data?.total_premium : null;
  const callShare = flow.data?.status === 'OK' ? flow.data?.call_premium_share : null;

  // The earnings count the index payload already carries for this week - no extra request.
  const week = ctx.indices.data?.dates?.THIS_WEEK;
  const earningsCount = week?.earnings_count;

  const sent = pulse.data?.sentiment;
  const clock = ctx.indices.data?.market || pulse.data?.market;

  return (
    <div className="anh-hcards">
      <Card
        icon={<Activity size={17} />} tone="green" title="Options Flow"
        value={money(prem)}
        sub={prem != null ? 'Premium traded today' : 'No reading right now'}
        badge={callShare != null ? `${callShare}% calls` : null}
      />
      <Card
        icon={<CalendarDays size={17} />} tone="blue" title="Earnings"
        value={earningsCount != null ? String(earningsCount) : '—'}
        sub={earningsCount != null ? 'Tracked names this week' : 'No calendar right now'}
      />
      <Card
        icon={<BarChart3 size={17} />} tone="green" title="Market Sentiment"
        value={sent?.value != null ? String(sent.value) : '—'}
        sub={sent?.label || 'No reading right now'}
      />
      <Card
        icon={<Zap size={17} />} tone="amber" title="Live Prices"
        value={clock ? (clock.is_open ? 'Open' : 'Closed') : '—'}
        sub={clock ? (clock.session_label || 'US markets') : 'No market clock'}
      />
    </div>
  );
}
