import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Receipt, Volume2, VolumeX } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './strategy-trades.css';

/**
 * Strategy Trades -- demo (paper) $10,000-notional trades opened from the
 * intraday model signals, tracked live until stop / target / score-drop /
 * the 15:50 ET close. Paper only; no real orders. Live P&L refreshes every ~15s.
 */

const money = (n: number | null | undefined) =>
  n == null ? '—' : `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (n: number | null | undefined, suffix = '') =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${n}${suffix}`;
const px = (n: number | null | undefined) =>
  n == null ? '—' : Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n: number | null | undefined, digits = 2) =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${Number(n).toFixed(digits)}%`;
// "$0.03 (0.04%)"; "—" when the quote has no spread.
const spreadText = (r: any) =>
  r.spread == null ? '—' : `$${Number(r.spread).toFixed(2)}${r.spread_pct != null ? ` (${Number(r.spread_pct).toFixed(2)}%)` : ''}`;
const wideCls = (p: any) => (p != null && Number(p) > 0.3 ? ' st-wide' : '');
// Today's date in the user's local calendar, as YYYY-MM-DD for <input type="date">.
function localToday(): string {
  const d = new Date();
  const z = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
}
// "HH:MM:SS" in the browser's local time from an ISO timestamp; "—" if unparsable.
function localClock(iso: any): string {
  if (!iso) return '—';
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return '—';
  const z = (n: number) => String(n).padStart(2, '0');
  return `${z(t.getHours())}:${z(t.getMinutes())}:${z(t.getSeconds())}`;
}
const ageText = (s: any) => (s == null ? '' : s < 90 ? `${Math.round(s)}s ago` : `${Math.round(s / 60)} min ago`);

/** SPY / QQQ / DIA live strip from GET /api/market/etf-strip. */
function EtfStrip() {
  const [open, setOpen] = useState(true);
  // 20 s while the market is open, 120 s otherwise (the refresh period follows the last answer).
  const strip = useApi<any>((s) => api2.etfStrip(s), [], { refreshMs: open ? 20_000 : 120_000 });
  const d = strip.data;
  useEffect(() => { if (d && typeof d.market_open === 'boolean') setOpen(d.market_open); }, [d]);
  const err = strip.error || (d?.status === 'ERROR' ? (d.detail || 'backend error') : null);
  const rows: any[] = Array.isArray(d?.rows) ? d.rows : [];
  const order = ['SPY', 'QQQ', 'DIA'];
  const bySym = new Map(rows.map((r) => [String(r.symbol).toUpperCase(), r]));
  return (
    <div className="st-etf">
      {order.map((sym) => {
        const r = bySym.get(sym);
        const ch = r?.change;
        const cls = ch == null ? '' : ch > 0 ? 'pos' : ch < 0 ? 'neg' : '';
        return (
          <div className={`st-etf-box ${cls}`} key={sym} data-testid={`etf-${sym}`}>
            <div className="st-etf-sym">{sym}</div>
            <div className="st-etf-px">{r?.price == null ? '—' : px(r.price)}</div>
            <div className={`st-etf-chg ${cls}`}>
              {ch == null ? '—' : `${ch > 0 ? '+' : ''}${Number(ch).toFixed(2)}`}
              {' '}({pct(r?.change_percent)})
            </div>
            <div className="st-etf-ba ma-n">bid {r?.bid == null ? '—' : px(r.bid)} · ask {r?.ask == null ? '—' : px(r.ask)}</div>
            <div className="st-etf-upd ma-n">last update {localClock(r?.updated_at)}{r?.age_s != null ? ` · ${ageText(r.age_s)}` : ''}</div>
          </div>
        );
      })}
      <div className="st-etf-meta">
        {d && d.market_open === false && <span className="st-etf-closed">Market closed{d.session_label ? ` · ${d.session_label}` : ''}</span>}
        {d && d.market_open === true && d.session_label && <span className="ma-n">{d.session_label}</span>}
        {err && <span className="st-error st-error-inline">ETF strip: {err}</span>}
        {!d && !err && strip.initialLoading && <span className="ma-n"><Loader2 size={12} className="spin" /> loading…</span>}
      </div>
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  OPEN: 'Open', STOP: 'Stopped', TARGET: 'Target', TIMED_EXIT: 'Timed Exit', SCORE_EXIT: 'Score Exit', NO_PRICE: 'No price (closed unpriced)',
  CLEANUP: 'Cleanup (closed prior-day leftover)', EOD_CLOSE: 'Closed 15:50 ET', MARKET_CLOSE: 'Closed at the bell',
};
const CLOSED = new Set(['STOP', 'TARGET', 'TIMED_EXIT', 'SCORE_EXIT', 'NO_PRICE', 'CLEANUP', 'EOD_CLOSE', 'MARKET_CLOSE']);
const FLASH_MS = 180_000;   // 3 min: how long an entry / target / stop stays pinned on top & blinking

// --- page-only audio alerts (voice + a short beep). Built lazily on first
// enable so it starts from a user gesture (browsers block autoplay otherwise). ---
let _audio: AudioContext | null = null;
function _beep(ok: boolean) {
  try {
    _audio = _audio || new (window.AudioContext || (window as any).webkitAudioContext)();
    const ctx = _audio;
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = 'sine';
    o.frequency.value = ok ? 880 : 300;        // high chime for a win, low tone for a loss
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.5);
    o.start(); o.stop(ctx.currentTime + 0.52);
  } catch { /* no audio available */ }
}
function _say(text: string) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0; u.volume = 1.0;
    window.speechSynthesis.speak(u);       // queue, do not cancel: multiple events read in turn
  } catch { /* no speech available */ }
}
// Spell a ticker so TTS reads the letters ("A A P L"), not a mangled word.
const spell = (s?: string) => (s || '').toUpperCase().split('').join(' ');
const shortStrat = (s?: string) => (s || '').replace(' Intraday', '');

/**
 * For an OPEN trade, how much of the move from entry to target is already gone?
 * Tells you whether it's still worth taking "in the middle" or you've missed it.
 * Works for long and short (target is below entry for a short, so the ratio
 * stays positive as price moves the signal's way).
 */
function entryWindow(r: any): { label: string; cls: string; tip: string } | null {
  if (r.status !== 'OPEN') return null;
  const e = r.entry_price, t = r.target, last = r.last_price;
  if (e == null || t == null || last == null || t === e) return { label: 'Take now', cls: 'good', tip: 'Fresh signal — enter near the entry price.' };
  const prog = (last - e) / (t - e);           // 0 = at entry, 1 = at target
  if (prog >= 1) return { label: 'Missed', cls: 'late', tip: 'Price already reached the target — skip, do not chase.' };
  if (prog <= 0.15) return { label: 'Good entry', cls: 'good', tip: 'Still at/near the entry — best reward-to-risk. Take it now.' };
  if (prog <= 0.55) return { label: 'Still OK', cls: 'ok', tip: 'Part of the move is gone but reward still beats risk. OK to enter, or wait for a pullback toward entry.' };
  return { label: 'Late — wait', cls: 'late', tip: 'Most of the move to target is gone. Little reward left — wait for a pullback to entry or skip.' };
}

export default function StrategyTradesPage() {
  const [strategy, setStrategy] = useState('all');
  const [date, setDate] = useState(() => localToday());
  const [symQ, setSymQ] = useState('');
  const [sound, setSound] = useState(() => {
    try { return localStorage.getItem('st-sound') === '1'; } catch { return false; }
  });
  const q = useApi<any>((s) => api2.strategyTrades(strategy, date, s), [strategy, date], { refreshMs: 15_000 });
  const d = q.data;
  const loadError: string | null = q.error || (d?.status === 'ERROR' ? (d.detail || d.error || 'backend error') : null);
  const allRows: any[] = d?.rows || [];
  const needle = symQ.trim().toUpperCase();
  const rows: any[] = needle ? allRows.filter((r) => (r.symbol || '').toUpperCase().includes(needle)) : allRows;
  const strategies: any[] = d?.strategies || [];
  const total = d?.total_profit ?? 0;
  // Gross profit (winners) vs gross loss (losers) for the selected strategy.
  const gross = useMemo(() => {
    let p = 0, l = 0;
    for (const r of allRows) {
      const v = r.profit || 0;
      if (v > 0) p += v; else if (v < 0) l += v;
    }
    return { p, l };
  }, [allRows]);
  // Total % = total profit / sum of entry notional; "—" when no row carries a notional.
  const totalPct = useMemo(() => {
    let n = 0; let any = false;
    for (const r of allRows) if (r.notional != null) { n += Number(r.notional) || 0; any = true; }
    return any && n > 0 ? (Number(total) / n) * 100 : null;
  }, [allRows, total]);

  // Track what we've seen so we can detect NEW entries and OPEN->closed exits.
  const seenRef = useRef<Map<number, string>>(new Map());
  const initedRef = useRef(false);
  const soundRef = useRef(sound);
  soundRef.current = sound;
  // id -> flash kind: 'NEW' (entry), or the exit status (TARGET / STOP / ...).
  const [flash, setFlash] = useState<Record<number, string>>({});

  useEffect(() => {
    if (!d || !Array.isArray(d.rows)) return;
    const seen = seenRef.current;
    const all: any[] = d.rows;
    if (!initedRef.current) {               // first load: seed baseline, never announce
      all.forEach((r) => seen.set(r.id, r.status));
      initedRef.current = true;
      return;
    }
    const hits: { id: number; kind: string }[] = [];
    const voice: { ok: boolean; text: string }[] = [];
    for (const r of all) {
      const prev = seen.get(r.id);
      if (prev === undefined) {             // a brand-new trade row
        if (r.status === 'OPEN') {
          hits.push({ id: r.id, kind: 'NEW' });
          voice.push({ ok: r.side === 'LONG', text:
            `New ${r.side === 'LONG' ? 'buy' : 'sell'} trade, take it now. ${spell(r.symbol)}, ${shortStrat(r.strategy_label)}, entry ${Math.round((r.entry_price ?? 0) * 100) / 100}` });
        }
      } else if (prev === 'OPEN' && CLOSED.has(r.status)) {   // just hit stop / target / exit
        const win = (r.profit ?? 0) >= 0;
        const what = r.status === 'TARGET' ? 'hit target' : r.status === 'STOP' ? 'stopped out'
          : r.status === 'SCORE_EXIT' ? 'exited on signal drop' : 'closed at end of day';
        hits.push({ id: r.id, kind: r.status });
        voice.push({ ok: win, text:
          `${spell(r.symbol)} ${shortStrat(r.strategy_label)} ${what}, ${win ? 'profit' : 'loss'} ${Math.abs(Math.round(r.profit ?? 0))} dollars` });
      }
      seen.set(r.id, r.status);
    }
    if (hits.length) {
      setFlash((cur) => {
        const nx = { ...cur }; hits.forEach((h) => { nx[h.id] = h.kind; }); return nx;
      });
      hits.forEach((h) => setTimeout(() =>
        setFlash((cur) => { const nx = { ...cur }; delete nx[h.id]; return nx; }), FLASH_MS));
    }
    if (soundRef.current && voice.length) {
      _beep(voice.some((e) => e.ok));          // short win/loss tone
      voice.slice(0, 4).forEach((e) => _say(e.text));
    }
  }, [d]);

  function toggleSound() {
    const on = !sound;
    setSound(on);
    try { localStorage.setItem('st-sound', on ? '1' : '0'); } catch { /* ignore */ }
    if (on) { _beep(true); _say('Trade alerts on'); }   // first gesture unlocks audio
  }

  // Flashing trades (new entry / stop / target) pinned on top; then profit sort.
  const view = useMemo(() => {
    return [...rows].sort((a, b) => {
      const fa = flash[a.id] ? 1 : 0, fb = flash[b.id] ? 1 : 0;
      if (fa !== fb) return fb - fa;
      return (b.profit ?? 0) - (a.profit ?? 0);
    });
  }, [rows, flash]);

  const flashRows = view.filter((r) => flash[r.id]);

  return (
    <div className="page st-wrap">
      <div className="ts-head">
        <h1><Receipt size={20} /> Strategy Trades</h1>
        <p>Demo $10,000-notional paper trades opened from the intraday model signals, tracked live until
          stop / target / score-drop / the 15:50 ET close. Paper only — no real orders.</p>
      </div>

      <EtfStrip />

      <div className="st-bar">
        <span className="st-total pos"><span className="st-total-l">Profit</span><b>{money(gross.p)}</b></span>
        <span className="st-total neg"><span className="st-total-l">Loss</span><b>{money(gross.l)}</b></span>
        <span className={`st-total st-net ${total > 0 ? 'pos' : total < 0 ? 'neg' : ''}`}>
          <span className="st-total-l">Total</span><b>{money(total)}</b></span>
        <span className={`st-total ${totalPct != null && totalPct > 0 ? 'pos' : totalPct != null && totalPct < 0 ? 'neg' : ''}`}>
          <span className="st-total-l">Total %</span><b>{pct(totalPct)}</b></span>
        <label className="st-filter">Date
          <input type="date" className="st-date" value={date} max={localToday()}
            onChange={(e) => { if (e.target.value) setDate(e.target.value); }} />
        </label>
        <label className="st-filter">Strategy
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="all">All Strategies</option>
            {strategies.map((s) => <option value={s.key} key={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="st-filter">Symbol
          <input className="st-sym" value={symQ} onChange={(e) => setSymQ(e.target.value)}
            placeholder="e.g. AAPL" spellCheck={false} />
          {symQ && <button className="st-clear" onClick={() => setSymQ('')} title="Clear">×</button>}
        </label>
        <button className={`st-snd ${sound ? 'on' : ''}`} onClick={toggleSound}
          title={sound ? 'Trade voice alerts on — click to mute' : 'Turn on voice alerts for new trades & stop/target'}>
          {sound ? <Volume2 size={14} /> : <VolumeX size={14} />} {sound ? 'Alerts on' : 'Alerts off'}
        </button>
        {d && <span className="ma-n">{needle ? `${rows.length} of ${d.count}` : d.count} trades · {d.open} open · {d.wins} winners
          {d.date ? ` · ${d.date}` : ''}{d.after_close ? ' · market closed' : d.rth ? ' · live' : ''}</span>}
      </div>

      {flashRows.length > 0 && (
        <div className="st-alert">
          <span className="st-alert-h">🔔 LIVE — last 3 min</span>
          <div className="st-alert-list">
            {flashRows.map((r) => {
              const k = flash[r.id];
              if (k === 'NEW') return (
                <span className={`st-alert-chip new ${r.side === 'LONG' ? 'long' : 'short'}`} key={r.id}>
                  <span className="st-alert-tag new">TAKE NOW</span>
                  <b>{r.symbol}</b> {r.side === 'LONG' ? 'BUY' : 'SELL'} @ {px(r.entry_price)}
                  <span className="st-alert-sub"> · SL {px(r.stop)} · TP {px(r.target)} · {shortStrat(r.strategy_label)}</span>
                </span>
              );
              const tgt = k === 'TARGET';
              const label = tgt ? 'TARGET HIT' : k === 'STOP' ? 'STOPPED OUT'
                : k === 'SCORE_EXIT' ? 'EXIT (signal)' : 'CLOSED';
              return (
                <span className={`st-alert-chip ${tgt ? 'won' : 'lost'}`} key={r.id}>
                  <span className={`st-alert-tag ${tgt ? 'won' : 'lost'}`}>{label}</span>
                  <b>{r.symbol}</b> @ {px(r.exit_price ?? r.last_price)}
                  <span className="st-alert-sub"> · {money(r.profit)} · {shortStrat(r.strategy_label)}</span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {(d?.carried_open?.length ?? 0) > 0 && (
        <div className="st-alert" data-testid="carried-open">
          <span className="st-alert-h">↪ {d.carried_open.length} position{d.carried_open.length === 1 ? '' : 's'} still open from earlier dates</span>
          <div style={{ overflowX: 'auto' }}>
            <table className="ma-tbl st-tbl">
              <thead>
                <tr>
                  <th className="l">Opened</th><th className="l">L/S</th><th className="l">Symbol</th>
                  <th className="l">Strategy</th><th>Entry</th><th>Last</th><th>Profit</th><th>Stop</th><th>Target</th>
                </tr>
              </thead>
              <tbody>
                {d.carried_open.map((r: any) => (
                  <tr key={r.id} className={(r.profit ?? 0) > 0 ? 'st-win' : (r.profit ?? 0) < 0 ? 'st-loss' : ''}>
                    <td className="l">{r.opened_on}</td>
                    <td className="l"><span className={`st-ls ${r.side === 'LONG' ? 'long' : 'short'}`}>{r.side === 'LONG' ? 'Long' : 'Short'}</span></td>
                    <td className="l"><b>{r.symbol}</b></td>
                    <td className="l ma-n">{r.strategy_label?.replace(' Intraday', '')}</td>
                    <td>{px(r.entry_price)}</td>
                    <td>{px(r.last_price)}</td>
                    <td className={(r.profit ?? 0) > 0 ? 'pos' : (r.profit ?? 0) < 0 ? 'neg' : ''}><b>{money(r.profit)}</b></td>
                    <td className="ma-n">{px(r.stop)}</td>
                    <td className="ma-n">{px(r.target)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ma-note">These stay tracked (stop, target, score exit, the next 4:00pm close) until a rule closes them. Their P&amp;L belongs to the date they were opened and is not in today's total.</div>
        </div>
      )}

      {loadError && (
        <div className="st-error" role="alert">Could not load trades: {loadError}</div>
      )}
      {q.initialLoading && !loadError ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Loading trades…</div>
      ) : loadError && rows.length === 0 ? null : rows.length === 0 ? (
        <div className="ma-note">{needle ? `No trades match “${needle}” on ${date}.`
          : `No demo trades for ${date}. Trades open automatically when an intraday model signals BUY or SELL between 10:00 and 15:00 ET on a trading day.`}</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="ma-tbl st-tbl">
            <thead>
              <tr>
                <th className="l">Entry Time</th><th className="l">L/S</th><th className="l">Symbol</th>
                <th className="l">Strategy</th><th>Shares</th><th>Entry</th><th>Chg $</th><th>Last</th>
                <th>Bid</th><th>Ask</th><th>Spread</th>
                <th>Profit</th><th>Profit %</th><th>Stop</th><th>Target</th>
                <th className="l">Take Now?</th><th className="l">Status</th>
              </tr>
            </thead>
            <tbody>
              {view.map((r) => {
                const win = (r.profit ?? 0) > 0;
                const loss = (r.profit ?? 0) < 0;
                const ew = entryWindow(r);
                const fk = flash[r.id];                       // flash kind or undefined
                const flashCls = fk === 'NEW' ? 'st-new' : fk === 'TARGET' ? 'st-flash-won'
                  : fk ? 'st-flash-lost' : '';
                const tag = fk === 'NEW' ? 'NEW' : fk === 'TARGET' ? 'TP' : fk === 'STOP' ? 'SL' : fk ? '•' : '';
                return (
                  <tr key={r.id} className={`${flashCls} ${win ? 'st-win' : loss ? 'st-loss' : ''}`}>
                    <td className="l">{r.entry_time}</td>
                    <td className="l"><span className={`st-ls ${r.side === 'LONG' ? 'long' : 'short'}`}>
                      {r.side === 'LONG' ? 'Long' : 'Short'}</span></td>
                    <td className="l"><b>{r.symbol}</b>{fk && <span className={`st-newtag ${fk === 'NEW' ? '' : fk === 'TARGET' ? 'won' : 'lost'}`}>{tag}</span>}</td>
                    <td className="l ma-n">{r.strategy_label?.replace(' Intraday', '')}</td>
                    <td>{r.shares}</td>
                    <td>{px(r.entry_price)}</td>
                    <td className={r.chg > 0 ? 'pos' : r.chg < 0 ? 'neg' : ''}>{signed(r.chg)}</td>
                    <td>{px(r.last_price)}</td>
                    <td className="ma-n">{px(r.bid)}</td>
                    <td className="ma-n">{px(r.ask)}</td>
                    <td className={`ma-n${wideCls(r.spread_pct)}`}
                      title={r.quote_age_s != null ? `quote ${ageText(r.quote_age_s)}` : undefined}>{spreadText(r)}</td>
                    <td className={win ? 'pos' : loss ? 'neg' : ''}><b>{money(r.profit)}</b></td>
                    <td className={win ? 'pos' : loss ? 'neg' : ''}>{signed(r.profit_pct, '%')}</td>
                    <td className="ma-n">{px(r.stop)}</td>
                    <td className="ma-n">{px(r.target)}</td>
                    <td className="l">{fk === 'NEW' && r.status === 'OPEN'
                      ? <span className="st-take takenow" title="Brand-new signal — enter now at the entry price.">⚡ TAKE NOW</span>
                      : ew
                        ? <span className={`st-take ${ew.cls}`} title={ew.tip}>{ew.label}</span>
                        : <span className="ma-n">—</span>}</td>
                    <td className="l"><span className={`st-stat ${r.status}`} title={r.exit_reason_label || undefined}>{STATUS_LABEL[r.status] || r.exit_reason_label || r.status}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="st-note">Live P&amp;L updates every ~15s. One trade per symbol per strategy per day,
        no re-entry. Every trade is $10,000 notional (shares = floor(10,000 / entry)). Entries are taken
        only between 10:00 and 15:00 ET on regular trading days. Entry/stop/target come from each model's
        ATR levels; the stop moves to breakeven (entry) once price has covered 50% of the distance to the
        target. A position closes on stop, target, a 15‑point score drop, or at 15:50 ET (10 min before an
        early close). Fills are gap-aware: a stop or target jumped over at the open fills at the open print,
        not the level.
        <br /><b>Take Now?</b> tells you whether an OPEN signal is still worth entering mid‑move:
        <span className="st-take good">Good entry</span> = price still at/near entry (best);
        <span className="st-take ok">Still OK</span> = part of the move gone, reward still beats risk;
        <span className="st-take late">Late — wait</span> = most of the move to target is gone, wait for a pullback or skip.
        <br />Turn on <b>Alerts</b> to pin & blink each new trade at the top and hear a spoken call-out when a
        trade opens or hits its stop / target (this page only; your browser must allow sound).</div>
    </div>
  );
}
