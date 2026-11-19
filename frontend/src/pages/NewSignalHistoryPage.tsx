import React from 'react';
import { NavLink } from 'react-router-dom';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, History, Minus, Search, Target } from 'lucide-react';
import {
  Bar, BarChart, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import signalsHero from '../assets/analysis-hero.webp';
import '../new-strategy.css';
import '../new-signals.css';

/**
 * Signal History in the redesigned shell (Figma frame 8:6).
 *
 * THE FRAME'S COLUMNS MEAN SOMETHING ELSE. It shows D-5..D-1 - the five daily moves BEFORE a report.
 * This endpoint returns, per symbol, its real PAST EARNINGS EVENTS: each cell is one previous report and
 * the move that followed it. Drawing ours under the frame's headings would mislabel the data, so the
 * columns are headed with the event's own date.
 *
 * WHY THERE IS NO WIN RATE HERE. The frame's right rail - win rate, signal performance, average return
 * by signal, win-rate trend, top performers - all need a past SIGNAL paired with its OUTCOME. These
 * cells are outcomes only: what the stock did, not what was called beforehand. Pairing them would mean
 * inventing the historical call. That comparison is what the Forward Scoreboard exists for, and
 * per-model accuracy is administrator-only anyway (B.1a), so it belongs there, not here.
 *
 * THE WIN RATE BOX comes from the FORWARD SCOREBOARD, not from this grid, and is labelled as such.
 * The owner asked for the frame's box; this endpoint cannot fill it, because a win rate needs a call
 * recorded BEFORE the event and these cells are outcomes only. The scoreboard measures exactly that,
 * over calls that were captured before each announcement, so the box shows a real figure instead of
 * one invented from outcomes. The frame's win-rate TREND is still absent: the scoreboard reports one
 * figure over its window, not a curve.
 *
 * What this page can honestly say is how each name has BEHAVED after its reports, and what is called for
 * it now - which is the question a reader brings to it.
 */

type Any = any;

const movePct = (v: Any): string =>
  (v === null || v === undefined || Number.isNaN(Number(v))
    ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`);

const dmy = (s: Any): string => {
  if (!s) return '';
  try {
    return new Date(`${String(s).slice(0, 10)}T12:00:00`)
      .toLocaleDateString(undefined, { day: '2-digit', month: 'short' });
  } catch { return String(s).slice(0, 10); }
};

const TONE: Record<string, string> = { BUY: 'win', SELL: 'loss', NEUTRAL: 'flat' };
const ICON: Record<string, React.ReactNode> = {
  BUY: <ArrowUpRight size={12} />, SELL: <ArrowDownRight size={12} />, NEUTRAL: <Minus size={12} />,
};

export default function NewSignalHistoryPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [find, setFind] = React.useState('');
  const [lookup, setLookup] = React.useState('');
  const [only, setOnly] = React.useState<'all' | 'BUY' | 'SELL' | 'NEUTRAL'>('all');

  const grid = useApi<Any>((s) => api2.earningsSignalHistory(8, s), [], { refreshMs: 300_000 });
  // The win rate the frame asks for, from the one place that measures it over recorded calls.
  // Database-backed, not a provider call, so it adds no request budget.
  const board = useApi<Any>((s) => api2.forwardScoreboard(undefined, s), [], { refreshMs: 600_000 });
  const one = useApi<Any>(
    (s) => (lookup ? api2.earningsSignalHistory(8, s, lookup) : Promise.resolve(null)),
    [lookup],
  );

  const d = (lookup && one.data?.status === 'OK') ? one.data : (grid.data || {});
  const rows: Any[] = d.rows || [];
  const n: number = d.n || 8;

  const shown = React.useMemo(() => {
    let r = rows;
    if (only !== 'all') r = r.filter((x) => String(x.now || '') === only);
    const f = find.trim().toUpperCase();
    if (f) r = r.filter((x) => String(x.symbol || '').includes(f));
    return r;
  }, [rows, only, find]);

  // Counts of what is called NOW. A name with no current call is not folded into any bucket.
  const calls = React.useMemo(() => {
    const c: Record<string, number> = { BUY: 0, SELL: 0, NEUTRAL: 0 };
    rows.forEach((r) => { const k = String(r.now || ''); if (k in c) c[k] += 1; });
    return c;
  }, [rows]);
  const called = calls.BUY + calls.SELL + calls.NEUTRAL;

  // How these names have behaved after past reports - outcomes, not calls.
  const allMoves = React.useMemo(
    () => rows.flatMap((r: Any) => (r.cells || [])
      .map((c: Any) => Number(c?.move_pct))
      .filter((v: number) => Number.isFinite(v))),
    [rows],
  );
  const ups = allMoves.filter((v) => v > 0).length;
  const downs = allMoves.filter((v) => v < 0).length;
  const avgAbs = allMoves.length
    ? allMoves.reduce((a, b) => a + Math.abs(b), 0) / allMoves.length : null;

  const winRate: number | null = board.data?.overall?.cost?.win_rate ?? null;
  const winN: number | null = board.data?.overall?.cost?.n ?? null;

  // Average move after a report, split by what actually happened - outcomes, which is what we hold.
  const upMoves = allMoves.filter((v) => v > 0);
  const downMoves = allMoves.filter((v) => v < 0);
  const avgUp = upMoves.length ? upMoves.reduce((a, b) => a + b, 0) / upMoves.length : null;
  const avgDown = downMoves.length ? downMoves.reduce((a, b) => a + b, 0) / downMoves.length : null;

  const callPie = (['BUY', 'NEUTRAL', 'SELL'] as const)
    .map((k) => ({ name: k, value: calls[k] }))
    .filter((x) => x.value > 0);
  const PIE_FILL: Record<string, string> = {
    BUY: 'var(--nu-up)', SELL: 'var(--nu-down)', NEUTRAL: 'var(--nu-accent-2)',
  };

  const reactionMix = [
    { name: 'Rose', value: ups, fill: 'var(--nu-up)' },
    { name: 'Fell', value: downs, fill: 'var(--nu-down)' },
    { name: 'Flat', value: Math.max(0, allMoves.length - ups - downs), fill: 'var(--nu-ink-dim)' },
  ];

  const avgByCall = (['BUY', 'NEUTRAL', 'SELL'] as const).map((k) => {
    const mv = rows.filter((r: Any) => String(r.now || '') === k)
      .flatMap((r: Any) => (r.cells || []).map((c: Any) => Number(c?.move_pct)))
      .filter((v: number) => Number.isFinite(v));
    return { name: k, value: mv.length ? Number((mv.reduce((a, b) => a + b, 0) / mv.length).toFixed(2)) : 0,
      fill: PIE_FILL[k] };
  }).filter((x) => x.value !== 0);

  /** The biggest single reactions on record, newest first within a tie. */
  const topMoves = React.useMemo(() => rows.flatMap((r: Any) => (r.cells || [])
    .filter((c: Any) => Number.isFinite(Number(c?.move_pct)))
    .map((c: Any) => ({ symbol: r.symbol, now: r.now, move: Number(c.move_pct), date: c.date, label: c.label })))
    .sort((a, b) => Math.abs(b.move) - Math.abs(a.move))
    .slice(0, 6), [rows]);

  /** The most recent reports across every name. */
  const recent = React.useMemo(() => rows.flatMap((r: Any) => (r.cells || [])
    .filter((c: Any) => c?.date && Number.isFinite(Number(c?.move_pct)))
    .map((c: Any) => ({ symbol: r.symbol, now: r.now, move: Number(c.move_pct), date: c.date, label: c.label })))
    .sort((a, b) => String(b.date).localeCompare(String(a.date)))
    .slice(0, 6), [rows]);

  const stats = [
    { tone: 'blue', icon: <History size={18} />, value: rows.length ? String(rows.length) : '—',
      label: 'Names covered', sub: `${n} past reports each` },
    // The frame's Win Rate box. Sourced from recorded CALLS (Forward Scoreboard), never from the
    // outcome cells on this page, and the caption says where it comes from.
    { tone: 'violet', icon: <Target size={18} />,
      value: winRate != null ? `${Number(winRate).toFixed(1)}%` : '—',
      label: 'Win rate (recorded calls)',
      sub: winN ? `${winN} calls, after costs — Forward Scoreboard` : 'from the Forward Scoreboard' },
    { tone: 'green', icon: <ArrowUpRight size={18} />,
      value: allMoves.length ? `${Math.round((ups / allMoves.length) * 100)}%` : '—',
      label: 'Rose after the report', sub: allMoves.length ? `${ups} of ${allMoves.length}` : '' },
    { tone: 'red', icon: <ArrowDownRight size={18} />,
      value: allMoves.length ? `${Math.round((downs / allMoves.length) * 100)}%` : '—',
      label: 'Fell after the report', sub: allMoves.length ? `${downs} of ${allMoves.length}` : '' },
    { tone: 'amber', icon: <History size={18} />,
      value: avgAbs != null ? `${avgAbs.toFixed(1)}%` : '—',
      label: 'Average move, either way', sub: 'size, not direction' },
  ];

  const err = lookup ? one.error : grid.error;
  const loading = lookup ? one.loading : grid.loading;

  return (
    <div className="nd nst nua">
      <section className="nd-hero nsh-hero">
        <img className="nd-hero-img" src={signalsHero} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow"><History size={13} /> Earnings reactions</span>
          <h1>Signal <em>History</em></h1>
          <p>
            How each name has actually behaved after its last {n} earnings reports, and what is called
            for it now. The cells are what the stock did — not what was predicted at the time.
          </p>
        </div>
      </section>

      <div className="nd-stats nst-stats">
        {stats.map((s) => (
          <div className="nd-stat" key={s.label}>
            <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
            <div><b>{s.value}</b><span>{s.label}</span>{!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}</div>
          </div>
        ))}
      </div>

      <div className="nua-explain">
        <b>Reading a row.</b> Each cell is one past report and the move that followed it — green up, red
        down. <b>Now</b> is the current call for the next one. Because the cells are outcomes and not past
        calls, this page shows no win rate: judging a model against its own record is the Forward
        Scoreboard&apos;s job, with the calls it actually made.
      </div>

      <div className="nst-filters">
        <div className="nd-tabs">
          {(['all', 'BUY', 'NEUTRAL', 'SELL'] as const).map((k) => (
            <button type="button" key={k} className={only === k ? 'is-on' : ''} onClick={() => setOnly(k)}>
              {k === 'all' ? 'All' : k.charAt(0) + k.slice(1).toLowerCase()}
            </button>
          ))}
        </div>
        <label className="nst-field nst-find">
          <span>Filter the list</span>
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="e.g. NVDA" />
        </label>
        <label className="nst-field nst-find">
          <span>Look up any ticker</span>
          <span className="nsh-lookup">
            <input
              defaultValue=""
              placeholder="any stock, even one not listed"
              onKeyDown={(e) => {
                if (e.key === 'Enter') setLookup((e.target as HTMLInputElement).value.trim().toUpperCase());
              }}
            />
            <Search size={15} />
          </span>
        </label>
        {!!lookup && (
          <button type="button" className="nsh-clear" onClick={() => setLookup('')}>
            showing {lookup} — clear
          </button>
        )}
        <span className="nst-count">{shown.length} of {rows.length}</span>
      </div>

      {/* what is called now, across the listed names */}
      {!!called && !lookup && (
        <div className="nd-stats nua-bstats nsh-calls">
          {(['BUY', 'NEUTRAL', 'SELL'] as const).map((k) => (
            <div className="nd-stat" key={k}>
              <span className={`nd-stat-ico ${k === 'BUY' ? 'green' : k === 'SELL' ? 'red' : 'violet'}`}>
                {ICON[k]}
              </span>
              <div>
                <b>{calls[k]}</b>
                <span>Called {k.toLowerCase()} now</span>
                <i className="nst-stat-sub">{Math.round((calls[k] / called) * 100)}% of {called} called</i>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="nsh-split">
      <section className="nd-card nst-card">
        {loading && !rows.length && <div className="nd-empty">Loading…</div>}
        {!loading && !rows.length && (
          <div className="nd-empty">
            {lookup ? `No past reports found for ${lookup}.` : 'No names in the current window.'}
          </div>
        )}
        {!!rows.length && !shown.length && <div className="nd-empty">Nothing matches this filter.</div>}

        {!!shown.length && (
          <div className="nst-scroll">
            <table className="nd-table nst-table nsh-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  {Array.from({ length: n }).map((_, i) => (
                    <th key={i} className="r">
                      {i === n - 1 ? 'Last report' : `${n - 1 - i} reports ago`}
                    </th>
                  ))}
                  <th>Now</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.symbol}>
                    <td><NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink></td>
                    {(r.cells || []).map((c: Any, i: number) => (
                      <td key={i} className="r">
                        {c?.move_pct == null ? (
                          <span className="nst-mute">—</span>
                        ) : (
                          <>
                            <span className={`nsh-move ${Number(c.move_pct) >= 0 ? 'up' : 'down'}`}>
                              {movePct(c.move_pct)}
                            </span>
                            <div className="nst-sub">{c.label || dmy(c.date)}</div>
                          </>
                        )}
                      </td>
                    ))}
                    <td>
                      {r.now ? (
                        <span className={`nst-status ${TONE[String(r.now)] || 'flat'}`}>
                          {ICON[String(r.now)]}{String(r.now).charAt(0) + String(r.now).slice(1).toLowerCase()}
                        </span>
                      ) : <span className="nst-mute">no call</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ------------------------------------------------------------ right rail */}
      <aside className="nsh-rail">
        <section className="nd-card nsh-panel">
          <header className="nd-card-head"><h3>What is called now</h3></header>
          {callPie.length ? (
            <ResponsiveContainer width="100%" height={176}>
              <PieChart>
                <Pie data={callPie} dataKey="value" nameKey="name" innerRadius={44} outerRadius={70}
                     paddingAngle={2} stroke="none">
                  {callPie.map((e) => <Cell key={e.name} fill={PIE_FILL[e.name]} />)}
                </Pie>
                <Legend verticalAlign="bottom" height={24} iconType="circle"
                        formatter={(v: Any) => <span className="nsh-legend">{v}</span>} />
                <Tooltip formatter={(v: Any, n2: Any) => [`${v} names`, n2]} />
              </PieChart>
            </ResponsiveContainer>
          ) : <div className="nd-empty">No current call on these names.</div>}
        </section>

        <section className="nd-card nsh-panel">
          <header className="nd-card-head"><h3>How reports landed</h3>
            <span className="nst-mute">{allMoves.length} on record</span></header>
          {allMoves.length ? (
            <ResponsiveContainer width="100%" height={158}>
              <BarChart data={reactionMix} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="var(--nu-ink-dim)" tickLine={false} />
                <YAxis tick={{ fontSize: 10 }} stroke="var(--nu-ink-dim)" width={38} tickLine={false} />
                <Tooltip formatter={(v: Any) => [`${v} reports`, '']} />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {reactionMix.map((e) => <Cell key={e.name} fill={e.fill} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="nd-empty">No reactions on record.</div>}
        </section>

        <section className="nd-card nsh-panel">
          <header className="nd-card-head"><h3>Average move after a report</h3></header>
          <div className="nsh-avg">
            <div><span>Rose</span><b className="up">{avgUp != null ? movePct(avgUp) : '—'}</b></div>
            <div><span>Fell</span><b className="down">{avgDown != null ? movePct(avgDown) : '—'}</b></div>
            <div><span>Either way</span><b>{avgAbs != null ? `${avgAbs.toFixed(1)}%` : '—'}</b></div>
          </div>
          {avgByCall.length > 0 && (
            <ResponsiveContainer width="100%" height={132}>
              <BarChart data={avgByCall} margin={{ top: 6, right: 8, left: -24, bottom: 0 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="var(--nu-ink-dim)" tickLine={false} />
                <YAxis tick={{ fontSize: 10 }} stroke="var(--nu-ink-dim)" width={38} tickLine={false} unit="%" />
                <Tooltip formatter={(v: Any) => [`${Number(v).toFixed(2)}%`, 'avg move']} />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {avgByCall.map((e) => <Cell key={e.name} fill={e.fill} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
          <p className="nsh-note">Grouped by what is called for the name NOW, over its past reactions.</p>
        </section>

        <section className="nd-card nsh-panel">
          <header className="nd-card-head"><h3>Biggest reactions</h3></header>
          {topMoves.length ? (
            <ul className="nsh-list">
              {topMoves.map((m, i) => (
                <li key={`${m.symbol}-${i}`}>
                  <span className="nd-sym">{m.symbol}</span>
                  <span className="nst-mute">{m.label || dmy(m.date)}</span>
                  <b className={m.move >= 0 ? 'up' : 'down'}>{movePct(m.move)}</b>
                </li>
              ))}
            </ul>
          ) : <div className="nd-empty">Nothing on record yet.</div>}
        </section>

        <section className="nd-card nsh-panel">
          <header className="nd-card-head"><h3>Most recent reports</h3></header>
          {recent.length ? (
            <ul className="nsh-list">
              {recent.map((m, i) => (
                <li key={`${m.symbol}-r${i}`}>
                  <span className="nd-sym">{m.symbol}</span>
                  <span className="nst-mute">{m.label || dmy(m.date)}</span>
                  <b className={m.move >= 0 ? 'up' : 'down'}>{movePct(m.move)}</b>
                </li>
              ))}
            </ul>
          ) : <div className="nd-empty">Nothing on record yet.</div>}
        </section>
      </aside>
      </div>

      {isAdmin && (
        <details className="nd-ops">
          <summary>Grid detail (administrator)</summary>
          <div>
            source {d.source || '—'} · status {d.status || '—'} · columns {n} ·
            rows {rows.length} · reactions on record {allMoves.length} ·
            win rate from scoreboard n={winN ?? '—'}
            {!!d.detail && <> · {d.detail}</>}
          </div>
        </details>
      )}

      {err && (
        <div className="nd-err"><AlertTriangle size={15} /> The history could not be loaded. Nothing is shown rather than a partial record.</div>
      )}
    </div>
  );
}
