import React, { useMemo, useState } from 'react';
import { Layers, Moon, UserCheck } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Panel } from './common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { compactMoney, money, num } from '../lib/format';

/**
 * Dark-pool prints, and what insiders are doing across the market.
 *
 * Two readings the app has never carried, kept on their own tabs because
 * neither is an options reading and folding them into the tape would blur
 * what each one measures.
 *
 * The important restraint here: a dark-pool print carries no side. It is a
 * record that size changed hands away from the lit book, reported after the
 * fact. Every other screen in this app refuses to infer a buyer from a
 * price, and this one does too -- the spread position is shown as a
 * position, never relabelled "buying".
 */

function Tile({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'pos' | 'neg';
}) {
  return (
    <div className="dp-tile">
      <span className="dp-tile-label">{label}</span>
      <b className={tone ? `dp-${tone}` : undefined}>{value}</b>
      {sub && <em>{sub}</em>}
    </div>
  );
}

export function DarkPoolTab({ symbol }: { symbol: string }) {
  const recent = useApi<any>((s) => api2.darkpoolRecent(60, s), []);
  const mine = useApi<any>((s) => api2.darkpoolSymbol(symbol, 40, s), [symbol]);
  const levels = useApi<any>((s) => api2.darkpoolLevels(symbol, 12, s), [symbol]);

  const r = recent.data;
  const m = mine.data;
  const l = levels.data;

  return (
    <div className="dp">
      <Panel title="Off-Exchange Prints" icon={<Moon size={13} />} noBody
        right={r?.status === 'OK'
          ? <span className="badge gray">{r.blocks} blocks</span> : undefined}>
        <div className="dp-tiles">
          <Tile label="PRINTS SHOWN" value={r ? num(r.count, 0) : '--'}
            sub="largest first" />
          <Tile label="BLOCK PREMIUM"
            value={r?.block_premium ? compactMoney(r.block_premium, 1) : '--'}
            sub="prints over $1M" />
          <Tile label="TOTAL PRINTED"
            value={r?.total_premium ? compactMoney(r.total_premium, 1) : '--'}
            sub="this window" />
        </div>
        {recent.initialLoading ? (
          <p className="dp-note">Reading the off-exchange tape…</p>
        ) : r?.status !== 'OK' ? (
          <p className="dp-note">{r?.detail || 'No dark-pool prints available.'}</p>
        ) : (
          <div className="dp-table-wrap">
            <table className="dp-table">
              <thead>
                <tr>
                  <th>Time</th><th>Ticker</th><th className="r">Size</th>
                  <th className="r">Price</th><th className="r">Value</th>
                  <th>In spread</th><th className="r">% of day</th>
                </tr>
              </thead>
              <tbody>
                {r.rows.map((p: any, i: number) => (
                  <tr key={`${p.symbol}-${p.time}-${i}`}
                    className={p.block ? 'dp-block' : undefined}>
                    <td className="dp-dim">{p.time_label}</td>
                    <td><b>{p.symbol}</b></td>
                    <td className="r">{num(p.size, 0)}</td>
                    <td className="r">{money(p.price)}</td>
                    <td className="r"><b>{compactMoney(p.premium, 2)}</b></td>
                    <td className="dp-dim">{p.spread_position || '--'}</td>
                    <td className="r dp-dim">
                      {p.share_of_day == null ? '--' : `${p.share_of_day}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="dp-foot">{r?.detail}</p>
      </Panel>

      <div className="dp-two">
        <Panel title={`${symbol} Off-Exchange`} icon={<Moon size={13} />}>
          {mine.initialLoading ? (
            <p className="dp-note">Reading…</p>
          ) : m?.status !== 'OK' ? (
            <p className="dp-note">{m?.detail || 'No prints for this symbol.'}</p>
          ) : (
            <>
              <div className="dp-tiles">
                <Tile label="PRINTS" value={num(m.prints_seen, 0)} />
                <Tile label="SHARES" value={num(m.shares_printed, 0)} />
                <Tile label="OF DAY'S VOLUME"
                  value={m.share_of_day_volume == null
                    ? '--' : `${m.share_of_day_volume}%`}
                  sub="a floor, not the session" />
              </div>
              {m.largest && (
                <p className="dp-largest">
                  Largest: <b>{num(m.largest.size, 0)}</b> shares at{' '}
                  <b>{money(m.largest.price)}</b> —{' '}
                  {compactMoney(m.largest.premium, 2)}, {m.largest.spread_position}
                </p>
              )}
              <p className="dp-foot">{m.detail}</p>
            </>
          )}
        </Panel>

        <Panel title="Heaviest Off-Exchange Levels" icon={<Layers size={13} />}>
          {levels.initialLoading ? (
            <p className="dp-note">Reading…</p>
          ) : l?.status !== 'OK' ? (
            <p className="dp-note">{l?.detail || 'No level data.'}</p>
          ) : (
            <>
              <div className="dp-levels">
                {l.rows.map((row: any) => {
                  const peak = Math.max(
                    ...l.rows.map((x: any) => x.off_exchange_volume || 0), 1);
                  return (
                    <div className="dp-level" key={row.price}>
                      <span className="dp-level-price">{money(row.price)}</span>
                      <span className="dp-level-bar">
                        <i style={{
                          width: `${(row.off_exchange_volume / peak) * 100}%`,
                        }} />
                      </span>
                      <b>{num(row.off_exchange_volume, 0)}</b>
                      <em>{row.off_share == null ? '' : `${row.off_share}% off`}</em>
                    </div>
                  );
                })}
              </div>
              <p className="dp-foot">{l.detail}</p>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}

function MarketInsiderTransactions() {
  const navigate = useNavigate();
  const [byValue, setByValue] = useState(true);
  const [buysOnly, setBuysOnly] = useState(false);
  const txns = useApi<any>((s) => api2.insidersTransactions(150, buysOnly, s), [buysOnly]);
  const t = txns.data;

  const rows = useMemo(() => {
    const r = [...(t?.rows || [])];
    if (!byValue) r.sort((a, b) => (b.date < a.date ? -1 : b.date > a.date ? 1 : 0));
    return r;
  }, [t, byValue]);

  return (
    <Panel title="Largest Insider Transactions" icon={<UserCheck size={13} />}
      right={
        <div className="ins-controls">
          <button className={`toggle ${byValue ? 'on' : ''}`}
            onClick={() => setByValue((v) => !v)}
            title="Sort by dollar size instead of most recent">
            <span /> Largest first
          </button>
          <button className={`mf-chip ${buysOnly ? 'on' : ''}`}
            onClick={() => setBuysOnly((v) => !v)}>Buys only</button>
        </div>
      }>
      {txns.initialLoading ? (
        <p className="dp-note">Reading insider filings…</p>
      ) : t?.status !== 'OK' ? (
        <p className="dp-note">{t?.detail || 'No insider transactions available.'}</p>
      ) : (
        <>
          <div className="dp-table-wrap ins-wrap">
            <table className="dp-table ins-table">
              <thead>
                <tr>
                  <th>Ticker</th><th>Date</th><th>Name</th><th>Type</th>
                  <th className="r">Shares</th><th className="r">Price</th>
                  <th className="r">Amount</th><th>Owner</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r: any, i: number) => (
                  <tr key={`${r.ticker}-${r.name}-${i}`}
                    className={`ins-row ${r.direction}`}>
                    <td>
                      <button className="ins-ticker"
                        onClick={() => navigate(`/earnings/${r.ticker}`)}>
                        {r.ticker}
                      </button>
                    </td>
                    <td className="dp-dim">{r.date}</td>
                    <td title={r.name || ''}>{(r.name || '--').slice(0, 26)}</td>
                    <td className="dp-dim">{r.type}{r.planned ? ' · 10b5-1' : ''}</td>
                    <td className="r">{num(r.shares, 0)}</td>
                    <td className="r">{money(r.price)}</td>
                    <td className="r"><b>{compactMoney(r.value, 2)}</b></td>
                    <td className="dp-dim" title={r.role || ''}>
                      {(r.role || '--').slice(0, 22)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="dp-foot">{t.detail}</p>
        </>
      )}
    </Panel>
  );
}

export function MarketInsidersTab() {
  const market = useApi<any>((s) => api2.insidersMarket(30, s), []);
  const sectors = useApi<any>((s) => api2.insidersSectors(10, s), []);

  const m = market.data;
  const s = sectors.data;

  return (
    <div className="dp">
      <MarketInsiderTransactions />

      <Panel title="Insiders Across the Market" icon={<UserCheck size={13} />}
        right={m?.status === 'OK'
          ? <span className={`badge ${m.lean === 'BUYING' ? 'green' : 'gray'}`}>
            {m.lean}
          </span> : undefined}>
        {market.initialLoading ? (
          <p className="dp-note">Reading insider filings…</p>
        ) : m?.status !== 'OK' ? (
          <p className="dp-note">{m?.detail || 'No insider data available.'}</p>
        ) : (
          <>
            <div className="dp-tiles">
              <Tile label="BUY FILINGS" value={num(m.total_buys, 0)}
                sub={compactMoney(m.buy_value, 1)} tone="pos" />
              <Tile label="SELL FILINGS" value={num(m.total_sells, 0)}
                sub={compactMoney(m.sell_value, 1)} tone="neg" />
              <Tile label="BUY SHARE"
                value={m.buy_share == null ? '--' : `${m.buy_share}%`}
                sub={`over ${m.days} filing days`} />
            </div>
            <div className="dp-table-wrap">
              <table className="dp-table">
                <thead>
                  <tr>
                    <th>Date</th><th className="r">Buys</th>
                    <th className="r">Sells</th><th className="r">Bought</th>
                    <th className="r">Sold</th><th>Lean</th>
                  </tr>
                </thead>
                <tbody>
                  {m.rows.map((row: any) => (
                    <tr key={row.date}>
                      <td className="dp-dim">{row.date}</td>
                      <td className="r dp-pos">{num(row.buys, 0)}</td>
                      <td className="r dp-neg">{num(row.sells, 0)}</td>
                      <td className="r">{compactMoney(row.buy_value, 1)}</td>
                      <td className="r">{compactMoney(row.sell_value, 1)}</td>
                      <td className={row.lean === 'BUYING' ? 'dp-pos' : 'dp-dim'}>
                        {row.lean}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <p className="dp-foot">{m?.detail}</p>
      </Panel>

      <Panel title="Insider Lean by Sector" icon={<Layers size={13} />}>
        {sectors.initialLoading ? (
          <p className="dp-note">Reading…</p>
        ) : s?.status !== 'OK' ? (
          <p className="dp-note">{s?.detail || 'No sector data.'}</p>
        ) : (
          <>
            <div className="dp-levels">
              {s.rows.map((row: any) => (
                <div className="dp-level" key={row.sector}>
                  <span className="dp-level-price dp-sector">{row.sector}</span>
                  <span className="dp-level-bar">
                    <i className={row.lean === 'BUYING' ? 'up' : 'dn'}
                      style={{ width: `${row.buy_share ?? 0}%` }} />
                  </span>
                  <b>{row.buy_share == null ? '--' : `${row.buy_share}%`}</b>
                  <em>{num(row.buy_transactions, 0)} buys · {num(row.sell_transactions, 0)} sells</em>
                </div>
              ))}
            </div>
            <p className="dp-foot">{s.detail}</p>
          </>
        )}
      </Panel>
    </div>
  );
}
