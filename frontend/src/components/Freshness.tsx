import React from 'react';
import type { FreshnessStamp } from '../api/client';

/**
 * How old the figures in this panel are.
 *
 * Every panel here draws on a different feed, and once rendered they all look
 * alike: a last trade, an OPRA tape print fifteen minutes behind, a Form 4 two
 * days old and a 13F a quarter old are four very different claims shown as
 * the same kind of number. Working out which was which meant knowing how the
 * app is wired, which is the wrong thing to ask of a reader.
 *
 * The badge reads the stamp the payload carries, never a constant written
 * beside the panel: when the option chain is the provider's delayed copy
 * outside market hours, this has to change with it or it is a decoration that
 * is wrong exactly when it matters.
 */
export default function Freshness({
  stamp,
  compact = false,
}: {
  stamp?: FreshnessStamp | null;
  compact?: boolean;
}) {
  if (!stamp || !stamp.label) return null;

  const kind = (stamp.kind || '').toLowerCase();
  const title = [stamp.detail, stamp.source ? `Source: ${stamp.source}` : '']
    .filter(Boolean)
    .join('\n');

  return (
    <span className={`fresh fresh-${kind} ${compact ? 'fresh-sm' : ''}`} title={title}>
      {kind === 'live' && <i className="fresh-dot" />}
      {stamp.label}
    </span>
  );
}
