/**
 * How hard the app should poll right now.
 *
 * MEASURED, NOT ASSUMED. One visible, idle page makes ~19 API calls a minute: the shell alone
 * polls /api/health every 45s, /api/indices every 60s and the quote every 25s, and each page adds
 * its own on top. That is reasonable while the market is moving. It is pure waste when the market
 * is shut - a quote polled every 25 seconds on a Sunday returns the identical Friday close every
 * time, and every one of those calls is a database hit and a provider request.
 *
 * (An earlier note of mine put this at 190 calls a minute. That was wrong: it counted a tab's
 * whole lifetime across 23 page visits, not a minute of idling. The real figure is ~19/min.)
 *
 * So intervals are multiplied when the market is closed. Nothing is skipped, only slowed, and the
 * hook still takes one fresh read whenever the tab is refocused - so coming back to the screen is
 * always current regardless of this.
 *
 * FAILS TOWARD FRESHNESS: until the shell reports the market state, and whenever that state is
 * unknown, the multiplier is 1. Being wrong here should cost requests, never staleness.
 */

let marketOpen: boolean | null = null;   // null = not known yet

/** Called by the shell from /api/health's `market` field. */
export function setMarketOpen(open: boolean | null): void {
  marketOpen = open;
}

export function isMarketOpen(): boolean | null {
  return marketOpen;
}

/**
 * Multiplier applied to every polling interval.
 *
 * 6 is chosen so the shell's 25s quote becomes 2.5 min and its 60s indices become 6 min when the
 * market is shut - slow enough to matter, fast enough that a page left open overnight is not
 * showing something hours old when the bell rings.
 */
export const CLOSED_POLL_SCALE = 6;

export function pollScale(): number {
  return marketOpen === false ? CLOSED_POLL_SCALE : 1;
}
