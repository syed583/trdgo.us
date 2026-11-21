import React, { useState } from 'react';
import type { PageContext } from '../App';
import { LiveView, SnapshotView, UniverseView, MondayBmoView } from './SmartMoveEngine20Page';
import '../new-dashboard.css';

/**
 * Smart Money Move in the redesigned shell (Figma frame "Smart move live").
 *
 * DISPLAY ONLY, AND LITERALLY SO. This file imports the four views the live page already renders and
 * puts them under a new header. It fetches nothing, derives nothing and decides nothing. No Smart Move
 * engine file, route file or scanner setting is touched by this kit - the owner froze those, and a
 * redesign has no business in them anyway.
 *
 * WHY NOT REBUILD THE VIEWS. The frame draws the scan banner, the five count tiles, the category
 * filter and the predictions table with its badges. The live page ALREADY has every one of those,
 * driven by the scanner's own payload. Re-implementing them here would be a second copy of how a
 * prediction is displayed - including which category is a prediction and which emphatically is not
 * (NO TRADE vs NO VALID PREDICTION vs PENDING) - and that copy would be the one to go stale. So the
 * views are reused and the new look is CSS, as it is on the Analysis landing.
 *
 * WHAT THE FRAME HAS THAT THIS DOES NOT. A per-table symbol search, an Export button and a Columns
 * button. The live page has none of them; the design's extras are left out rather than invented.
 *
 * NO ARTWORK. The frame's only image asset turned out to be a full screenshot of the frame itself -
 * a painted sidebar, painted count tiles reading BUY 24 / SELL 29, and painted table rows for
 * companies. Using it as a header image would print invented predictions onto the real page, which
 * is the specimen-data problem this redesign already had to undo once. The band is the gradient
 * alone until there is a real image.
 *
 * THE TABS ARE THE LIVE ONES. The frame shows only the LIVE scanner tab, but the live page offers
 * four, so all four stay - nothing on the live site is removed. The historical snapshot stays
 * administrator-only exactly as it is today, for the same reason it was gated: beside three live
 * tabs, a frozen record of one past earnings day reads as a broken live view.
 */
export default function NewSmartMovePage({ ctx }: { ctx: PageContext }) {
  // The same condition the live page uses, so the snapshot tab appears for the same people.
  // The snapshot TAB is administrator-only (owner, 2026-11-21). It used to be gated on
  // `!ctx.readOnly`, which is NOT an administrator: any full-access account has readOnly false,
  // so an ordinary member could open a frozen record of one past earnings day beside three live
  // tabs. ctx.isAdmin is the real flag, and it is what every model internal on this page uses.
  const isAdmin = !!ctx.isAdmin;
  const [view, setView] = useState<'live' | 'universe' | 'mondaybmo' | 'snapshot'>('live');

  return (
    <div className="nd nsm">
      {/* NO HERO: frame 2:1253 puts the tab row directly under the top bar and the title
          inside the status band below it (sm-fig-head), beside the artwork. A hero here put
          every block on the page 203px below where the frame draws it. */}

      <div className="sm nsm-tabs" style={{ paddingBottom: 0 }}>
        <div className="sm-tabs" role="tablist" aria-label="Smart Money Move view">
          <button role="tab" aria-selected={view === 'live'}
            className={`sm-tab ${view === 'live' ? 'on' : ''}`} data-testid="tab-live"
            onClick={() => setView('live')}>LIVE scanner</button>
          <button role="tab" aria-selected={view === 'universe'}
            className={`sm-tab ${view === 'universe' ? 'on' : ''}`} data-testid="tab-universe"
            onClick={() => setView('universe')}>Event universe (30 days)</button>
          <button role="tab" aria-selected={view === 'mondaybmo'}
            className={`sm-tab ${view === 'mondaybmo' ? 'on' : ''}`} data-testid="tab-monday-bmo"
            onClick={() => setView('mondaybmo')}>Monday BMO (Friday window)</button>
          {isAdmin && (
            <button role="tab" aria-selected={view === 'snapshot'}
              className={`sm-tab ${view === 'snapshot' ? 'on' : ''}`} data-testid="tab-snapshot"
              onClick={() => setView('snapshot')}>Historical snapshot (20 stocks)</button>
          )}
        </div>
      </div>

      <div className="nsm-body">
        {view === 'live' ? <LiveView isAdmin={!!ctx.isAdmin} />
          : view === 'universe' ? <UniverseView />
            : view === 'mondaybmo' ? <MondayBmoView />
              : <SnapshotView isAdmin={isAdmin} />}
      </div>
    </div>
  );
}
