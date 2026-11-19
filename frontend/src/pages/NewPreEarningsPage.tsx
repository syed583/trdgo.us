import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  AlertTriangle, ArrowUpRight, ArrowDownRight, CalendarDays, Compass, Minus, Search,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import preEarningsHero from '../assets/pre-earnings-hero.webp';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * Pre-Earnings Direction in the redesigned shell (Figma frame 2:1552).
 *
 * WHAT IS NOT HERE. The frame carries an "Average Score Trend" chart over time; this endpoint returns a
 * single scan, with no history, so the chart is absent rather than drawn from one point. The frame also
 * shows each row's contributing parameters - those are model internals, gated to an administrator by
 * B.1/A.29, and the server already strips them, so the page never looks for them.
 *
 * PENDING IS NOT A VERDICT. A name whose score has not been computed yet comes back as PENDING with a
 * null score. It is shown as awaiting a score and is excluded from the BUY/NEUTRAL/SELL counts and the
 * ring - counting it as "neutral" would turn "we have not looked yet" into a judgement about the stock.
 */

type Any = any;

const n0 = (v: Any): string => (v === null || v === undefined ? '—' : String(Math.round(Number(v))));
const n2 = (v: Any): string => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));
const cap = (v: Any): string => {
  const x = Number(v);
  if (!Number.isFinite(x) || x <= 0) return '—';
  if (x >= 1e12) return `$${(x / 1e12).toFixed(1)}T`;
  if (x >= 1e9) return `$${(x / 1e9).toFixed(0)}B`;
  if (x >= 1e6) return `$${(x / 1e6).toFixed(0)}M`;
  return `$${x.toFixed(0)}`;
};

const SIGNAL_TONE: Record<string, string> = { BUY: 'win', SELL: 'loss', NEUTRAL: 'flat', PENDING: 'open' };
const SIGNAL_ICON: Record<string, React.ReactNode> = {
  BUY: <ArrowUpRight size={13} />, SELL: <ArrowDownRight size={13} />, NEUTRAL: <Minus size={13} />,
};

export default function NewPreEarningsPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [days, setDays] = React.useState(15);
  const [sig, setSig] = React.useState<'all' | 'BUY' | 'SELL' | 'NEUTRAL' | 'PENDING'>('all');
  const [find, setFind] = React.useState('');

  const q = useApi<Any>((s) => api2.preEarningsDirectionScan(days, s), [days], { refreshMs: 120_000 });
  const d = q.data || {};
  const rows: Any[] = d.rows || [];

  const counts = React.useMemo(() => {
    const c: Record<string, number> = { BUY: 0, SELL: 0, NEUTRAL: 0, PENDING: 0 };
    rows.forEach((r) => { const k = String(r.signal || 'PENDING'); if (k in c) c[k] += 1; });
    return c;
  }, [rows]);

  // The ring and the shares are of SCORED names only - see the note above.
  const scored = counts.BUY + counts.SELL + counts.NEUTRAL;
  const share = (n: number) => (scored ? Math.round((n / scored) * 100) : null);

  const [session, setSession] = React.useState('all');
  const [sector, setSector] = React.useState('all');
  const [page, setPage] = React.useState(1);
  const perPage = 25;

  // Built from what is actually in the scan, so a filter can never offer an empty option.
  const sessions = React.useMemo(
    () => Array.from(new Set(rows.map((r: Any) => r.time_label).filter(Boolean))).sort(),
    [rows],
  );
  const sectors = React.useMemo(
    () => Array.from(new Set(rows.map((r: Any) => r.sector).filter(Boolean))).sort(),
    [rows],
  );

  const shown = React.useMemo(() => {
    let r = rows;
    if (sig !== 'all') r = r.filter((x) => String(x.signal || 'PENDING') === sig);
    if (session !== 'all') r = r.filter((x) => String(x.time_label || '') === session);
    if (sector !== 'all') r = r.filter((x) => String(x.sector || '') === sector);
    const f = find.trim().toUpperCase();
    if (f) r = r.filter((x) => String(x.symbol || '').includes(f) || String(x.company || '').toUpperCase().includes(f));
    // highest score first; a name without a score sorts last rather than as a zero
    return [...r].sort((a, b) => {
      const A = a.score == null ? -1 : Number(a.score);
      const B = b.score == null ? -1 : Number(b.score);
      return B - A;
    });
  }, [rows, sig, find, session, sector]);

  React.useEffect(() => { setPage(1); }, [sig, find, session, sector, days]);
  const pages = Math.max(1, Math.ceil(shown.length / perPage));
  const pageRows = React.useMemo(
    () => shown.slice((page - 1) * perPage, (page - 1) * perPage + perPage),
    [shown, page],
  );

  /** The visible rows, as a CSV - the frame's Export. Only the columns on screen, so an export can
   *  never carry a field the page itself is not allowed to show. */
  const exportCsv = React.useCallback(() => {
    const head = ['Symbol', 'Company', 'Reports', 'Timing', 'Days', 'Prior close',
      'Market cap', 'Sector', 'Score', 'Direction'];
    const lines = pageRows.map((r: Any) => [
      r.symbol, r.company, r.date_label || r.date, r.time_label, r.days_to,
      r.prior_close, r.market_cap, r.sector, r.score, r.signal,
    ].map((v) => {
      const t = v == null ? '' : String(v);
      return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
    }).join(','));
    const blob = new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `pre-earnings-direction-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [shown]);

  const stats = [
    { tone: 'blue', value: d.universe != null ? String(d.universe) : String(rows.length),
      label: 'Companies reporting', sub: `next ${days} days` },
    { tone: 'green', value: String(counts.BUY), label: 'Buy', sub: share(counts.BUY) != null ? `${share(counts.BUY)}% of scored` : '' },
    { tone: 'violet', value: String(counts.NEUTRAL), label: 'Neutral', sub: share(counts.NEUTRAL) != null ? `${share(counts.NEUTRAL)}% of scored` : '' },
    { tone: 'red', value: String(counts.SELL), label: 'Sell', sub: share(counts.SELL) != null ? `${share(counts.SELL)}% of scored` : '' },
    { tone: 'amber', value: String(counts.PENDING), label: 'Awaiting a score', sub: counts.PENDING ? 'filling in' : 'all scored' },
  ];

  // the ring, drawn from the three scored shares
  const ring = (() => {
    const C = 2 * Math.PI * 46;
    let at = 0;
    return (['BUY', 'NEUTRAL', 'SELL'] as const).map((k) => {
      const v = scored ? counts[k] / scored : 0;
      const seg = { k, len: v * C, off: -at * C };
      at += v;
      return seg;
    });
  })();

  return (
    <div className="nd nst">
      <section className="nd-hero nst-hero">
        <img className="nd-hero-img" src={preEarningsHero} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow"><Compass size={14} /> Before the report</span>
          <h1>Pre-Earnings <em>Direction</em></h1>
          <p>
            A direction read on every company reporting in the next {days} days, ranked by score.
            It is a view on direction, not a recommendation to trade.
          </p>
          <div className="nst-rulebar">
            <span>{rows.length} names listed</span>
            <span>{scored} scored</span>
            {counts.PENDING > 0 && <span>{counts.PENDING} still filling in</span>}
          </div>
        </div>
      </section>

      <div className="nd-stats nst-stats">
        {stats.map((s) => (
          <div className="nd-stat" key={s.label}>
            <span className={`nd-stat-ico ${s.tone}`}>
              {s.label === 'Buy' ? <ArrowUpRight size={18} />
                : s.label === 'Sell' ? <ArrowDownRight size={18} />
                  : s.label === 'Neutral' ? <Minus size={18} /> : <CalendarDays size={18} />}
            </span>
            <div><b>{s.value}</b><span>{s.label}</span>{!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}</div>
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------------------- split of the scored names */}
      <div className="npe-split">
        <section className="nd-card npe-ring-card">
          <header className="nd-card-head"><h3>How the scored names split</h3></header>
          {scored ? (
            <div className="npe-ring-body">
              <svg viewBox="0 0 120 120" width="150" height="150" aria-hidden="true">
                <circle cx="60" cy="60" r="46" fill="none" stroke="var(--nu-line)" strokeWidth="14" />
                {ring.map((s) => (
                  <circle key={s.k} cx="60" cy="60" r="46" fill="none" strokeWidth="14"
                          stroke={s.k === 'BUY' ? 'var(--nu-up)' : s.k === 'SELL' ? 'var(--nu-down)' : 'var(--nu-ink-dim)'}
                          strokeDasharray={`${s.len} ${2 * Math.PI * 46}`}
                          strokeDashoffset={s.off}
                          transform="rotate(-90 60 60)" />
                ))}
                <text x="60" y="58" textAnchor="middle" className="npe-ring-n">{scored}</text>
                <text x="60" y="74" textAnchor="middle" className="npe-ring-l">scored</text>
              </svg>
              <ul className="nd-sent-legend">
                <li><i className="up" />Buy <b>{counts.BUY}{share(counts.BUY) != null ? ` · ${share(counts.BUY)}%` : ''}</b></li>
                <li><i className="flat" />Neutral <b>{counts.NEUTRAL}{share(counts.NEUTRAL) != null ? ` · ${share(counts.NEUTRAL)}%` : ''}</b></li>
                <li><i className="down" />Sell <b>{counts.SELL}{share(counts.SELL) != null ? ` · ${share(counts.SELL)}%` : ''}</b></li>
              </ul>
            </div>
          ) : q.loading ? <div className="nd-empty">Loading…</div>
            : <div className="nd-empty">Nothing is scored yet.</div>}
          <p className="nd-note">
            Shares are of the {scored} scored names. Companies still awaiting a score are left out —
            counting them as neutral would turn “not looked at yet” into a view on the stock.
          </p>
        </section>

        <section className="nd-card npe-read-card">
          <header className="nd-card-head"><h3>How to read this</h3></header>
          <ul className="npe-read">
            <li><b>Score</b> runs 0–100 and ranks the names against each other on this scan.</li>
            <li><b>Buy / Neutral / Sell</b> is the direction the score implies for the reaction to the report.</li>
            <li><b>Days</b> is trading days until the company reports; <b>Timing</b> is before the open or after the close.</li>
            <li>A name with no score yet is listed as awaiting one and is filled in by a background pass.</li>
            <li>This is a direction read on the report, not a trade plan: it carries no entry, stop or size.</li>
          </ul>
        </section>
      </div>

      {/* ---------------------------------------------------------------- filters */}
      <div className="nst-filters">
        <div className="nd-tabs">
          {(['all', 'BUY', 'NEUTRAL', 'SELL', 'PENDING'] as const).map((k) => (
            <button type="button" key={k} className={sig === k ? 'is-on' : ''} onClick={() => setSig(k)}>
              {k === 'all' ? 'All' : k === 'PENDING' ? 'Awaiting' : k.charAt(0) + k.slice(1).toLowerCase()}
              <i className="npe-chip-n">{k === 'all' ? rows.length : counts[k] ?? 0}</i>
            </button>
          ))}
        </div>
        <label className="nst-field">
          <span>Window</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 10, 15, 21, 30].map((n) => <option key={n} value={n}>Next {n} days</option>)}
          </select>
        </label>
        <label className="nst-field">
          <span>Session</span>
          <select value={session} onChange={(e) => setSession(e.target.value)}>
            <option value="all">All sessions</option>
            {sessions.map((x: Any) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <label className="nst-field">
          <span>Sector</span>
          <select value={sector} onChange={(e) => setSector(e.target.value)}>
            <option value="all">All sectors</option>
            {sectors.map((x: Any) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <button type="button" className="ghost-btn" onClick={exportCsv} disabled={!shown.length}>
          Export
        </button>
        <label className="nst-field nst-find">
          <span>Search</span>
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="symbol or company" />
        </label>
        <span className="nst-count">{shown.length} of {rows.length}</span>
      </div>

      {/* ---------------------------------------------------------------- the ranked table */}
      <section className="nd-card nst-card">
        {q.loading && !rows.length && <div className="nd-empty">Loading…</div>}
        {!q.loading && !rows.length && <div className="nd-empty">No companies report in this window.</div>}
        {!!rows.length && !shown.length && <div className="nd-empty">Nothing matches this filter.</div>}

        {!!shown.length && (
          <div className="nst-scroll">
            <table className="nd-table nst-table">
              <thead>
                <tr>
                  <th>#</th><th>Symbol</th><th>Company</th><th>Reports</th><th>Timing</th>
                  <th className="r">Days</th><th className="r">Prior close</th><th className="r">Market cap</th>
                  <th>Sector</th><th className="r">Score</th><th>Direction</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r, i) => {
                  const s = String(r.signal || 'PENDING');
                  return (
                    <tr key={`${r.symbol}-${r.date}`}>
                      <td className="nst-mute">{i + 1}</td>
                      <td><NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink></td>
                      <td className="nst-mute npe-co">{r.company || '—'}</td>
                      <td>{r.date_label || r.date || '—'}</td>
                      <td className="nst-mute">{r.time_label || '—'}</td>
                      <td className="r">{r.days_to != null ? r.days_to : '—'}</td>
                      <td className="r">{n2(r.prior_close)}</td>
                      <td className="r nst-mute">{cap(r.market_cap)}</td>
                      <td className="nst-mute">{r.sector || '—'}</td>
                      <td className="r"><b>{r.score == null ? '—' : n0(r.score)}</b></td>
                      <td>
                        <span className={`nst-status ${SIGNAL_TONE[s] || 'flat'}`}>
                          {SIGNAL_ICON[s]}{s === 'PENDING' ? 'Awaiting' : s.charAt(0) + s.slice(1).toLowerCase()}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {isAdmin && (
        <details className="nd-ops">
          <summary>Scan detail (administrator)</summary>
          <div>
            source: {d.source || '—'} · universe {d.universe ?? '—'} · scored {d.scored ?? '—'} ·
            window {days} days
            {!!(d.missing || []).length && <> · awaiting: {(d.missing || []).slice(0, 20).join(', ')}</>}
          </div>
        </details>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The scan could not be loaded. Nothing is shown rather than a stale list.</div>
      )}
    </div>
  );
}
