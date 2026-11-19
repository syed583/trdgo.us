import React from 'react';
import { NavLink } from 'react-router-dom';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, History, Minus, Search } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import '../new-strategy.css';

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

  const stats = [
    { tone: 'blue', icon: <History size={18} />, value: rows.length ? String(rows.length) : '—',
      label: 'Names covered', sub: `${n} past reports each` },
    { tone: 'violet', icon: <History size={18} />, value: allMoves.length ? String(allMoves.length) : '—',
      label: 'Past reactions on record', sub: '' },
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
      <section className="nua-head">
        <h1><History size={22} /> Signal History</h1>
        <p>
          How each name has actually behaved after its last {n} earnings reports, and what is called for
          it now. The cells are what the stock did — not what was predicted at the time.
        </p>
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

      {isAdmin && (
        <details className="nd-ops">
          <summary>Grid detail (administrator)</summary>
          <div>
            source {d.source || '—'} · status {d.status || '—'} · columns {n} ·
            rows {rows.length} · reactions on record {allMoves.length}
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
