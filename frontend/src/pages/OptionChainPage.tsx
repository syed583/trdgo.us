import React, { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import Freshness from '../components/Freshness';
import { compact, money, num, pct } from '../lib/format';
import { ErrorState, Loading, PageHead, StatusChip, Unavailable } from './shared';

type View = 'quotes' | 'greeks';

/**
 * The option chain.
 *
 * Rendered as a tab inside the options screen, and standalone for any link
 * still pointing at the old route. ``embedded`` drops the page frame and the
 * title: the screen around it already names the symbol and the section, and
 * repeating both reads as a page inside a page.
 */
export default function OptionChainPage({
  ctx, embedded = false,
}: { ctx: PageContext; embedded?: boolean }) {
  const { symbol } = ctx;
  const [expiry, setExpiry] = useState<string | undefined>(undefined);
  const [view, setView] = useState<View>('quotes');

  // Poll at the pace the source can actually change.
  //
  // This never refreshed at all: the chain was fetched once on arrival, so
  // even when the ladder was being served live it froze at whatever
  // it read first. The cadence comes from the payload's own badge -- a live
  // chain is re-read every ten seconds, a delayed or closed-market copy
  // once a minute, because asking a fifteen-minute-delayed feed every ten
  // seconds only repeats the same numbers. The server shares one build per
  // chain, so a short interval here costs one snapshot, not one per viewer.
  const [live, setLive] = useState(false);
  const data = useApi<any>(
    (s) => api2.optionChain(symbol, expiry, s),
    [symbol, expiry],
    { refreshMs: live ? 5000 : 30000 },
  );
  const d = data.data;
  useEffect(() => {
    setLive(d?.freshness?.kind === 'LIVE');
  }, [d?.freshness?.kind]);

  // How long ago the ladder on screen was read, ticking, so a reader can see
  // the refresh happen rather than trusting that it does.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const age = data.lastUpdated
    ? Math.max(0, Math.round((now - data.lastUpdated.getTime()) / 1000))
    : null;
  const strikes: any[] = d?.strikes || [];

  return (
    <div className={embedded ? 'chain-embed' : 'page'}>
      {!embedded && (
      <PageHead
        title={`Option Chain · ${symbol}`}
        subtitle={
          d?.spot
            ? <>Spot {money(d.spot)} · {d.expiry_label} · {d.dte != null ? `${Math.round(d.dte)} DTE` : ''}</>
            : 'Calls | strike | puts ladder for one expiry.'
        }
        right={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 10, color: 'var(--text-dim)',
              display: 'flex', gap: 5, alignItems: 'center' }}>
              Expiry
              <select
                className="mini"
                value={expiry ?? d?.expiry ?? ''}
                onChange={(e) => setExpiry(e.target.value || undefined)}
              >
                {(d?.expirations || []).map((e: string, i: number) => (
                  <option key={e} value={e}>
                    {d.expiration_labels?.[i] || e}
                  </option>
                ))}
              </select>
            </label>
            <div className="seg">
              <button className={view === 'quotes' ? 'active' : ''}
                onClick={() => setView('quotes')}>Quotes</button>
              <button className={view === 'greeks' ? 'active' : ''}
                onClick={() => setView('greeks')}>Greeks</button>
            </div>
            <button className="ghost-btn" onClick={data.refresh}>
              <RefreshCw size={12} className={data.loading ? 'spin' : undefined} />
            </button>
          </div>
        }
      />
      )}

      <Panel
        title={`Calls / Strike / Puts (${strikes.length} strikes)`}
        noBody
        right={
          <span className="chain-fresh">
            <Freshness stamp={d?.freshness} compact />
            {age != null && (
              <span className="chain-age" title="Seconds since this ladder was read">
                {data.loading ? 'refreshing…' : `updated ${age}s ago`}
              </span>
            )}
            <StatusChip status={d?.status} />
          </span>
        }
      >
        {data.error ? <ErrorState error={data.error} />
          : data.initialLoading ? <Loading label="Loading chain…" />
            : !strikes.length ? <Unavailable status={d?.status} detail={d?.error} />
              : (
                <div className="tbl-scroll" style={{ maxHeight: 620 }}>
                  <table className="tbl chain-tbl">
                    <thead>
                      <tr>
                        <th colSpan={view === 'quotes' ? 6 : 5} className="chain-side calls">CALLS</th>
                        <th className="chain-strike-head">Strike</th>
                        <th colSpan={view === 'quotes' ? 6 : 5} className="chain-side puts">PUTS</th>
                      </tr>
                      <tr>
                        {view === 'quotes' ? (
                          <>
                            <th className="r">OI</th><th className="r">Vol</th>
                            <th className="r" title="Last traded price">Last</th>
                            <th className="r">Bid</th><th className="r">Ask</th><th className="r">IV</th>
                          </>
                        ) : (
                          <>
                            <th className="r">Delta</th><th className="r">Gamma</th>
                            <th className="r">Vega</th><th className="r">Theta</th><th className="r">IV</th>
                          </>
                        )}
                        <th />
                        {view === 'quotes' ? (
                          <>
                            <th className="r">IV</th><th className="r">Bid</th><th className="r">Ask</th>
                            <th className="r" title="Last traded price">Last</th>
                            <th className="r">Vol</th><th className="r">OI</th>
                          </>
                        ) : (
                          <>
                            <th className="r">IV</th><th className="r">Delta</th>
                            <th className="r">Gamma</th><th className="r">Vega</th><th className="r">Theta</th>
                          </>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {strikes.map((row) => (
                        <tr key={row.strike} className={row.atm ? 'atm-row' : ''}>
                          {view === 'quotes' ? (
                            <>
                              <Cell v={compact(row.call?.open_interest, 0)} itm={row.call?.itm} />
                              <Tick key="q-cv" v={compact(row.call?.volume, 0)} raw={row.call?.volume} itm={row.call?.itm} />
                              <Tick key="q-cl" v={quote(row.call?.last)} raw={row.call?.last} itm={row.call?.itm} strong />
                              <Tick key="q-cb" v={quote(row.call?.bid)} raw={row.call?.bid} itm={row.call?.itm} />
                              <Tick key="q-ca" v={quote(row.call?.ask)} raw={row.call?.ask} itm={row.call?.itm} />
                              <Cell v={pct(row.call?.iv, 1)} itm={row.call?.itm} />
                            </>
                          ) : (
                            <>
                              <Cell v={num(row.call?.delta, 3)} itm={row.call?.itm} />
                              <Cell v={num(row.call?.gamma, 4)} itm={row.call?.itm} />
                              <Cell v={num(row.call?.vega, 3)} itm={row.call?.itm} />
                              <Cell v={num(row.call?.theta, 3)} itm={row.call?.itm} />
                              <Cell v={pct(row.call?.iv, 1)} itm={row.call?.itm} />
                            </>
                          )}

                          <td className="chain-strike">{row.strike}</td>

                          {view === 'quotes' ? (
                            <>
                              <Cell v={pct(row.put?.iv, 1)} itm={row.put?.itm} />
                              <Tick key="q-pb" v={quote(row.put?.bid)} raw={row.put?.bid} itm={row.put?.itm} />
                              <Tick key="q-pa" v={quote(row.put?.ask)} raw={row.put?.ask} itm={row.put?.itm} />
                              <Tick key="q-pl" v={quote(row.put?.last)} raw={row.put?.last} itm={row.put?.itm} strong />
                              <Tick key="q-pv" v={compact(row.put?.volume, 0)} raw={row.put?.volume} itm={row.put?.itm} />
                              <Cell v={compact(row.put?.open_interest, 0)} itm={row.put?.itm} />
                            </>
                          ) : (
                            <>
                              <Cell v={pct(row.put?.iv, 1)} itm={row.put?.itm} />
                              <Cell v={num(row.put?.delta, 3)} itm={row.put?.itm} />
                              <Cell v={num(row.put?.gamma, 4)} itm={row.put?.itm} />
                              <Cell v={num(row.put?.vega, 3)} itm={row.put?.itm} />
                              <Cell v={num(row.put?.theta, 3)} itm={row.put?.itm} />
                            </>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

        {d?.greeks_note && <div className="hint">{d.greeks_note}</div>}
      </Panel>
    </div>
  );
}

/**
 * A bid or ask, where zero means "nobody is quoting" rather than "worth
 * nothing". Outside market hours every quote on the ladder is 0.0, and
 * printing that as $0.00 on every strike read as a chain of worthless
 * options -- while the real traded prices sat in a column that was never
 * shown.
 */
function quote(value: number | null | undefined): string {
  return value && value > 0 ? money(value) : '--';
}

function Cell({ v, itm }: { v: string; itm?: boolean | null }) {
  return <td className={`num r ${itm ? 'itm' : ''}`}>{v}</td>;
}

/**
 * A cell that flashes when its value moves.
 *
 * The ladder refreshes on a timer, and a table whose numbers change silently
 * between refreshes does not look like it is moving -- a reader has to compare
 * two snapshots from memory to see anything happen. Each price remembers what
 * it showed last and flashes green for an uptick, red for a downtick, the way
 * a trading screen does. It compares the raw number, not the formatted text,
 * so a change smaller than the display rounding still counts and a reformat
 * alone never does.
 */
function Tick({ v, raw, itm, strong }: {
  v: string; raw?: number | null; itm?: boolean | null; strong?: boolean;
}) {
  const prev = useRef<number | null | undefined>(raw);
  const [dir, setDir] = useState<'up' | 'down' | null>(null);

  useEffect(() => {
    const before = prev.current;
    prev.current = raw;
    if (raw == null || before == null || raw === before) return undefined;
    setDir(raw > before ? 'up' : 'down');
    const t = setTimeout(() => setDir(null), 1600);
    return () => clearTimeout(t);
  }, [raw]);

  return (
    <td className={`num r ${itm ? 'itm' : ''} ${strong ? 'chain-last' : ''} ${dir ? `tick-${dir}` : ''}`}>
      {dir === 'up' ? '▲ ' : dir === 'down' ? '▼ ' : ''}{v}
    </td>
  );
}
