import React from 'react';
import { Coins } from 'lucide-react';
import { Panel } from './common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';

/**
 * What the company pays its holders, and whether that is growing.
 *
 * Declared dividends only -- the dates and amounts the company has actually
 * announced. A forecast of the next payment would be a guess dressed as a
 * schedule, so an upcoming row appears once it has been declared and not
 * before.
 */
export default function Dividends({ symbol }: { symbol: string }) {
  const d = useApi<any>((s) => api2.dividends(symbol, s), [symbol]).data;

  if (!d) {
    return <Panel title="Dividends"><p className="ce-note">Reading dividend history…</p></Panel>;
  }
  if (d.status !== 'OK') {
    return <Panel title="Dividends"><p className="ce-note">{d.detail}</p></Panel>;
  }

  const trend = d.trend as string | null;
  const growth = d.growth_pct as number | null;

  return (
    <Panel title="Dividends" noBody
      right={<span className="ce-count">{d.dividend_yield_pct}% yield</span>}>
      <div className="fa">
        <div className="fa-stats dv-stats">
          <div className="fa-stat">
            <b>{d.latest?.amount != null ? `$${d.latest.amount}` : '--'}</b>
            <span>last payment</span>
          </div>
          <div className="fa-stat">
            <b>${d.annual_dividend ?? '--'}</b>
            <span>a year</span>
          </div>
          <div className="fa-stat">
            <b>{d.dividend_yield_pct ?? '--'}%</b>
            <span>yield</span>
          </div>
          <div className={`fa-stat ${trend === 'rising' ? 'fa-buy' : trend === 'cut' ? 'fa-sell' : ''}`}>
            <b>{growth == null ? '--' : `${growth > 0 ? '+' : ''}${growth}%`}</b>
            <span>{trend === 'cut' ? 'payout cut' : 'vs year before'}</span>
          </div>
        </div>

        <div className="dv-list">
          <div className="dv-row dv-head">
            <span>Ex-date</span><span>Amount</span><span>Declared</span><span>Paid</span>
          </div>
          {(d.payments || []).slice(0, 6).map((p: any) => (
            <div className="dv-row" key={p.ex_date}>
              <span><Coins size={11} /> {p.ex_date}</span>
              <b>${p.amount}</b>
              <span>{p.declared || '--'}</span>
              <span>{p.pay_date || '--'}</span>
            </div>
          ))}
        </div>

        <p className="ce-foot">{d.detail} Source: Nasdaq.</p>
      </div>
    </Panel>
  );
}
