import React from 'react';
import type { PageContext } from '../App';
import MarketPulsePage from './MarketPulsePage';
import '../new-dashboard.css';
import '../new-market.css';

/**
 * Market Overview in the redesigned shell (Figma frame 24:327, "Tradgo.us - Market Overview").
 *
 * THIS FRAME IS A RESTYLE OF THE LIVE PAGE, NOT A NEW ONE. Panel for panel it is what /market already
 * renders - the header with its stock search, Closed/Refresh and subtitle; the five index cards and
 * their explainer; Market Tide; Sector Performance; Market Heatmap; Market Breadth; Key Market
 * Indicators; Top Gainers; Top Losers; Global Markets; Market Sentiment; Options Put / Call - in that
 * order, down to the footnote wording ("The eleven SPDR sector ETFs, today's move, strongest first").
 * So there is nothing here to rebuild and nothing of the design's to leave out: the difference between
 * the frame and the live page is entirely surface.
 *
 * Which is why this file renders the LIVE page and restyles it in CSS. Re-implementing thirteen panels
 * of market data to change their corners and shadows would duplicate every figure on the page, and the
 * duplicate is the one that stops matching the server. No data is read here and none is derived.
 *
 * Light, dark and mobile come from the shell's own tokens, which the stylesheet uses throughout rather
 * than naming colours, so the page follows the theme instead of pinning one.
 */
export default function NewMarketOverviewPage({ ctx }: { ctx: PageContext }) {
  return (
    <div className="nd nmo">
      <MarketPulsePage ctx={ctx} />
    </div>
  );
}
