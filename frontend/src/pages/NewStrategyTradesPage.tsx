import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  Activity, AlertTriangle, ArrowRight, CheckCircle2, Clock, Crosshair, TrendingUp,
  Volume2, VolumeX,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import strategyHero from '../assets/strategy-hero.webp';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * Strategy Trades in the redesigned shell (Figma frame 2:430).
 *
 * WHAT THE DESIGN ASKS FOR AND WE DO NOT HAVE. The frame's table has Setup (Intraday/Swing/Position),
 * Target 2, Trend, and filters for Market, Sector and Signal. The blotter has none of those: a trade
 * carries one target, no setup class and no sector. Per the owner's rule, what we cannot measure is
 * removed rather than invented - so those columns and filters are absent, not filled with plausible
 * values. Everything that IS shown comes from /api/strategy-trades.
 *
 * No figure here is derived any more: R:R was the only one, and the owner removed it (2026-11-21).
 * Everything rendered is a field of the payload.
 *
 * A.26: a non-administrator sees Agent 1-4, never which model a strategy is. The server already maps
 * this - `strategy_label` arrives as "Agent 2" for a user - so the page simply renders what it is given
 * and must never try to resolve a name itself.
 */

type Any = any;

const n2 = (v: Any): string => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));
const money = (v: Any): string => {
  const x = Number(v);
  if (v === null || v === undefined || Number.isNaN(x)) return '—';
  return `${x < 0 ? '-' : ''}$${Math.abs(x).toFixed(2)}`;
};
const pct = (v: Any): string =>
  (v === null || v === undefined ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
/** up / down / neither. ZERO GETS NO COLOUR: a flat $0.00 painted green reads as a profit,
 *  which is what the whole stat row did on a day with no trades. */
const dir = (v: Any): string => {
  if (v === null || v === undefined) return '';
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
};


/* ---------------------------------------------------------------- voice alerts
 * Ported from the classic Strategy Trades page so it behaves exactly as it did there. Both the
 * AudioContext and the first utterance are created from the button press: a browser blocks audio
 * that does not come from a user gesture, so building them lazily is not an optimisation, it is
 * the only way this works at all.
 */
let _audio: AudioContext | null = null;

function _beep(ok: boolean) {
  try {
    _audio = _audio || new (window.AudioContext || (window as any).webkitAudioContext)();
    const ctx = _audio;
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = 'sine';
    o.frequency.value = ok ? 880 : 300;          // high chime for a win, low tone for a loss
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.5);
    o.start(); o.stop(ctx.currentTime + 0.52);
  } catch { /* a machine with no audio is not an error worth surfacing */ }
}

function _say(text: string) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0; u.volume = 1.0;
    window.speechSynthesis.speak(u);             // queue, do not cancel: events are read in turn
  } catch { /* no speech engine */ }
}

/** Spell a ticker so it is read as letters ("A A P L"), not as a mangled word. */
const spell = (s?: string) => (s || '').toUpperCase().split('').join(' ');
const shortStrat = (s?: string) => (s || '').replace(' Intraday', '');
const CLOSED_STATES = new Set(['TARGET', 'STOP', 'SCORE_EXIT', 'EOD_CLOSE', 'MARKET_CLOSE', 'CLEANUP']);

const STATUS_TONE: Record<string, string> = {
  OPEN: 'open', TARGET: 'win', STOP: 'loss', SCORE_EXIT: 'flat', EOD_CLOSE: 'flat',
  CLEANUP: 'flat', NO_PRICE: 'flat', MARKET_CLOSE: 'flat', PENDING_RECON: 'flat',
};

export default function NewStrategyTradesPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [strategy, setStrategy] = React.useState('all');
  const [date, setDate] = React.useState('');
  const [find, setFind] = React.useState('');
  const [view, setView] = React.useState<'all' | 'open' | 'closed'>('all');
  const [page, setPage] = React.useState(1);
  const [perPage, setPerPage] = React.useState(25);
  const [sound, setSound] = React.useState(false);
  const soundRef = React.useRef(false);
  const seenRef = React.useRef<Map<number, string>>(new Map());
  const initedRef = React.useRef(false);

  const q = useApi<Any>((s) => api2.strategyTrades(strategy, date, s), [strategy, date],
    { refreshMs: 15_000 });
  const d = q.data || {};
  const rows: Any[] = d.rows || [];

  /* What changed since the last read, and say it. The FIRST read only seeds the baseline:
     a page that announces twenty trades the moment you open it teaches you to turn it off. */
  React.useEffect(() => {
    const all: Any[] = d.rows || [];
    if (!all.length) return;
    const seen = seenRef.current;
    if (!initedRef.current) {
      all.forEach((r) => seen.set(r.id, r.status));
      initedRef.current = true;
      return;
    }
    const voice: { ok: boolean; text: string }[] = [];
    for (const r of all) {
      const prev = seen.get(r.id);
      if (prev === undefined) {
        if (r.status === 'OPEN') {
          voice.push({
            ok: r.side !== 'SHORT',
            text: `New ${r.side === 'SHORT' ? 'sell' : 'buy'} trade, take it now. ${spell(r.symbol)},`
              + ` ${shortStrat(r.strategy_label)}, entry ${Math.round((r.entry_price ?? 0) * 100) / 100}`,
          });
        }
      } else if (prev === 'OPEN' && CLOSED_STATES.has(r.status)) {
        const win = (r.profit ?? 0) >= 0;
        const what = r.status === 'TARGET' ? 'hit target'
          : r.status === 'STOP' ? 'stopped out'
          : r.status === 'SCORE_EXIT' ? 'exited on signal drop' : 'closed at end of day';
        voice.push({
          ok: win,
          text: `${spell(r.symbol)} ${shortStrat(r.strategy_label)} ${what},`
            + ` ${win ? 'profit' : 'loss'} ${Math.abs(Math.round(r.profit ?? 0))} dollars`,
        });
      }
      seen.set(r.id, r.status);
    }
    if (soundRef.current && voice.length) {
      _beep(voice.some((e) => e.ok));
      voice.slice(0, 4).forEach((e) => _say(e.text));   // four is plenty; the rest are on screen
    }
  }, [d]);

  function toggleSound() {
    const on = !sound;
    setSound(on);
    soundRef.current = on;
    // Speak once, here, from the click: this is the gesture that unlocks audio for the tab.
    if (on) { _beep(true); _say('Voice alerts on'); }
    else { try { window.speechSynthesis.cancel(); } catch { /* none */ } }
  }

  const shown = React.useMemo(() => {
    let r = rows;
    if (view === 'open') r = r.filter((x) => x.status === 'OPEN');
    if (view === 'closed') r = r.filter((x) => x.status !== 'OPEN');
    const f = find.trim().toUpperCase();
    if (f) r = r.filter((x) => String(x.symbol || '').includes(f));
    return r;
  }, [rows, view, find]);

  // The frame paginates this table. Any filter change returns to page one, so a narrowed list never
  // opens on an empty page.
  React.useEffect(() => { setPage(1); }, [view, find, strategy, date, perPage]);
  const pages = Math.max(1, Math.ceil(shown.length / perPage));
  const pageRows = React.useMemo(
    () => shown.slice((page - 1) * perPage, (page - 1) * perPage + perPage),
    [shown, page, perPage],
  );

  // Every figure below is in the payload or arithmetic on it.
  const closed = d.closed ?? 0;
  const wins = d.wins ?? 0;
  const winRate = closed ? (wins / closed) * 100 : null;

  const stats = [
    { icon: <Activity size={18} />, tone: 'blue', value: d.count != null ? String(d.count) : '—',
      label: 'Trades on this date', sub: d.open != null ? `${d.open} still open` : '' },
    { icon: <CheckCircle2 size={18} />, tone: 'green', value: winRate != null ? `${winRate.toFixed(1)}%` : '—',
      label: 'Win rate', sub: closed ? `${wins} of ${closed} closed` : 'nothing closed yet' },
    // `money` is a P&L, so the NUMBER carries the colour, not just the icon square beside it.
    { icon: <TrendingUp size={18} />, tone: d.total_profit >= 0 ? 'green' : 'red',
      value: d.total_profit != null ? money(d.total_profit) : '—', pl: d.total_profit,
      label: 'P&L, trades opened on this date', sub: d.total_pct != null ? pct(d.total_pct) : '' },
    { icon: <Clock size={18} />, tone: 'amber',
      value: d.realized_on_date != null ? money(d.realized_on_date) : '—', pl: d.realized_on_date,
      label: 'Realised on this date', sub: d.closed_on_date != null ? `${d.closed_on_date} positions closed` : '' },
  ];

  return (
    <div className="nd nst">
      {/* ---------------------------------------------------------------- hero */}
      <section className="nd-hero nst-hero">
        <img className="nd-hero-img" src={strategyHero} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow"><Crosshair size={14} /> Tracked paper trades</span>
          <h1>Strategy Trades</h1>
          <p>
            Every signal the intraday models opened, with its entry, stop and target, tracked to the close.
            Paper only — no order is placed from this page.
          </p>
          <div className="nst-rulebar">
            {d.rules?.notional_usd != null && <span>${Number(d.rules.notional_usd).toLocaleString('en-US')} a trade</span>}
            {d.rules?.max_stop_pct != null && <span>stop capped at {d.rules.max_stop_pct}%</span>}
            {d.rules?.eod_close_et && <span>flat by {d.rules.eod_close_et} ET</span>}
            {d.rth != null && <span className={d.rth ? 'on' : ''}>{d.rth ? 'Market open' : 'Market closed'}</span>}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- stat row */}
      <div className="nd-stats nst-stats">
        {stats.map((s) => (
          <div className="nd-stat" key={s.label}>
            <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
            <div>
              <b className={s.pl != null ? dir(s.pl) : undefined}>{s.value}</b>
              <span>{s.label}</span>
              {!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}
            </div>
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------------------- filters */}
      <div className="nst-filters">
        <div className="nd-tabs">
          {(['all', 'open', 'closed'] as const).map((k) => (
            <button type="button" key={k} className={view === k ? 'is-on' : ''} onClick={() => setView(k)}>
              {k === 'all' ? 'All trades' : k === 'open' ? 'Open now' : 'Closed'}
            </button>
          ))}
        </div>

        <label className="nst-field">
          <span>Model</span>
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="all">All</option>
            {(d.strategies || []).map((s: Any) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
        </label>

        <label className="nst-field">
          <span>Date</span>
          <input type="date" value={date || (d.date || '')} onChange={(e) => setDate(e.target.value)} />
        </label>

        <label className="nst-field nst-find">
          <span>Symbol</span>
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="e.g. NVDA" />
        </label>

        <button type="button" className={`nst-snd${sound ? ' is-on' : ''}`} onClick={toggleSound}
          aria-pressed={sound}
          title={sound ? 'Voice alerts are on - click to mute'
            : 'Speak new trades and their stop or target as they happen'}>
          {sound ? <Volume2 size={15} /> : <VolumeX size={15} />}
          {sound ? 'Voice alerts on' : 'Enable voice alerts'}
        </button>

        <span className="nst-count">{shown.length} of {rows.length}</span>
      </div>

      {/* ---------------------------------------------------------------- the blotter */}
      <section className="nd-card nst-card">
        {q.loading && !rows.length && <div className="nd-empty">Loading…</div>}
        {!q.loading && !rows.length && (
          <div className="nd-empty">No trades on this date.</div>
        )}
        {!!rows.length && !shown.length && (
          <div className="nd-empty">No trades match this filter.</div>
        )}

        {!!shown.length && (
          <div className="nst-scroll">
            <table className="nd-table nst-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Model</th><th>Signal</th>
                  <th className="r">Entry</th><th className="r">Target</th><th className="r">Stop</th>
                  <th className="r">Bid</th><th className="r">Ask</th>
                  <th className="r">Spread</th><th className="r">Price</th><th className="r">Change</th>
                  <th className="r">P&amp;L</th><th className="r">Score</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r) => {
                  const tone = STATUS_TONE[r.status] || 'flat';
                  return (
                    <tr key={r.id}>
                      <td>
                        {/* The route is /ai-insights/:symbol. Written as a query, the router kept the PREVIOUS
    symbol in the path and nothing consumed the query, so this landed on the landing page
    with the ticker lost. run=1 is the flag the page already honours to start at once. */}
                        <NavLink to={`/ai-insights/${encodeURIComponent(r.symbol)}?run=1`}
                          className="nd-sym">{r.symbol}</NavLink>
                        {r.entry_time && <div className="nst-sub">{r.entry_time}</div>}
                      </td>
                      {/* strategy_label is already "Agent 2" for a user - rendered, never resolved here (A.26) */}
                      <td className="nst-mute">{r.strategy_label || r.strategy}</td>
                      <td>
                        <span className={`nd-chip ${r.side === 'SHORT' ? 'put' : 'call'}`}>
                          {r.side === 'SHORT' ? 'SELL' : 'BUY'}
                        </span>
                      </td>
                      <td className="r">{n2(r.entry_price)}</td>
                      <td className="r">{n2(r.target)}</td>
                      <td className="r">
                        {n2(r.stop)}
                        {r.be_moved && <div className="nst-sub">at breakeven</div>}
                      </td>
                      <td className="r nst-mute">{n2(r.bid)}</td>
                      <td className="r nst-mute">{n2(r.ask)}</td>
                      <td className="r nst-mute">
                        {r.spread != null ? n2(r.spread) : '—'}
                        {r.spread_pct != null && <div className="nst-sub">{(Number(r.spread_pct)).toFixed(2)}%</div>}
                      </td>
                      <td className="r">{n2(r.last_price)}</td>
                      <td className={`r ${dir(r.profit_pct)}`}>{pct(r.profit_pct)}</td>
                      <td className={`r ${dir(r.profit)}`}>{money(r.profit)}</td>
                      <td className="r nst-mute">{r.score_at_entry != null ? n2(r.score_at_entry) : '—'}</td>
                      <td>
                        <span className={`nst-status ${tone}`}>{r.exit_reason_label || r.status}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

      </section>

      {/* 2:430 draws the pager BELOW the table card, not inside it. */}
        {/* The frame's pager. Only shown when there is more than one page to walk. */}
      {pages > 1 && (
        <div className="nst-pager">
          <span className="nst-mute">
            Showing {(page - 1) * perPage + 1}–{Math.min(page * perPage, shown.length)} of {shown.length}
          </span>
          <div className="nst-pager-btns">
            <button type="button" className="ghost-btn" disabled={page === 1}
              onClick={() => setPage((x) => Math.max(1, x - 1))}>Previous</button>
            {Array.from({ length: Math.min(pages, 6) }).map((_, i) => {
              const n = i + 1;
              return (
                <button type="button" key={n} className={page === n ? 'is-on' : ''}
                  onClick={() => setPage(n)}>{n}</button>
              );
            })}
            {pages > 6 && <span className="nst-mute">… {pages}</span>}
            <button type="button" className="ghost-btn" disabled={page === pages}
              onClick={() => setPage((x) => Math.min(pages, x + 1))}>Next</button>
          </div>
          <label className="nst-field nst-perpage">
            <span>Rows</span>
            <select value={perPage} onChange={(e) => setPerPage(Number(e.target.value))}>
              {[10, 25, 50, 100].map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
        </div>
      )}

      {isAdmin && (
        <details className="nd-ops">
          <summary>Blotter detail (administrator)</summary>
          <div>
            source: {d.source || '—'} · date {d.date || '—'} · notional {money(d.total_notional)} ·
            gap effect {money(d.gap_effect_total)} · entries open: {String(d.entries_open)} ·
            after close: {String(d.after_close)}
            {d.rules?.stop_cap_hits != null && <> · stop-cap hits {d.rules.stop_cap_hits}</>}
            {d.rules?.score_drop_exit != null && <> · score-drop exit {d.rules.score_drop_exit}</>}
          </div>
        </details>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The blotter could not be loaded. Nothing is shown rather than a stale book.</div>
      )}
        {/* What the design asks for and the blotter does not record. Stated rather than left as a
          silent gap, so nobody wonders whether it is broken or simply absent. */}
      <p className="nd-note nst-absent">
        A trade here carries one target, no setup class and no sector, so the design&apos;s
        Setup, Target 2, Sector and Market columns have nothing behind them and are left out.
        There is no per-trade price series either, so there is no trend sparkline.
      </p>

      {/* Positions carried from an earlier date are deliberately outside the table and the totals. */}
      {!!d.carried_open_count && (
        <p className="nd-note">
          {d.carried_open_count} position{d.carried_open_count === 1 ? '' : 's'} opened on an earlier date
          {' '}are still open. Their P&amp;L stays attributed to the day they were opened, so they are not in
          the table or the totals above.
        </p>
      )}
      {!!d.realized_note && <p className="nd-note">{d.realized_note}</p>}

    </div>
  );
}
