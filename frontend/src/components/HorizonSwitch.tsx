import React, { useEffect, useState } from 'react';

/**
 * Which span of time a call is about.
 *
 * The same stock can be a buy for tomorrow and a sell for the next hour, and
 * a score with no horizon beside it cannot be acted on because nobody knows
 * which question it answered. So every screen that shows a call says which
 * one it is showing, and lets the reader pick.
 *
 * The default follows the market clock: during the session the question is
 * today; any other time -- after the close, overnight, pre-market -- it is
 * the next session.
 */
export type Horizon = 'TODAY' | 'TOMORROW' | 'SWING';

export const HORIZON_COPY: Record<Horizon, { label: string; basis: string }> = {
  TODAY: {
    label: 'Today',
    basis: 'Up or down by today\'s close. Before 9:30 AM ET: yesterday\'s close, '
      + 'the pre-market move and options flow. After the open: VWAP, opening '
      + 'range, intraday trend and volume, and the live options tape.',
  },
  TOMORROW: {
    label: 'Tomorrow',
    basis: 'For the next session: where today closed, the day against SPY, '
      + 'the after-hours move and the full day\'s options flow.',
  },
  SWING: {
    label: 'Swing',
    basis: 'The next few weeks: trend, earnings history, insider activity '
      + 'and positioning.',
  },
};

export function defaultHorizon(session?: string | null): Horizon {
  return session === 'OPEN' || session === 'PRE_MARKET' ? 'TODAY' : 'TOMORROW';
}

export default function HorizonSwitch({
  value, onChange, session,
}: {
  value: Horizon;
  onChange: (h: Horizon) => void;
  session?: string | null;
}) {
  const open = session === 'OPEN';
  return (
    <div className="hz" role="tablist" aria-label="Outlook">
      {(['TODAY', 'TOMORROW', 'SWING'] as Horizon[]).map((h) => {
        // Today is still selectable when the market is shut -- it then shows
        // the session that just finished -- but it says so rather than
        // implying the market is moving.
        const note = h === 'TODAY' && !open ? ' (market closed)' : '';
        return (
          <button
            key={h}
            role="tab"
            aria-selected={value === h}
            className={`hz-btn ${value === h ? 'active' : ''}`}
            onClick={() => onChange(h)}
            title={HORIZON_COPY[h].basis + note}
          >
            {HORIZON_COPY[h].label}
          </button>
        );
      })}
      <UsClock session={session} />
    </div>
  );
}

const SESSION_LABEL: Record<string, string> = {
  OPEN: 'Market open', PRE_MARKET: 'Pre-market', AFTER_HOURS: 'After hours',
  CLOSED: 'Market closed',
};

/** New York time beside the switch: every horizon is defined by it. */
function UsClock({ session }: { session?: string | null }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(t);
  }, []);
  const opts = { timeZone: 'America/New_York' } as const;
  const time = now.toLocaleTimeString('en-US', { ...opts, hour: 'numeric', minute: '2-digit' });
  const day = now.toLocaleDateString('en-US', { ...opts, weekday: 'short' });
  return (
    <span className={`hz-clock ${session === 'OPEN' ? 'open' : ''}`}
      title="US Eastern time (New York)">
      <i />{day} {time} ET{session ? ` · ${SESSION_LABEL[session] || session}` : ''}
    </span>
  );
}
