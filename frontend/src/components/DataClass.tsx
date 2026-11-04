import React from 'react';

/** LIVE / CACHED / STALE / HISTORICAL / UNAVAILABLE: one vocabulary for how current a figure is, shared by the Earnings, Options Flow and Smart Money Move screens. */
export const DATA_CLASS: Record<string, [string, string]> = {
  LIVE: ['sm-q-badge green', 'Produced just now from a live feed, or by the frozen engine on evidence collected inside its prediction window.'],
  CACHED: ['sm-q-badge gray', "Real data not fetched for this request: served from the application's own caches within their limit, or a feed delayed by the account's entitlement."],
  STALE: ['sm-q-badge amber', 'Older than its freshness limit: do not treat it as current.'],
  HISTORICAL: ['sm-q-badge gray', "A stored earlier run, a closed market's last session, or a call about an event that has already reported."],
  UNAVAILABLE: ['sm-q-badge amber', 'No usable data: the provider did not answer, the answer was empty, or no call was made.'],
};

export function DataClassBadge({ klass }: { klass?: string | null }) {
  if (!klass || !DATA_CLASS[klass]) return null;
  return <span className={DATA_CLASS[klass][0]} title={DATA_CLASS[klass][1]} data-testid="data-class">{klass}</span>;
}
