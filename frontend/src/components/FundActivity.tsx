import React from 'react';
import { Clock, TrendingDown, TrendingUp } from 'lucide-react';
import { Panel } from './common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';

/**
 * Which funds bought and which sold, from Form 13F.
 *
 * Every institution managing over $100m reports its US equity positions each
 * quarter, so this is the real money's position rather than a guess from
 * price. It is also up to 45 days old by the time it is filed, which is why
 * the quarter it covers is on screen next to every number -- a reader who
 * mistakes this for today's positioning is reading a different signal from
 * the one on offer.
 */
function shares(n: number | null | undefined): string {
  if (n == null) return '--';
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}bn`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(1)}m`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(0)}k`;
  return `${sign}${abs.toFixed(0)}`;
}

function money(n: number | null | undefined): string {
  if (!n) return '';
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}bn`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}m`;
  return `$${n.toFixed(0)}`;
}

/** "2026-Q2" -> "30 June 2026", which is what the numbers actually describe. */
function quarterEnd(quarter: string | null): string {
  const match = /(\d{4})-Q([1-4])/.exec(quarter || '');
  if (!match) return 'the last reported quarter end';
  const [, year, q] = match;
  return ['31 March', '30 June', '30 September', '31 December'][Number(q) - 1]
    + ' ' + year;
}

function shortDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-US',
    { month: 'short', day: 'numeric' });
}

/** Filings read today, before the SEC's quarterly dataset catches up. */
function JustFiled({ symbol }: { symbol: string }) {
  const live = useApi<any>((s) => api2.fundsLive(symbol, s), [symbol]).data;
  const rows = (live?.filings || []).slice(0, 5);
  if (!rows.length) return null;
  return (
    <div className="fa-live">
      <h5>Filed in the last few days</h5>
      <p className="fa-age-line">
        Read from EDGAR within a minute of filing — but the filing itself
        still describes the quarter end, up to 45 days earlier.
      </p>
      {rows.map((f: any) => {
        const p = f.positions[0];
        const change = p.share_change as number | null;
        return (
          <div className="fa-row" key={f.url}>
            <span className="fa-fund">{f.fund}</span>
            <span className={`fa-chg ${change == null ? '' : change > 0 ? 'pos' : 'neg'}`}>
              {p.action === 'NEW POSITION' ? 'new position'
                : change == null ? p.action.toLowerCase()
                  : `${change > 0 ? '+' : ''}${shares(change)}`}
            </span>
            <span className="fa-val">{shortDate(f.filed_at)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Daily, from the sector ETFs -- the fastest ownership data there is. */
function DailyEtf({ symbol }: { symbol: string }) {
  const d = useApi<any>((s) => api2.fundsDaily(symbol, s), [symbol]).data;
  if (!d || d.status !== 'OK' || !d.days?.length) return null;
  const days = d.days.filter((x: any) => x.share_change != null).slice(-6);
  if (!days.length) return null;
  return (
    <div className="fa-live">
      <h5>ETF holdings, daily · {d.trend}</h5>
      <p className="fa-age-line">
        The fastest ownership data there is: sector ETFs publish their whole
        book every evening, so these are yesterday's real purchases.
      </p>
      {days.map((x: any) => (
        <div className="fa-row" key={x.date}>
          <span className="fa-fund">{x.date}</span>
          <span className={`fa-chg ${x.share_change > 0 ? 'pos' : x.share_change < 0 ? 'neg' : ''}`}>
            {x.share_change > 0 ? '+' : ''}{shares(x.share_change)}
          </span>
          <span className="fa-val">{shares(x.shares)} held</span>
        </div>
      ))}
    </div>
  );
}

/** Monthly, from Form N-PORT: every mutual fund and ETF, three per quarter. */
function MonthlyFunds({ symbol }: { symbol: string }) {
  const d = useApi<any>((s) => api2.fundsMonthly(symbol, s), [symbol]).data;
  if (!d || d.status !== 'OK' || !d.months?.length) return null;
  return (
    <div className="fa-live">
      <h5>All funds, monthly · {d.trend}</h5>
      <p className="fa-age-line">
        Every mutual fund and ETF reports monthly on Form N-PORT, published
        about <b>60 days</b> after the quarter. Each fund is compared with its
        own previous report.
      </p>
      {d.months.map((m: any) => (
        <div className="fa-row" key={m.month}>
          <span className="fa-fund">{m.month} · {m.funds} reported</span>
          <span className={`fa-chg ${m.funds_added > m.funds_trimmed ? 'pos'
            : m.funds_trimmed > m.funds_added ? 'neg' : ''}`}>
            {m.funds_compared
              ? `${m.funds_added} added / ${m.funds_trimmed} cut`
              : 'first report'}
          </span>
          <span className="fa-val">
            {m.added_share_pct == null ? '--' : `${m.added_share_pct}% adding`}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function FundActivity({ symbol }: { symbol: string }) {
  const held = useApi<any>((s) => api2.institutional(symbol, s), [symbol]);
  const d = held.data;

  if (held.initialLoading) {
    return <Panel title="Fund Buying & Selling"><p className="ce-note">Reading 13F filings…</p></Panel>;
  }
  if (!d || d.status !== 'OK') {
    return (
      <Panel title="Fund Buying & Selling">
        <p className="ce-note">
          {d?.detail || 'No 13F holdings on record for this symbol.'}
        </p>
      </Panel>
    );
  }

  const net = d.net_share_change_pct as number | null;
  const tone = net == null ? 'flat' : net > 0.5 ? 'buy' : net < -0.5 ? 'sell' : 'flat';

  return (
    <Panel title="Fund Buying & Selling" noBody
      right={<span className="ce-count">{d.latest_quarter} vs {d.previous_quarter}</span>}>
      <div className="fa">
        {/* Said before the numbers, not after them. A reader who takes these
            for today's positioning is reading a different signal from the one
            on offer, and by the time they reach a footnote they have already
            drawn the conclusion. */}
        <p className="fa-age">
          <Clock size={12} />
          <span>
            <b>Not live.</b> Funds report holdings as of the quarter end and
            have <b>45 days</b> to file, so the figures below describe what was
            owned on {quarterEnd(d.latest_quarter)} — not today. There is no
            faster source: the law requires disclosure only after the fact.
          </span>
        </p>

        <div className="fa-stats">
          <div className="fa-stat">
            <b>{d.funds_increasing}</b>
            <span>funds added</span>
          </div>
          <div className="fa-stat">
            <b>{d.funds_decreasing}</b>
            <span>funds trimmed</span>
          </div>
          <div className="fa-stat">
            <b>{d.new_positions}</b>
            <span>opened new</span>
          </div>
          <div className="fa-stat">
            <b>{d.closed_positions}</b>
            <span>sold out</span>
          </div>
          <div className={`fa-stat fa-${tone}`}>
            <b>{net == null ? '--' : `${net > 0 ? '+' : ''}${net.toFixed(1)}%`}</b>
            <span>net shares held</span>
          </div>
        </div>

        <div className="fa-cols">
          <div>
            <h5><TrendingUp size={12} /> Biggest buyers</h5>
            {(d.top_buyers || []).slice(0, 6).map((f: any) => (
              <div className="fa-row" key={f.cik + f.fund}>
                <span className="fa-fund">{f.fund}</span>
                <span className="fa-chg pos">
                  +{shares(f.share_change)}
                  {f.state === 'NEW POSITION' ? ' · new' : ''}
                </span>
                <span className="fa-val">{money(f.value)}</span>
              </div>
            ))}
          </div>
          <div>
            <h5><TrendingDown size={12} /> Biggest sellers</h5>
            {(d.top_sellers || []).slice(0, 6).map((f: any) => (
              <div className="fa-row" key={f.cik + f.fund}>
                <span className="fa-fund">{f.fund}</span>
                <span className="fa-chg neg">
                  {shares(f.share_change)}
                  {f.state === 'CLOSED' ? ' · closed' : ''}
                </span>
                <span className="fa-val">{money(f.value)}</span>
              </div>
            ))}
          </div>
        </div>

        <DailyEtf symbol={symbol} />

        <MonthlyFunds symbol={symbol} />

        <JustFiled symbol={symbol} />

        <p className="ce-foot">
          {d.total_funds} funds hold {symbol}.
          {d.family_transfers ? ` ${d.family_transfers} position moves within a `
            + 'fund family were treated as paperwork, not buying or selling.' : ''}
          {' '}{d.staleness_note} Source: SEC Form 13F.
        </p>
      </div>
    </Panel>
  );
}
