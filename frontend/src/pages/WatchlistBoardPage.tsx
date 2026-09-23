import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, BarChart3, Bell, CalendarDays, Download, Plus, RefreshCw,
  Search, Trash2, TrendingDown, TrendingUp, X,
} from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import { compactMoney, money, num, signedPct, tone } from '../lib/format';
import { ErrorState, Loading } from './shared';

/**
 * The watchlist board.
 *
 * Every figure comes from the same payload the table is drawn from, so the
 * summary cards and the rows can never disagree with each other.
 *
 * There is no Trade action here. This application does not place orders, and a
 * button that looks like one -- even disabled -- is an invitation to a mistake
 * on a screen full of live prices.
 */

const TABS = ['Overview', 'Earnings', 'News'] as const;
type Tab = typeof TABS[number];

function volume(value: number | null | undefined): string {
  if (value == null) return '--';
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`;
  return String(Math.round(value));
}

function Stat({ label, value, sub, tone: t = '', icon }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode;
  tone?: string; icon?: React.ReactNode;
}) {
  return (
    <div className="wl-stat">
      <span className="wl-stat-head">
        <span className="wl-stat-label">{label}</span>
        {icon && <span className={`wl-stat-icon ${t}`}>{icon}</span>}
      </span>
      <b className={`wl-stat-value ${t}`}>{value}</b>
      {sub && <span className="wl-stat-sub">{sub}</span>}
    </div>
  );
}

/** Sector split as a ring with a legend, sized by symbol count. */
function SectorRing({ sectors, total }: { sectors: any[]; total: number }) {
  const R = 34;
  const C = 2 * Math.PI * R;
  const palette = ['#2f8fe0', '#21d07a', '#a06be0', '#f0a92b', '#e0563f',
    '#3fb0a8', '#7a8699'];
  let offset = 0;
  const arcs = sectors.map((s, i) => {
    const len = (s.count / (total || 1)) * C;
    const arc = { ...s, len, offset, colour: palette[i % palette.length] };
    offset += len;
    return arc;
  });

  return (
    <div className="wl-ring-wrap">
      <div className="wl-ring">
        <svg viewBox="0 0 90 90" width={104} height={104}>
          <g transform="rotate(-90 45 45)">
            <circle cx="45" cy="45" r={R} fill="none"
              stroke="var(--panel-2)" strokeWidth="11" />
            {arcs.map((a) => a.len > 0 && (
              <circle key={a.sector} cx="45" cy="45" r={R} fill="none"
                stroke={a.colour} strokeWidth="11"
                strokeDasharray={`${a.len} ${C - a.len}`}
                strokeDashoffset={-a.offset} />
            ))}
          </g>
          <text x="45" y="44" textAnchor="middle" className="wl-ring-value">
            {total}
          </text>
          <text x="45" y="55" textAnchor="middle" className="wl-ring-unit">
            symbols
          </text>
        </svg>
      </div>
      <div className="wl-ring-legend">
        {arcs.map((a) => (
          <div className="wl-legend-row" key={a.sector}>
            <em style={{ background: a.colour }} />
            <span>{a.sector}</span>
            <b>{a.percent}%</b>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function WatchlistBoardPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('Overview');
  const [adding, setAdding] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const view = useApi<any>((s) => api2.watchlistView(s), []);
  const d = view.data;

  const rows = useMemo(() => {
    const all: any[] = d?.rows || [];
    const needle = query.trim().toUpperCase();
    if (!needle) return all;
    return all.filter((r) => r.symbol.includes(needle)
      || String(r.company || '').toUpperCase().includes(needle));
  }, [d, query]);

  const selected = useMemo(
    () => (d?.rows || []).find((r: any) => r.symbol === picked) || null,
    [d, picked],
  );

  const brief = useApi<any>(
    (s) => (picked ? api2.earningsBrief(picked, s) : Promise.resolve(null)),
    [picked],
  );
  const news = useApi<any>(
    (s) => (picked && (tab === 'News' || tab === 'Overview')
      ? api2.news(picked, 6, s) : Promise.resolve(null)),
    [picked, tab],
  );

  const add = async () => {
    const symbol = adding.trim().toUpperCase();
    if (!symbol || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api2.watchlistAdd(symbol);
      setAdding('');
      view.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (row: any) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api2.watchlistRemove(row.symbol);
      if (picked === row.symbol) setPicked(null);
      view.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /** CSV of exactly the rows on screen, filter included. */
  const exportCsv = () => {
    const cols = ['symbol', 'company', 'price', 'change', 'change_percent',
      'volume', 'market_cap', 'sector', 'industry'];
    const esc = (v: any) => {
      const text = v == null ? '' : String(v);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const csv = [cols.concat('next_earnings').join(',')]
      .concat(rows.map((r) => cols.map((c) => esc(r[c]))
        .concat(esc(r.earnings?.date)).join(',')))
      .join('\n');
    const url = URL.createObjectURL(
      new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'watchlist.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  if (view.error) {
    return (
      <div className="page">
        <Panel title="Watchlist"><ErrorState error={view.error} /></Panel>
      </div>
    );
  }
  if (view.initialLoading || !d) {
    return (
      <div className="page">
        <Panel title="Watchlist"><Loading /></Panel>
      </div>
    );
  }

  const t = d.totals || {};
  const empty = d.status === 'EMPTY';

  return (
    <div className="page">
      <div className="wl-head">
        <div>
          <h1>My Watchlist</h1>
          <p>Live prices, sector and market cap, and the next scheduled report.</p>
        </div>
        <div className="wl-head-right">
          <span className={`wl-live ${d.market?.is_open ? 'on' : ''}`}>
            <i />{d.market?.label || 'Market'}
          </span>
          <form className="wl-add"
            onSubmit={(e) => { e.preventDefault(); add(); }}>
            <input value={adding} placeholder="Add symbol"
              onChange={(e) => setAdding(e.target.value.toUpperCase())} />
            <button type="submit" disabled={!adding.trim() || busy}>
              <Plus size={12} />Add
            </button>
          </form>
          <button className="ghost-btn" onClick={exportCsv} disabled={!rows.length}>
            <Download size={12} />Export
          </button>
          <button className="ghost-btn" onClick={view.refresh}>
            <RefreshCw size={12} className={view.loading ? 'spin' : undefined} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="wl-error"><AlertTriangle size={12} />{error}</div>
      )}

      <div className="wl-stats">
        <Stat label="Symbols" value={t.symbols ?? 0}
          sub={`${t.priced ?? 0} priced now`} />
        <Stat label="Average move" tone={(t.average_change ?? 0) >= 0 ? 'green' : 'red'}
          value={t.average_change == null ? '--' : signedPct(t.average_change)}
          sub="equal-weight, not a return"
          icon={(t.average_change ?? 0) >= 0
            ? <TrendingUp size={13} /> : <TrendingDown size={13} />} />
        <Stat label="Gainers" tone="green" value={t.gainers ?? 0}
          icon={<TrendingUp size={13} />} />
        <Stat label="Losers" tone="red" value={t.losers ?? 0}
          icon={<TrendingDown size={13} />} />
        <Stat label="Upcoming earnings" value={t.upcoming_earnings ?? 0}
          sub="on record" icon={<CalendarDays size={13} />} />
      </div>

      <div className="wl-split">
        <Panel
          title={`Symbols (${rows.length})`}
          noBody
          right={(
            <span className="wl-search">
              <Search size={11} color="var(--text-mute)" />
              <input value={query} placeholder="Filter"
                onChange={(e) => setQuery(e.target.value)} />
            </span>
          )}
        >
          {empty ? (
            <div className="wl-empty">
              {d.detail} Type a ticker above and press Add.
            </div>
          ) : !rows.length ? (
            <div className="wl-empty">No symbol matches that filter.</div>
          ) : (
            <div className="tbl-scroll" style={{ maxHeight: 520 }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>#</th><th>Symbol</th><th>Name</th>
                    <th className="r">Price</th><th className="r">Change</th>
                    <th className="r">% Change</th><th className="r">Volume</th>
                    <th className="r">Market cap</th><th>Sector</th>
                    <th>Earnings</th><th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.symbol}
                      className={`clickable ${picked === r.symbol ? 'wl-picked' : ''}`}
                      onClick={() => { setPicked(r.symbol); setTab('Overview'); }}>
                      <td className="num wl-dim">{r.rank}</td>
                      <td><b className="wl-sym">{r.symbol}</b></td>
                      <td className="wl-dim">{r.company}</td>
                      <td className="num r">{money(r.price)}</td>
                      <td className={`num r ${tone(r.change)}`}>
                        {r.change == null ? '--'
                          : `${r.change > 0 ? '+' : ''}${num(r.change)}`}
                      </td>
                      <td className={`num r ${tone(r.change_percent)}`}>
                        {signedPct(r.change_percent)}
                      </td>
                      <td className="num r wl-dim">{volume(r.volume)}</td>
                      <td className="num r" title={r.shares_as_of
                        ? `Shares outstanding as of ${r.shares_as_of}` : undefined}>
                        {r.market_cap != null ? compactMoney(r.market_cap) : '--'}
                      </td>
                      <td className="wl-dim" title={r.industry || undefined}>
                        {r.sector || '--'}
                      </td>
                      <td className="num wl-dim">
                        {r.earnings?.date_label || '--'}
                        {r.earnings && r.earnings.confirmed === false && (
                          <i className="wl-est">est</i>
                        )}
                      </td>
                      <td>
                        <button className="wl-remove"
                          title={`Remove ${r.symbol}`}
                          onClick={(e) => { e.stopPropagation(); remove(r); }}>
                          <Trash2 size={12} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="hint">{d.detail}</div>
        </Panel>

        <Panel className="wl-detail" noBody title={undefined}>
          {!selected ? (
            <div className="wl-empty-detail">
              Select a symbol to see its stats, analyst consensus, next report
              and headlines.
            </div>
          ) : (
            <>
              <div className="wl-detail-head">
                <div style={{ minWidth: 0 }}>
                  <b>{selected.symbol}</b>
                  <i>{selected.company}</i>
                </div>
                <button className="wl-close" onClick={() => setPicked(null)}>
                  <X size={14} />
                </button>
              </div>

              <div className="wl-price">
                <b>{money(selected.price)}</b>
                <em className={tone(selected.change_percent)}>
                  {selected.change != null
                    && `${selected.change > 0 ? '+' : ''}${num(selected.change)} `}
                  ({signedPct(selected.change_percent)})
                </em>
              </div>

              <div className="wl-tabs">
                {TABS.map((x) => (
                  <button key={x}
                    className={`wl-tab ${tab === x ? 'active' : ''}`}
                    onClick={() => setTab(x)}>{x}</button>
                ))}
              </div>

              {tab === 'Overview' && (
                <>
                  <div className="wl-kv">
                    <div><span>Market cap</span>
                      <b>{selected.market_cap != null
                        ? compactMoney(selected.market_cap) : '--'}</b></div>
                    <div><span>Volume</span><b>{volume(selected.volume)}</b></div>
                    <div><span>Sector</span>
                      <b style={{ fontSize: 11 }}>{selected.sector || '--'}</b></div>
                    <div><span>Industry</span>
                      <b style={{ fontSize: 10.5 }}>{selected.industry || '--'}</b></div>
                  </div>

                  <div className="wl-sec">
                    <h4>Analyst consensus</h4>
                    {brief.initialLoading ? (
                      <div className="wl-note">Loading coverage…</div>
                    ) : brief.data?.analysts?.status !== 'OK' ? (
                      <div className="wl-note">
                        {brief.data?.analysts?.detail || 'No coverage on record.'}
                      </div>
                    ) : (
                      <>
                        {[['Buy', brief.data.analysts.buy_percent, 'var(--green)'],
                          ['Hold', brief.data.analysts.hold_percent, 'var(--amber)'],
                          ['Sell', brief.data.analysts.sell_percent, 'var(--red)'],
                        ].map(([label, value, colour]: any) => (
                          <div className="wl-bar" key={label}>
                            <span>{label}</span>
                            <span className="wl-bar-track">
                              <i style={{ width: `${value ?? 0}%`, background: colour }} />
                            </span>
                            <b>{value ?? 0}%</b>
                          </div>
                        ))}
                        <div className="wl-note">
                          {brief.data.analysts.lean} · {brief.data.analysts.rated} firms
                          {brief.data.analysts.price_target != null && (
                            <> · target {money(brief.data.analysts.price_target)}</>
                          )}
                        </div>
                      </>
                    )}
                  </div>

                  <div className="wl-sec">
                    <h4>Not shown</h4>
                    <div className="wl-note">
                      P/E, EPS, dividend yield and beta need a fundamentals
                      feed. Every configured provider either rejects the
                      request or gates it behind a plan, so the fields are
                      absent rather than stale.
                    </div>
                  </div>
                </>
              )}

              {tab === 'Earnings' && (
                <div className="wl-sec">
                  <h4>Next report</h4>
                  {!selected.earnings ? (
                    <div className="wl-note">
                      No scheduled report on record for {selected.symbol}.
                    </div>
                  ) : (
                    <>
                      <div className="wl-next">
                        <b>{selected.earnings.date_label}</b>
                        <i>
                          {selected.earnings.time_label
                            || selected.earnings.reporting_time || 'Timing TBD'}
                          {selected.earnings.confirmed === false
                            && ' · provider estimate, not confirmed'}
                        </i>
                      </div>
                      <button className="ghost-btn" style={{ marginTop: 8 }}
                        onClick={() => navigate(`/earnings/${selected.symbol}${ctx.search}`)}>
                        Earnings intelligence
                      </button>
                    </>
                  )}
                </div>
              )}

              {tab === 'News' && (
                <div className="wl-sec">
                  {news.initialLoading ? <div className="wl-note">Loading…</div>
                    : !(news.data?.items || []).length ? (
                      <div className="wl-note">
                        {news.data?.detail || 'No headlines available.'}
                      </div>
                    ) : (
                      <div className="wl-news">
                        {(news.data.items || []).map((n: any, i: number) => (
                          <div className="wl-news-row" key={n.id || i}>
                            <b>{n.headline || n.title}</b>
                            <i>{[n.provider, n.time_label || n.published_label]
                              .filter(Boolean).join(' · ')}</i>
                          </div>
                        ))}
                      </div>
                    )}
                </div>
              )}

              <div className="wl-cta">
                <button className="ghost-btn"
                  onClick={() => navigate(`/ai-insights/${selected.symbol}?run=1`)}>
                  <BarChart3 size={12} />Analyse
                </button>
                <button className="ghost-btn"
                  onClick={() => navigate(`/options-flow/${selected.symbol}`)}>
                  <Bell size={12} />Options flow
                </button>
              </div>
            </>
          )}
        </Panel>
      </div>

      <div className="wl-bottom">
        <Panel title="Sector allocation" icon={<BarChart3 size={13} />}>
          {!d.sectors?.length ? (
            <div className="wl-note">Nothing to group yet.</div>
          ) : (
            <SectorRing sectors={d.sectors} total={t.symbols || 0} />
          )}
          <div className="hint">
            By symbol count, not by position size — a watchlist holds no sizes.
            Sectors are the SIC division each issuer files under.
          </div>
        </Panel>

        <Panel title="Upcoming earnings" icon={<CalendarDays size={13} />} noBody>
          {!d.earnings?.length ? (
            <div className="wl-empty">
              No scheduled reports on record for these symbols.
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr><th>Symbol</th><th>Company</th><th>Date</th><th>Timing</th></tr>
              </thead>
              <tbody>
                {d.earnings.map((e: any) => (
                  <tr key={e.symbol} className="clickable"
                    onClick={() => navigate(`/earnings/${e.symbol}${ctx.search}`)}>
                    <td><b className="wl-sym">{e.symbol}</b></td>
                    <td className="wl-dim">{e.company}</td>
                    <td className="num">
                      {e.date_label}
                      {e.confirmed === false && <i className="wl-est">est</i>}
                    </td>
                    <td className="wl-dim">
                      {e.time_label || e.reporting_time || 'TBD'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}
