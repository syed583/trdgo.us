import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity, ArrowRight, CalendarDays, Layers, PieChart,
  RefreshCw, Search, X,
} from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import EarningsPreview from '../components/EarningsPreview';
import { Panel } from '../components/common';
import { compactMoney, money, signedPct, tone } from '../lib/format';
import {
  ErrorState, Loading, PageHead, StatusChip, Th, Unavailable, useSort,
} from './shared';

const RANGES = [
  { key: 'TODAY', label: 'Today' },
  { key: 'TOMORROW', label: 'Tomorrow' },
  { key: 'THIS_WEEK', label: 'This Week' },
  { key: 'NEXT_WEEK', label: 'Next Week' },
  { key: 'THIS_MONTH', label: 'This Month' },
  { key: 'ALL', label: 'All Upcoming' },
];

const TABS = ['Overview', 'Preview', 'Estimates', 'Historical', 'News'] as const;
type Tab = typeof TABS[number];

/** How far off a report is, phrased the way someone would say it out loud. */
function daysAway(iso: string | null, today: string | undefined): string {
  if (!iso || !today) return '';
  const days = Math.round(
    (Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`))
    / 86400000,
  );
  if (days === 0) return 'today';
  if (days === 1) return 'tomorrow';
  if (days < 0) return `${Math.abs(days)}d ago`;
  return `in ${days}d`;
}

/** Growth against the year-ago quarter. Undefined across zero or a sign flip. */
function growth(now: number | null, before: number | null): number | null {
  if (now == null || before == null || before <= 0) return null;
  return Math.round((now - before) / before * 1000) / 10;
}

function Pct({ value }: { value: number | null }) {
  if (value == null) return <span className="ec-mute">--</span>;
  return (
    <span className={value >= 0 ? 'pos' : 'neg'}>
      {value > 0 ? '+' : ''}{value}%
    </span>
  );
}

/** Delta chips under the header counts. A missing base says so, rather than 0%. */
function Delta({ value, label }: { value: number | null; label: string }) {
  if (value == null) {
    return <span className="ec-delta flat">no {label} baseline</span>;
  }
  const cls = value > 0 ? 'pos' : value < 0 ? 'neg' : 'flat';
  return (
    <span className={`ec-delta ${cls}`}>
      {value > 0 ? '+' : ''}{value}% vs {label}
    </span>
  );
}

function Stat({
  value, label, delta, deltaLabel, variant = '',
}: {
  value: number | null | undefined; label: string;
  delta: number | null; deltaLabel: string; variant?: string;
}) {
  return (
    <div className={`ec-stat ${variant}`}>
      <b>{value ?? '--'}</b>
      <span>{label}</span>
      <Delta value={delta} label={deltaLabel} />
    </div>
  );
}

/**
 * The donut for beat / in line / missed.
 *
 * Three stroked arcs on one circle rather than a chart library: it is three
 * numbers, and recharts' pie would pull in a whole layout pass for them.
 */
function Donut({ beat, inLine, missed }: {
  beat: number; inLine: number; missed: number;
}) {
  const total = beat + inLine + missed;
  const R = 52;
  const C = 2 * Math.PI * R;
  if (!total) return null;

  let offset = 0;
  const arcs = [
    { n: beat, color: 'var(--green)' },
    { n: inLine, color: 'var(--amber)' },
    { n: missed, color: 'var(--red)' },
  ].map((a) => {
    const len = a.n / total * C;
    const seg = { ...a, len, offset };
    offset += len;
    return seg;
  });

  return (
    <svg viewBox="0 0 140 140" width={128} height={128}>
      <g transform="rotate(-90 70 70)">
        <circle cx="70" cy="70" r={R} fill="none"
          stroke="var(--panel-2)" strokeWidth="18" />
        {arcs.map((a, i) => a.len > 0 && (
          <circle key={i} cx="70" cy="70" r={R} fill="none"
            stroke={a.color} strokeWidth="18"
            strokeDasharray={`${a.len} ${C - a.len}`}
            strokeDashoffset={-a.offset} />
        ))}
      </g>
    </svg>
  );
}


/**
 * The analyst consensus as a single score.
 *
 * Buy counts one, hold a half, sell nothing -- so a unanimous buy list scores
 * 100 and a unanimous sell list zero. It is a restatement of the bars beside
 * it, not a second opinion, and the firm count is shown so a 100 from two
 * analysts cannot be mistaken for a 100 from thirty.
 */
function AnalystScore({ a }: { a: any }) {
  if (!a || a.status !== 'OK' || !a.rated) return null;
  const score = Math.round(
    ((a.buy + a.hold * 0.5) / a.rated) * 100);
  const tone = score >= 60 ? 'var(--green)'
    : score <= 40 ? 'var(--red)' : 'var(--amber)';
  const R = 26;
  const C = 2 * Math.PI * R;
  const lit = score / 100 * C;
  return (
    <div className="ec-score">
      <svg viewBox="0 0 70 70" width={64} height={64}>
        <g transform="rotate(-90 35 35)">
          <circle cx="35" cy="35" r={R} fill="none"
            stroke="var(--panel-2)" strokeWidth="6" />
          <circle cx="35" cy="35" r={R} fill="none" stroke={tone}
            strokeWidth="6" strokeLinecap="round"
            strokeDasharray={`${lit} ${C - lit}`} />
        </g>
        <text x="35" y="40" textAnchor="middle" className="ec-score-value">
          {score}
        </text>
      </svg>
      <span>Analyst score</span>
      <b style={{ color: tone }}>{a.lean}</b>
      <i>{a.rated} firms</i>
    </div>
  );
}

/** Buy / hold / sell shares as stacked bars, the way a ratings page reads. */
function AnalystForecast({ a }: { a: any }) {
  if (!a || a.status !== 'OK') {
    return (
      <div className="ec-note">
        {a?.detail || 'No analyst coverage available for this symbol.'}
      </div>
    );
  }
  const rows = [
    { label: 'Buy', pct: a.buy_percent, color: 'var(--green)' },
    { label: 'Hold', pct: a.hold_percent, color: 'var(--amber)' },
    { label: 'Sell', pct: a.sell_percent, color: 'var(--red)' },
  ];
  return (
    <>
      <div className="ec-forecast">
        {rows.map((r) => (
          <div className="ec-forecast-row" key={r.label}>
            <span>{r.label}</span>
            <span className="ec-forecast-bar">
              <i style={{ width: `${r.pct ?? 0}%`, background: r.color }} />
            </span>
            <b>{r.pct ?? 0}%</b>
          </div>
        ))}
      </div>
      <div className="ec-note">
        {a.lean} · {a.rated} firms in the last {a.window_days} days
        {a.price_target != null && (
          <> · consensus target {money(a.price_target)} from {a.price_target_firms} firms</>
        )}
      </div>
    </>
  );
}


/**
 * Sector mix of whatever is on screen, as proportional tiles.
 *
 * Built from the loaded rows rather than a fixed "this week" window: the
 * screen already opens on whichever range has data, and a heatmap of an empty
 * week is a box of nothing.
 */
function Heatmap({ rows }: { rows: any[] }) {
  const tally = useMemo(() => {
    const counts = new Map<string, number>();
    rows.forEach((r) => {
      const key = r.sector || 'Unclassified';
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    const total = rows.length || 1;
    return [...counts.entries()]
      .map(([sector, n]) => ({
        sector, n, pct: Math.round(n / total * 1000) / 10,
      }))
      .sort((a, b) => b.n - a.n);
  }, [rows]);

  if (!tally.length) return <div className="ec-note">Nothing to group.</div>;

  const top = tally[0].n;
  return (
    <div className="ec-heat">
      {tally.map((t, i) => (
        <div className="ec-heat-tile" key={t.sector}
          title={`${t.sector}: ${t.n} companies`}
          style={{
            // Area follows the count, floored so a one-company sector is
            // still readable rather than a sliver with clipped text.
            flexGrow: Math.max(t.n / top, 0.34),
            background: `var(--heat-${Math.min(i, 6)})`,
          }}>
          <b>{t.sector}</b>
          <u>{t.n}</u>
          <i>{t.pct}%</i>
        </div>
      ))}
    </div>
  );
}

/** Reaction sizes as a signed bar per report, oldest first. */
function ImpactChart({ moves }: { moves: any[] }) {
  if (!moves.length) return null;
  const peak = Math.max(...moves.map((m) => Math.abs(m.move)), 1);
  return (
    <>
      <div className="ec-impact-chart">
        {moves.map((m) => {
          const h = Math.abs(m.move) / peak * 46;
          return (
            <span className="ec-spark-col" key={`${m.symbol}-${m.date}`}
              title={`${m.symbol} ${m.date_label}: ${m.move > 0 ? '+' : ''}${m.move}% on ${m.reaction_date}`}>
              {m.move >= 0
                ? <span className="up" style={{ height: h }} />
                : <span className="dn" style={{ height: h }} />}
            </span>
          );
        })}
      </div>
      <div className="ec-spark-axis">
        <span>{moves[0]?.date_label}</span>
        <span>Close-to-close move on the session that priced each report</span>
        <span>{moves[moves.length - 1]?.date_label}</span>
      </div>
    </>
  );
}

export default function CalendarPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const [range, setRange] = useState('THIS_WEEK');
  // Once the operator picks a range it is theirs; the automatic jump below
  // must not overrule it on the next context refresh.
  const chosen = useRef(false);
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [day, setDay] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('Overview');

  const data = useApi<any>(
    (s) => api2.earningsCalendar(range, 'DATE', applied, s),
    [range, applied],
  );
  const context = useApi<any>((s) => api2.earningsCalendarContext(s), []);

  const d = data.data;
  const c = context.data;

  // Reporting clusters into four short seasons, so "this week" is genuinely
  // empty for most of the year and opening there looked like a broken screen.
  // The backend names the narrowest window that actually holds rows.
  useEffect(() => {
    if (!chosen.current && c?.default_range && c.default_range !== range) {
      setRange(c.default_range);
    }
  }, [c?.default_range]);

  const pickRange = (key: string) => {
    chosen.current = true;
    setRange(key);
    setDay(null);
  };

  const source = d?.source;
  const allRows: any[] = d?.rows || [];
  // The day strip filters the loaded range rather than refetching: the week is
  // already in hand, and a round trip to drop six sevenths of it is wasted.
  const rows = day ? allRows.filter((r) => r.date === day) : allRows;
  const { sorted, sort, dir, toggle } = useSort(rows, 'date');

  // Soonest-first regardless of how the table is sorted: this list answers
  // "who is next", so a click on the Company header must not reorder it.
  const nextUp = useMemo(
    () => [...rows]
      .filter((r) => r.date)
      .sort((a, b) => String(a.date).localeCompare(String(b.date)))
      .slice(0, 6),
    [rows],
  );

  const selected = useMemo(
    () => rows.find((r) => r.symbol === picked) || null,
    [rows, picked],
  );

  // Analyst consensus and reported history, fetched only for the open company.
  const brief = useApi<any>(
    (s) => (picked ? api2.earningsBrief(picked, s) : Promise.resolve(null)),
    [picked],
  );
  // Headlines are a tab of their own, so they are not fetched until it opens.
  const news = useApi<any>(
    // Fetched for Overview as well: the reference layout shows the three most
    // recent headlines there, and waiting for a tab switch to load them would
    // leave the panel's busiest section empty on arrival.
    (s) => (picked && (tab === 'News' || tab === 'Overview')
      ? api2.news(picked, 8, s) : Promise.resolve(null)),
    [picked, tab],
  );

  const surpriseHistory = useMemo(() => {
    if (!selected || !c?.surprises?.rows) return [];
    return c.surprises.rows
      .filter((s: any) => s.symbol === selected.symbol)
      .slice(0, 6);
  }, [selected, c]);

  const counts = c?.counts;
  const results = c?.results;
  const surprises = c?.surprises;
  const impact = c?.impact;
  const quarters: any[] = brief.data?.history?.quarters || [];

  return (
    <div className="page">
      <PageHead
        title="Earnings Calendar"
        subtitle={
          source?.source
            ? <>Scheduled US earnings from <b>{source.source}</b> · {source.detail}</>
            : 'Scheduled US earnings events.'
        }
        right={
          <div className="ec-stats">
            <Stat value={counts?.week} label="This Week"
              delta={counts?.week_vs_last_week ?? null} deltaLabel="last week" />
            <Stat value={counts?.month} label="This Month" variant="month"
              delta={counts?.month_vs_last_month ?? null} deltaLabel="last month" />
            <Stat value={counts?.today} label="Today" variant="today"
              delta={counts?.today_vs_yesterday ?? null} deltaLabel="yesterday" />
            <button className="ghost-btn"
              onClick={() => { data.refresh(); context.refresh(); }}>
              <RefreshCw size={12} className={data.loading ? 'spin' : undefined} />
              Refresh
            </button>
          </div>
        }
      />

      <div className="ec-controls">
        {RANGES.map((r) => (
          <button key={r.key}
            className={`ec-pill ${range === r.key ? 'active' : ''}`}
            onClick={() => pickRange(r.key)}>
            {r.label}
          </button>
        ))}
        {c?.week_label && (
          <span className="ec-week-label">
            <CalendarDays size={12} />{c.week_label}
          </span>
        )}
        <div className="ec-right">
          <form className="ec-search"
            onSubmit={(e) => { e.preventDefault(); setApplied(query.trim().toUpperCase()); }}>
            <Search size={12} color="var(--text-mute)" />
            <input value={query} placeholder="Filter by ticker"
              onChange={(e) => setQuery(e.target.value.toUpperCase())} />
          </form>
        </div>
      </div>

      {/* Always the current week, whatever range is loaded: it is a picture of
          the reporting week, not a second range control. */}
      {c?.days && (
        <div className="ec-week">
          {c.days.map((dd: any) => (
            <button key={dd.date}
              className={`ec-day ${day === dd.date ? 'active' : ''} ${dd.is_today ? 'today' : ''}`}
              onClick={() => setDay(day === dd.date ? null : dd.date)}>
              <b>{dd.weekday}</b>
              <i>{dd.label}</i>
              <u className={dd.count ? '' : 'none'}>
                {dd.count ? `${dd.count} earnings` : 'none'}
              </u>
            </button>
          ))}
        </div>
      )}

      <div className="ec-split">
        <Panel
          title={`${d?.range_label || 'Earnings'}${day ? ' · selected day' : ''} (${rows.length})`}
          noBody
          right={source && <StatusChip status={source.status} title={source.detail} />}
        >
          {data.error ? <ErrorState error={data.error} />
            : data.initialLoading ? <Loading />
              : !rows.length ? (
                <Unavailable
                  status={d?.empty_window ? 'NO_DATA' : source?.status}
                  detail={day
                    ? 'No companies report on the selected day. Clear the day to see the whole range.'
                    : (d?.empty_detail || source?.detail)}
                  required={source?.required_provider}
                />
              ) : (
                <div className="tbl-scroll" style={{ maxHeight: 560 }}>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <Th label="Time" field="report_time" sort={sort} dir={dir} onSort={toggle} />
                        <Th label="Symbol" field="symbol" sort={sort} dir={dir} onSort={toggle} />
                        <Th label="Company" field="company" sort={sort} dir={dir} onSort={toggle} />
                        <Th label="EPS est." field="eps_estimate" sort={sort} dir={dir} onSort={toggle} align="r" />
                        <Th label="EPS prev." field="eps_previous" sort={sort} dir={dir} onSort={toggle} align="r" />
                        {/* The figure the old calendar could not carry: how
                            big a move the options market is pricing into
                            this report. */}
                        <Th label="Exp. move" field="expected_move_percent"
                          sort={sort} dir={dir} onSort={toggle} align="r" />
                        <Th label="Revenue est." field="revenue_estimate" sort={sort} dir={dir} onSort={toggle} align="r" />
                        <Th label="Market cap" field="market_cap" sort={sort} dir={dir} onSort={toggle} align="r" />
                        <Th label="Sector" field="sector" sort={sort} dir={dir} onSort={toggle} />
                        <Th label="Importance" field="importance" sort={sort} dir={dir} onSort={toggle} />
                        <Th label="Date" field="date" sort={sort} dir={dir} onSort={toggle} />
                        <Th label="Price" field="price" sort={sort} dir={dir} onSort={toggle} align="r" />
                      </tr>
                    </thead>
                    <tbody>
                      {sorted.map((r: any) => (
                        <tr key={`${r.symbol}-${r.date}`}
                          className={`clickable ec-row ${picked === r.symbol ? 'selected' : ''}`}
                          onClick={() => { setPicked(r.symbol); setTab('Overview'); }}>
                          <td>
                            <span className="ec-time">
                              <b>{r.time_label || '--'}</b>
                              <span className={`badge ${r.reporting_time === 'AMC' ? 'blue'
                                : r.reporting_time === 'BMO' ? 'amber' : 'gray'}`}>
                                {r.reporting_time || 'TBD'}
                              </span>
                            </span>
                          </td>
                          <td>
                            <span className="ec-sym">
                              <span className="ec-avatar">{r.symbol.slice(0, 2)}</span>
                              <b>{r.symbol}</b>
                            </span>
                          </td>
                          <td style={{ color: 'var(--text-dim)' }}>{r.company}</td>
                          <td className={`num r ${r.eps_estimate < 0 ? 'neg' : ''}`}>
                            {r.eps_estimate != null ? money(r.eps_estimate) : '--'}
                          </td>
                          <td className={`num r ec-mute ${r.eps_previous < 0 ? 'neg' : ''}`}>
                            {r.eps_previous != null ? money(r.eps_previous) : '--'}
                          </td>
                          <td className="num r">
                            {r.expected_move_percent != null ? (
                              <b title={r.expected_move != null
                                ? `${money(r.expected_move)} either way`
                                : undefined}>
                                ±{r.expected_move_percent}%
                              </b>
                            ) : <span className="ec-mute">--</span>}
                          </td>
                          <td className="num r">{compactMoney(r.revenue_estimate)}</td>
                          <td className="num r" title={r.market_cap_detail || undefined}>
                            {r.market_cap != null
                              ? compactMoney(r.market_cap)
                              : <span className="ec-mute">--</span>}
                          </td>
                          <td>
                            {r.sector
                              ? <span className="ec-sector" title={r.industry || undefined}>{r.sector}</span>
                              : <span className="ec-mute">--</span>}
                          </td>
                          <td>
                            {r.importance_label ? (
                              <span className={`badge ${r.importance_label === 'High' ? 'red'
                                : r.importance_label === 'Medium' ? 'amber' : 'gray'}`}>
                                {r.importance_label}
                              </span>
                            ) : <span className="ec-mute">--</span>}
                          </td>
                          <td className="num">
                            {r.date_label || '--'}
                            {r.date_confirmed === false && (
                              <i className="ec-est" title="Provider estimate; the company has not confirmed this date.">est</i>
                            )}
                          </td>
                          <td className="num r">
                            {money(r.price)}
                            {r.change_percent != null && (
                              <i className={`ec-chg ${tone(r.change_percent)}`}>
                                {signedPct(r.change_percent)}
                              </i>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

          <div className="hint">
            Sector and market cap come from SEC filings -- SIC division, and
            shares outstanding times the live price, with the share count's
            date on the cell. Options volume and expected move are still not
            shown: each needs one option chain per company.
          </div>
        </Panel>

        <Panel className="ec-detail" noBody title={undefined}>
          {!selected ? (
            <>
              <div className="ec-detail-head">
                <span className="ec-avatar"><CalendarDays size={15} /></span>
                <div style={{ minWidth: 0 }}>
                  <b>Next to report</b>
                  <i>Soonest first · select one for its detail</i>
                </div>
              </div>
              {!nextUp.length ? (
                <div className="ec-empty-detail">
                  {range === 'ALL'
                    ? 'No scheduled earnings on record at all. Sync the calendar provider to fill this in.'
                    : 'Nothing scheduled in this range. Widen it to All Upcoming to see the next companies on record.'}
                </div>
              ) : (
                <div className="ec-next">
                  {nextUp.map((r: any) => (
                    <button className="ec-next-row" key={`${r.symbol}-${r.date}`}
                      onClick={() => { setPicked(r.symbol); setTab('Overview'); }}>
                      <span className="ec-avatar">{r.symbol.slice(0, 2)}</span>
                      <span className="ec-next-body">
                        <b>{r.symbol}</b>
                        <i>{r.company}</i>
                      </span>
                      <span className="ec-next-when">
                        <b>{r.date_label || '--'}</b>
                        <i>{daysAway(r.date, c?.today)}</i>
                      </span>
                      <span className={`badge ${r.reporting_time === 'AMC' ? 'blue'
                        : r.reporting_time === 'BMO' ? 'amber' : 'gray'}`}>
                        {r.reporting_time || 'TBD'}
                      </span>
                    </button>
                  ))}
                </div>
              )}
              <div className="hint" style={{ margin: 0 }}>
                Select a row for its estimates, reported history and news.
              </div>
            </>
          ) : (
            <>
              <div className="ec-detail-head">
                <span className="ec-avatar">{selected.symbol.slice(0, 2)}</span>
                <div style={{ minWidth: 0 }}>
                  <b>{selected.symbol}</b>
                  <i>{selected.company}{selected.exchange ? ` · ${selected.exchange}` : ''}</i>
                </div>
                <button className="ec-detail-close" onClick={() => setPicked(null)}
                  aria-label="Close detail">
                  <X size={14} />
                </button>
              </div>

              <div className="ec-tabs">
                {TABS.map((t) => (
                  <button key={t}
                    className={`ec-tab ${tab === t ? 'active' : ''}`}
                    onClick={() => setTab(t)}>{t}</button>
                ))}
              </div>

              {tab === 'Preview' && (
                <div className="ec-pane">
                  <EarningsPreview symbol={selected.symbol} />
                </div>
              )}

              {tab === 'Overview' && (
                <div className="ec-pane">
                  <div className="ec-kv">
                    <div>
                      <span>Earnings date</span>
                      <b>{selected.date_label || '--'}
                        <small>{selected.time_label || selected.reporting_time || 'TBD'}</small>
                        {selected.date_confirmed === false && (
                          <em className="ec-est-note">Provider estimate, not confirmed by the company.</em>
                        )}</b>
                    </div>
                    <div>
                      <span>Quarter</span>
                      <b>{selected.quarter_label || '--'}</b>
                    </div>
                    <div>
                      <span>EPS estimate</span>
                      <b>{selected.eps_estimate != null ? money(selected.eps_estimate) : '--'}</b>
                    </div>
                    <div>
                      <span>Previous EPS</span>
                      <b>{selected.eps_previous != null ? money(selected.eps_previous) : '--'}
                        <small><Pct value={growth(selected.eps_estimate, selected.eps_previous)} /></small></b>
                    </div>
                    <div>
                      <span>Revenue estimate</span>
                      <b>{compactMoney(selected.revenue_estimate)}</b>
                    </div>
                    <div>
                      <span>Previous revenue</span>
                      <b>{compactMoney(selected.revenue_prior)}
                        <small><Pct value={growth(selected.revenue_estimate, selected.revenue_prior)} /></small></b>
                    </div>
                    <div>
                      <span>Price</span>
                      <b className={tone(selected.change_percent)}>
                        {money(selected.price)}
                        <small>{signedPct(selected.change_percent)}</small>
                      </b>
                    </div>
                    <div>
                      <span>Sector</span>
                      <b style={{ fontSize: 11.5 }}>{selected.sector || '--'}
                        {selected.industry && <em className="ec-sub-note">{selected.industry}</em>}</b>
                    </div>
                    <div>
                      <span>Market cap</span>
                      <b>{selected.market_cap != null ? compactMoney(selected.market_cap) : '--'}
                        {selected.shares_as_of && (
                          <em className="ec-sub-note">
                            {selected.market_cap != null
                              ? `share count as of ${selected.shares_as_of}`
                              : selected.market_cap_detail}
                          </em>
                        )}</b>
                    </div>
                    <div>
                      <span>Importance</span>
                      <b style={{ fontSize: 12 }}>
                        {selected.importance_label || '--'}
                        {selected.importance != null && <small>{selected.importance}/5</small>}
                      </b>
                    </div>
                  </div>

                  <div className="ec-detail-sec">
                    <h4>Analyst forecast</h4>
                    <p className="ec-sec-note">
                      This is the <b>Wall Street analyst consensus</b> — how many
                      firms rate the stock buy, hold or sell, and their price
                      target. It is other analysts' opinion, not this app's own
                      call. For this app's AI directional score, use
                      <b> Analyse</b> below.
                    </p>
                    {brief.initialLoading
                      ? <div className="ec-note">Loading analyst coverage…</div>
                      : (
                        <div className="ec-forecast-row-wrap">
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <AnalystForecast a={brief.data?.analysts} />
                          </div>
                          <AnalystScore a={brief.data?.analysts} />
                        </div>
                      )}
                  </div>
                </div>
              )}

              {tab === 'Overview' && (
                <div className="ec-detail-sec">
                  <h4>
                    Recent news
                    <button className="ec-viewall" onClick={() => setTab('News')}>
                      View all
                    </button>
                  </h4>
                  {!(news.data?.items || []).length ? (
                    <div className="ec-note">
                      {news.data?.detail
                        || 'Open the News tab to load headlines.'}
                    </div>
                  ) : (
                    <div className="ec-news">
                      {(news.data.items || []).slice(0, 3).map((n: any, i: number) => (
                        <div className="ec-news-row" key={n.id || i}>
                          <b>{n.headline || n.title}</b>
                          <i>{[n.provider, n.time_label || n.published_label]
                            .filter(Boolean).join(' · ')}</i>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {tab === 'Estimates' && (
                <div className="ec-pane">
                  <div className="ec-detail-sec">
                    <h4>This quarter vs the year-ago quarter</h4>
                    <div className="ec-hist">
                      <div className="ec-hist-row">
                        <span>EPS</span>
                        <b className="wide">
                          {selected.eps_previous != null ? money(selected.eps_previous) : '--'}
                          {' → '}
                          {selected.eps_estimate != null ? money(selected.eps_estimate) : '--'}
                        </b>
                        <b><Pct value={growth(selected.eps_estimate, selected.eps_previous)} /></b>
                      </div>
                      <div className="ec-hist-row">
                        <span>Revenue</span>
                        <b className="wide">
                          {compactMoney(selected.revenue_prior)}
                          {' → '}
                          {compactMoney(selected.revenue_estimate)}
                        </b>
                        <b><Pct value={growth(selected.revenue_estimate, selected.revenue_prior)} /></b>
                      </div>
                    </div>
                    {selected.eps_previous_basis && (
                      <div className="ec-note">
                        Previous EPS basis: {selected.eps_previous_basis}.
                      </div>
                    )}
                  </div>

                  <div className="ec-detail-sec">
                    <h4>EPS surprise, reported quarters</h4>
                    {!surpriseHistory.length ? (
                      <div className="ec-note">
                        No reported quarter for {selected.symbol} on record yet.
                      </div>
                    ) : (
                      <div className="ec-hist">
                        {surpriseHistory.map((h: any) => {
                          // Bars run from the centre and are capped: one +90%
                          // surprise otherwise flattens every other bar.
                          const w = Math.min(Math.abs(h.surprise), 50) / 50 * 50;
                          const neg = h.surprise < 0;
                          return (
                            <div className="ec-hist-row" key={h.date}>
                              <span>{h.date_label}</span>
                              <span className="ec-hist-bar">
                                <i className={neg ? 'neg' : ''}
                                  style={neg
                                    ? { left: `${50 - w}%`, width: `${w}%` }
                                    : { left: '50%', width: `${w}%` }} />
                              </span>
                              <b className={h.surprise >= 0 ? 'pos' : 'neg'}>
                                {h.surprise > 0 ? '+' : ''}{h.surprise}%
                              </b>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {tab === 'Historical' && (
                <div className="ec-pane">
                  {brief.initialLoading ? <div className="ec-note">Loading reported quarters…</div>
                    : !quarters.length ? (
                      <div className="ec-note">
                        {brief.data?.history?.status === 'OK'
                          ? `No reported quarter on record for ${selected.symbol}.`
                          : (brief.data?.history?.detail
                            || 'Reported quarters are not available for this symbol.')}
                      </div>
                    ) : (
                      <table className="tbl">
                        <thead>
                          <tr>
                            <Th label="Quarter" /><Th label="EPS" align="r" />
                            <Th label="Est." align="r" /><Th label="Surprise" align="r" />
                            <Th label="1D move" align="r" />
                          </tr>
                        </thead>
                        <tbody>
                          {quarters.map((q: any) => (
                            <tr key={q.date}>
                              <td>
                                <b>{q.quarter_label || q.date_label}</b>
                              </td>
                              <td className="num r">
                                {q.eps_actual != null ? money(q.eps_actual) : '--'}
                              </td>
                              <td className="num r ec-mute">
                                {q.eps_estimate != null ? money(q.eps_estimate) : '--'}
                              </td>
                              <td className="num r">
                                <Pct value={q.eps_surprise_percent != null
                                  ? Math.round(q.eps_surprise_percent * 10) / 10 : null} />
                              </td>
                              <td className="num r">
                                <Pct value={q.post_earnings_move_percent ?? null} />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                </div>
              )}

              {tab === 'News' && (
                <div className="ec-pane">
                  {news.initialLoading ? <div className="ec-note">Loading headlines…</div>
                    : !(news.data?.items || []).length ? (
                      <div className="ec-note">
                        {news.data?.detail
                          || `No headlines available for ${selected.symbol}.`}
                      </div>
                    ) : (
                      <div className="ec-news">
                        {(news.data.items || []).map((n: any, i: number) => (
                          <div className="ec-news-row" key={n.id || i}>
                            <b>{n.headline || n.title}</b>
                            <i>{[n.provider, n.time_label || n.published_label || n.time]
                              .filter(Boolean).join(' · ')}</i>
                          </div>
                        ))}
                      </div>
                    )}
                </div>
              )}

              <div className="ec-detail-cta">
                <button className="ghost-btn"
                  onClick={() => navigate(`/earnings/${selected.symbol}${ctx.search}`)}>
                  Earnings intelligence <ArrowRight size={12} />
                </button>
                <button className="ghost-btn"
                  onClick={() => navigate(`/ai-insights/${selected.symbol}?run=1`)}>
                  Analyse
                </button>
              </div>
            </>
          )}
        </Panel>
      </div>

      <div className="ec-bottom">
        <Panel title={`Sector mix (${d?.range_label || 'loaded range'})`}
          icon={<Layers size={13} />}
          right={rows.length
            ? <span className="badge gray">{rows.length} companies</span>
            : undefined}>
          <Heatmap rows={rows} />
          <div className="hint">
            Grouped by the SIC division each company files under, across the
            rows currently loaded -- not a fixed week.
          </div>
        </Panel>

        <Panel title="Earnings statistics" icon={<PieChart size={13} />}
          right={results?.reported
            ? <span className="badge gray">{results.reported} quarters</span>
            : undefined}>
          <div className="ec-figures">
            <div><b>{counts?.month ?? '--'}</b><span>Total this month</span></div>
            <div><b className="blue">{counts?.week ?? '--'}</b><span>This week</span></div>
            <div><b className="amber">{counts?.today ?? '--'}</b><span>Today</span></div>
          </div>
          {!results?.reported ? (
            <div className="ec-note">
              No quarter on record has both an estimate and a reported actual
              yet, so there is no beat rate to show.
            </div>
          ) : (
            <div className="ec-donut">
              <Donut beat={results.beat} inLine={results.in_line} missed={results.missed} />
              <div className="ec-donut-legend">
                <div className="ec-legend-row">
                  <em style={{ background: 'var(--green)' }} />Beat
                  <b>{results.beat_percent}% ({results.beat})</b>
                </div>
                <div className="ec-legend-row">
                  <em style={{ background: 'var(--amber)' }} />In line
                  <b>{results.in_line_percent}% ({results.in_line})</b>
                </div>
                <div className="ec-legend-row">
                  <em style={{ background: 'var(--red)' }} />Missed
                  <b>{results.missed_percent}% ({results.missed})</b>
                </div>
              </div>
            </div>
          )}
          <div className="hint">
            Counted across every reported quarter on record, not the calendar
            month -- reporting clusters into four short seasons, so a
            month-scoped rate is zero of zero most of the year.
          </div>
        </Panel>

        <Panel title="Market impact" icon={<Activity size={13} />}
          right={impact?.samples
            ? <span className="badge gray">{impact.samples} reports</span>
            : undefined}>
          {!impact?.samples ? (
            <div className="ec-note">
              {impact?.detail || 'No reaction has been measured yet.'}
            </div>
          ) : (
            <>
              <div className="ec-figures wide">
                <div>
                  <b>±{impact.avg_abs_move}%</b>
                  <span>Avg reaction</span>
                </div>
                <div>
                  <b className={impact.avg_drift >= 0 ? 'green' : 'red'}>
                    {impact.avg_drift > 0 ? '+' : ''}{impact.avg_drift}%
                  </b>
                  <span>Drift, next {impact.drift_sessions} sessions</span>
                </div>
                <div>
                  <b className="mute">--</b>
                  <span>IV increase</span>
                </div>
              </div>
              <ImpactChart moves={impact.moves || []} />
              <div className="hint">
                {impact.detail} A before-open report is measured on that day's
                close, an after-close report on the next -- the session that
                could actually price it. IV increase needs one option chain per
                company and is not measured.
              </div>
            </>
          )}
        </Panel>

      </div>
    </div>
  );
}
