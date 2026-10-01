import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState, Unavailable } from './shared';
import { Panel } from '../components/common';
import { compact, compactMoney, money } from '../lib/format';

/**
 * Covered Stocks: every optionable name the provider tracks, with a live price.
 *
 * The whole list comes from one screener request (price, change, market cap,
 * volume and IV rank are all on the row), so it never fans out into a quote per
 * symbol -- which is what would trip the rate limit. Search and sort are
 * client-side over the one payload.
 */

type SortKey = 'market_cap' | 'change_percent' | 'price' | 'volume' | 'symbol';

function tone(v: number | null | undefined): string {
  if (typeof v !== 'number') return 'var(--text-mute)';
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text-mute)';
}

export default function StocksPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const data = useApi<any>((s) => api2.stocksAll(s), [], { refreshMs: 60000 });
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortKey>('market_cap');
  const [desc, setDesc] = useState(true);

  const rows = useMemo(() => {
    let r: any[] = data.data?.rows || [];
    const needle = query.trim().toUpperCase();
    if (needle) r = r.filter((x) => String(x.symbol).includes(needle)
      || String(x.name || '').toUpperCase().includes(needle));
    const dir = desc ? -1 : 1;
    r = [...r].sort((a, b) => {
      const av = a[sort]; const bv = b[sort];
      if (sort === 'symbol') return dir * String(av).localeCompare(String(bv));
      return dir * (((av ?? -Infinity) as number) - ((bv ?? -Infinity) as number));
    });
    return r;
  }, [data.data, query, sort, desc]);

  const head = (key: SortKey, label: string, cls = '') => (
    <th className={`${cls} sortable ${sort === key ? 'on' : ''}`}
      onClick={() => { if (sort === key) setDesc((d) => !d); else { setSort(key); setDesc(true); } }}>
      {label}{sort === key ? (desc ? ' ▾' : ' ▴') : ''}
    </th>
  );

  return (
    <div className="page">
      <PageHead title="Stocks"
        subtitle="Every optionable name the data provider tracks, with a live price — one request, no per-stock calls." />

      <div className="nd-controls">
        <span className="nd-search">
          <Search size={12} color="var(--text-mute)" />
          <input value={query} placeholder="Search ticker or name"
            onChange={(e) => setQuery(e.target.value)} />
        </span>
        <span className="nd-count">{rows.length} shown{data.data?.count ? ` of ${data.data.count}` : ''}</span>
      </div>

      {data.error ? <ErrorState error={data.error} />
        : data.initialLoading ? <Loading />
          : data.data?.status !== 'OK' ? <Unavailable status={data.data?.status} detail={data.data?.detail} />
            : (
              <Panel title="Covered stocks" noBody>
                <div className="table-wrap" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
                  <table className="tbl">
                    <thead>
                      <tr>
                        {head('symbol', 'Ticker')}
                        {head('price', 'Price', 'r')}
                        {head('change_percent', 'Change', 'r')}
                        {head('market_cap', 'Market cap', 'r')}
                        {head('volume', 'Volume', 'r')}
                        <th className="r">IV rank</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((x) => (
                        <tr key={x.symbol} className="clickable"
                          onClick={() => navigate(`/dashboard/${x.symbol}${ctx.search}`)}>
                          <td>
                            <b className="mf-ticker">{x.symbol}</b>
                            {x.name && x.name !== x.symbol && (
                              <div className="pr-name">{x.name}</div>
                            )}
                          </td>
                          <td className="num r">{x.price != null ? money(x.price) : '--'}</td>
                          <td className="num r" style={{ color: tone(x.change_percent) }}>
                            {x.change_percent != null
                              ? `${x.change_percent > 0 ? '+' : ''}${x.change_percent}%` : '--'}
                          </td>
                          <td className="num r">{x.market_cap != null ? compactMoney(x.market_cap) : '--'}</td>
                          <td className="num r">{x.volume != null ? compact(x.volume) : '--'}</td>
                          <td className="num r">{x.iv_rank != null ? x.iv_rank.toFixed(0) : '--'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="hint">{data.data?.detail}</div>
              </Panel>
            )}
    </div>
  );
}
