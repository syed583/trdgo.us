import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, RefreshCw, Search } from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import { compact, money, signedPct, tone } from '../lib/format';
import { ErrorState, Loading, PageHead, Th, Unavailable, useSort } from './shared';

export default function ScannerPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();

  const [preset, setPreset] = useState('MOST_ACTIVE');
  const [minPrice, setMinPrice] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [minVolume, setMinVolume] = useState('');
  const [filter, setFilter] = useState('');
  const [technicals, setTechnicals] = useState(false);
  const [minRsi, setMinRsi] = useState('');
  const [maxRsi, setMaxRsi] = useState('');
  const [emaFilter, setEmaFilter] = useState('any');
  const [applied, setApplied] = useState(0);

  const presets = useApi<any>((s) => api2.scannerPresets(s), []);

  const params = useMemo(() => {
    const p: Record<string, string | number> = { preset, limit: 25 };
    if (minPrice) p.above_price = Number(minPrice);
    if (maxPrice) p.below_price = Number(maxPrice);
    if (minVolume) p.above_volume = Number(minVolume);
    if (technicals) p.technicals = 1;
    return p;
    // `applied` forces a re-run only when Run is pressed, so typing in a
    // filter box never fires a scan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, applied, technicals]);

  const scan = useApi<any>((s) => api2.scannerRun(params, s), [params]);
  const rows: any[] = scan.data?.rows || [];

  const visible = useMemo(() => {
    const q = filter.trim().toUpperCase();
    let out = q ? rows.filter((r) => r.symbol.includes(q)) : rows;

    // Technical filters only apply to rows that actually carry technicals;
    // an unsampled row is hidden rather than silently treated as passing.
    if (minRsi) out = out.filter((r) => r.rsi != null && r.rsi >= Number(minRsi));
    if (maxRsi) out = out.filter((r) => r.rsi != null && r.rsi <= Number(maxRsi));
    if (emaFilter !== 'any') {
      const key = `above_${emaFilter}`;
      out = out.filter((r) => r[key] === true);
    }
    return out;
  }, [rows, filter, minRsi, maxRsi, emaFilter]);

  const { sorted, sort, dir, toggle } = useSort(visible, 'rank');

  const add = async (symbol: string) => {
    await api2.watchlistAdd(symbol).catch(() => undefined);
    ctx.strip.refresh();
  };

  return (
    <div className="page">
      <PageHead
        title="Market Scanner"
        subtitle={
          <>
            Scans run inside TWS and return a ranked list;
            quotes are attached to the visible rows only, so a scan never
            approaches the market-data line limit.
          </>
        }
        right={
          <button className="ghost-btn" onClick={() => setApplied((n) => n + 1)}>
            <RefreshCw size={12} className={scan.loading ? 'spin' : undefined} />
            Run scan
          </button>
        }
      />

      <Panel title="Filters" noBody>
        <div className="filter-bar" style={{ borderBottom: 0 }}>
          <label>
            Scan
            <select className="mini" value={preset}
              onChange={(e) => { setPreset(e.target.value); setApplied((n) => n + 1); }}>
              {(presets.data?.presets || []).map((p: any) => (
                <option key={p.key} value={p.key} disabled={!p.available}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label>Min price
            <input type="number" value={minPrice} min={0}
              onChange={(e) => setMinPrice(e.target.value)} />
          </label>
          <label>Max price
            <input type="number" value={maxPrice} min={0}
              onChange={(e) => setMaxPrice(e.target.value)} />
          </label>
          <label>Min volume
            <input type="number" value={minVolume} min={0} step={100000}
              onChange={(e) => setMinVolume(e.target.value)} />
          </label>
          <label>Ticker
            <input type="text" value={filter} placeholder="Filter"
              onChange={(e) => setFilter(e.target.value.toUpperCase())} />
          </label>
          <label title="Fetches bars for the visible rows to compute RSI, EMA posture and relative volume">
            Technicals
            <button type="button"
              className={`toggle ${technicals ? 'on' : ''}`}
              onClick={() => { setTechnicals((t) => !t); setApplied((n) => n + 1); }}
              aria-pressed={technicals}>
              <span />
            </button>
          </label>
          {technicals && (
            <>
              <label>RSI min
                <input type="number" value={minRsi} min={0} max={100}
                  onChange={(e) => setMinRsi(e.target.value)} />
              </label>
              <label>RSI max
                <input type="number" value={maxRsi} min={0} max={100}
                  onChange={(e) => setMaxRsi(e.target.value)} />
              </label>
              <label>Above
                <select className="mini" value={emaFilter}
                  onChange={(e) => setEmaFilter(e.target.value)}>
                  <option value="any">Any EMA</option>
                  <option value="ema20">EMA 20</option>
                  <option value="ema50">EMA 50</option>
                  <option value="ema200">EMA 200</option>
                </select>
              </label>
            </>
          )}
          <button className="ghost-btn on" onClick={() => setApplied((n) => n + 1)}>
            <Search size={11} /> Apply
          </button>
          <button className="ghost-btn" onClick={() => {
            setMinPrice(''); setMaxPrice(''); setMinVolume(''); setFilter('');
            setMinRsi(''); setMaxRsi(''); setEmaFilter('any');
            setApplied((n) => n + 1);
          }}>Reset</button>
        </div>
      </Panel>

      <Panel
        title={scan.data?.label || 'Results'}
        noBody
        right={<span className="hint" style={{ padding: 0 }}>
          {sorted.length} rows{scan.data?.enriched ? ` · ${scan.data.enriched} quoted` : ''}
        </span>}
      >
        {scan.error ? <ErrorState error={scan.error} />
          : scan.initialLoading ? <Loading label="Running scan in TWS…" />
            : !sorted.length
              ? <Unavailable status={scan.data?.status} detail={scan.data?.detail} />
              : (
                <>
                  <div className="tbl-scroll" style={{ maxHeight: 560 }}>
                    <table className="tbl">
                      <thead>
                        <tr>
                          <Th label="#" field="rank" sort={sort} dir={dir} onSort={toggle} />
                          <Th label="Ticker" field="symbol" sort={sort} dir={dir} onSort={toggle} />
                          <Th label="Exchange" />
                          <Th label="Price" field="price" sort={sort} dir={dir} onSort={toggle} align="r" />
                          <Th label="Change %" field="change_percent" sort={sort} dir={dir} onSort={toggle} align="r" />
                          <Th label="Volume" field="volume" sort={sort} dir={dir} onSort={toggle} align="r" />
                          <Th label="Bid" align="r" />
                          <Th label="Ask" align="r" />
                          {technicals && (
                            <>
                              <Th label="RSI" field="rsi" sort={sort} dir={dir} onSort={toggle} align="r" />
                              <Th label="RVol" field="relative_volume" sort={sort} dir={dir} onSort={toggle} align="r" />
                              <Th label="EMA 20/50/200" />
                              <Th label="Trend" />
                            </>
                          )}
                          <Th label="" />
                        </tr>
                      </thead>
                      <tbody>
                        {sorted.map((r: any) => (
                          <tr key={r.con_id} className="clickable"
                            onClick={() => navigate(`/earnings/${r.symbol}${ctx.search}`)}>
                            <td className="num">{r.rank + 1}</td>
                            <td><b>{r.symbol}</b></td>
                            <td style={{ color: 'var(--text-dim)' }}>{r.exchange}</td>
                            <td className="num r">{money(r.price)}</td>
                            <td className={`num r ${tone(r.change_percent)}`}>
                              {signedPct(r.change_percent)}
                            </td>
                            <td className="num r">{compact(r.volume, 1)}</td>
                            <td className="num r">{money(r.bid)}</td>
                            <td className="num r">{money(r.ask)}</td>
                            {technicals && (
                              <>
                                <td className="num r">{r.rsi ?? '--'}</td>
                                <td className="num r">{r.relative_volume ?? '--'}</td>
                                <td><EmaDots row={r} /></td>
                                <td style={{ color: 'var(--text-dim)' }}>
                                  {r.trend || '--'}
                                </td>
                              </>
                            )}
                            <td>
                              <button
                                className="mini-btn"
                                title="Add to watchlist"
                                onClick={(e) => { e.stopPropagation(); add(r.symbol); }}
                              >
                                <Plus size={11} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {scan.data?.note && <div className="hint">{scan.data.note}</div>}
                </>
              )}
      </Panel>
    </div>
  );
}


function EmaDots({ row }: { row: any }) {
  const dot = (v: boolean | null | undefined, label: string) => (
    <span
      key={label}
      className="ema-dot"
      title={`${label}: ${v == null ? 'unknown' : v ? 'above' : 'below'}`}
      style={{
        background: v == null ? 'var(--gray-bar)'
          : v ? 'var(--green)' : 'var(--red)',
      }}
    />
  );
  return (
    <span className="ema-dots">
      {dot(row.above_ema20, 'EMA 20')}
      {dot(row.above_ema50, 'EMA 50')}
      {dot(row.above_ema200, 'EMA 200')}
    </span>
  );
}
