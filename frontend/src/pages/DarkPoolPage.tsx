import React from 'react';
import type { PageContext } from '../App';
import { DarkPoolTab } from '../components/DarkPool';
import { PageHead } from './shared';

/** Dark pool prints for a symbol -- its own page, out of the options sub-tabs. */
export default function DarkPoolPage({ ctx }: { ctx: PageContext }) {
  return (
    <div className="page">
      <PageHead title={`Dark Pool · ${ctx.symbol}`}
        subtitle="Off-exchange (dark pool) prints — large trades routed away from the lit market." />
      <DarkPoolTab symbol={ctx.symbol} />
    </div>
  );
}
