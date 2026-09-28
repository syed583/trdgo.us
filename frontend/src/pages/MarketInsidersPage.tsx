import React from 'react';
import type { PageContext } from '../App';
import { MarketInsidersTab } from '../components/DarkPool';
import { PageHead } from './shared';

/** Market-wide insider transactions -- its own page, out of the options sub-tabs. */
export default function MarketInsidersPage({ ctx }: { ctx: PageContext }) {
  return (
    <div className="page">
      <PageHead title="Market Insiders"
        subtitle="Insider buys and sells across the market — who is buying or selling their own company's stock." />
      <MarketInsidersTab />
    </div>
  );
}
