import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Receipt, Volume2, VolumeX } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './strategy-trades.css';

/**
 * Strategy Trades -- demo (paper) 100-share trades opened from the intraday
 * model signals, tracked live until stop / target / score-drop / timed exit.
 * Paper only; no real orders. Live P&L refreshes every ~15s.
 */

const money = (n: number | null | undefined) =>
  n == null ? '—' : `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (n: number | null | undefined, suffix = '') =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${n}${suffix}`;
const px = (n: number | null | undefined) =>
  n == null ? '—' : Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const STATUS_LABEL: Record<string, string> = {
  OPEN: 'Open', STOP: 'Stopped', TARGET: 'Target', TIMED_EXIT: 'Timed Exit', SCORE_EXIT: 'Score Exit',
};
const CLOSED = new Set(['STOP', 'TARGET', 'TIMED_EXIT', 'SCORE_EXIT']);
const NEW_BLINK_MS = 45_000;   // how long a just-opened trade blinks / stays pinned on top

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
  const [symQ, setSymQ] = useState('');
  const [sound, setSound] = useState(() => {
    try { return localStorage.getItem('st-sound') === '1'; } catch { return false; }
  });
  const q = useApi<any>((s) => api2.strategyTrades(strategy, '', s), [strategy], { refreshMs: 15_000 });
  const d = q.data;
  const allRows: any[] = d?.rows || [];
  const needle = symQ.trim().toUpperCase();
  const rows: any[] = needle ? allRows.filter((r) => (r.symbol || '').toUpperCase().includes(needle)) : allRows;
  const strategies: any[] = d?.strategies || [];
  const total = d?.total_profit ?? 0;

  // Track what we've seen so we can detect NEW trades and OPEN->closed transitions.
  const seenRef = useRef<Map<number, string>>(new Map());
  const initedRef = useRef(false);
  const soundRef = useRef(sound);
  soundRef.current = sound;
  const [newIds, setNewIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!d || !Array.isArray(d.rows)) return;
    const seen = seenRef.current;
    const all: any[] = d.rows;
    if (!initedRef.current) {               // first load: seed baseline, never announce
      all.forEach((r) => seen.set(r.id, r.status));
      initedRef.current = true;
      return;
    }
    const fresh: number[] = [];
    const exits: { ok: boolean; text: string }[] = [];
    for (const r of all) {
      const prev = seen.get(r.id);
      if (prev === undefined) {             // a brand-new trade row
        if (r.status === 'OPEN') {
          fresh.push(r.id);
          exits.push({ ok: r.side === 'LONG', text:
            `New ${r.side === 'LONG' ? 'buy' : 'sell'} trade, take it now. ${spell(r.symbol)}, ${shortStrat(r.strategy_label)}, entry ${Math.round((r.entry_price ?? 0) * 100) / 100}` });
        }
      } else if (prev === 'OPEN' && CLOSED.has(r.status)) {   // just closed
        const win = (r.profit ?? 0) >= 0;
        const what = r.status === 'TARGET' ? 'hit target' : r.status === 'STOP' ? 'stopped out'
          : r.status === 'SCORE_EXIT' ? 'exited on signal drop' : 'closed at end of day';
        exits.push({ ok: win, text:
          `${spell(r.symbol)} ${shortStrat(r.strategy_label)} ${what}, ${win ? 'profit' : 'loss'} ${Math.abs(Math.round(r.profit ?? 0))} dollars` });
      }
      seen.set(r.id, r.status);
    }
    if (fresh.length) {
      setNewIds((cur) => {
        const nx = new Set(cur); fresh.forEach((id) => nx.add(id)); return nx;
      });
      fresh.forEach((id) => setTimeout(() =>
        setNewIds((cur) => { const nx = new Set(cur); nx.delete(id); return nx; }), NEW_BLINK_MS));
    }
    if (soundRef.current && exits.length) {
      // a short beep (win/loss tone), then speak the events in turn
      _beep(exits.some((e) => e.ok));
      exits.slice(0, 4).forEach((e) => _say(e.text));
    }
  }, [d]);

  function toggleSound() {
    const on = !sound;
    setSound(on);
    try { localStorage.setItem('st-sound', on ? '1' : '0'); } catch { /* ignore */ }
    if (on) { _beep(true); _say('Trade alerts on'); }   // first gesture unlocks audio
  }

  // New trades pinned to the top (and blinking); then the backend's profit sort.
  const view = useMemo(() => {
    const isNew = (r: any) => newIds.has(r.id);
    return [...rows].sort((a, b) => {
      const na = isNew(a) ? 1 : 0, nb = isNew(b) ? 1 : 0;
      if (na !== nb) return nb - na;
      return (b.profit ?? 0) - (a.profit ?? 0);
    });
  }, [rows, newIds]);

  const freshRows = view.filter((r) => newIds.has(r.id));

  return (
    <div className="page st-wrap">
      <div className="ts-head">
        <h1><Receipt size={20} /> Strategy Trades</h1>
        <p>Demo 100-share paper trades opened from the intraday model signals, tracked live until
          stop / target / score-drop / timed exit. Paper only — no real orders.</p>
      </div>

      <div className="st-bar">
        <span className={`st-total ${total > 0 ? 'pos' : total < 0 ? 'neg' : ''}`}>
          Profit <b>{money(total)}</b>
        </span>
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

      {freshRows.length > 0 && (
        <div className="st-alert">
          <span className="st-alert-h">🔔 NEW — TAKE THE TRADE</span>
          <div className="st-alert-list">
            {freshRows.map((r) => (
              <span className={`st-alert-chip ${r.side === 'LONG' ? 'long' : 'short'}`} key={r.id}>
                <b>{r.symbol}</b> {r.side === 'LONG' ? 'BUY' : 'SELL'} @ {px(r.entry_price)}
                <span className="st-alert-sub"> · SL {px(r.stop)} · TP {px(r.target)} · {shortStrat(r.strategy_label)}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {q.initialLoading ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Loading trades…</div>
      ) : rows.length === 0 ? (
        <div className="ma-note">No demo trades yet today. Trades open automatically when an intraday
          model signals BUY or SELL during market hours.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="ma-tbl st-tbl">
            <thead>
              <tr>
                <th className="l">Entry Time</th><th className="l">L/S</th><th className="l">Symbol</th>
                <th className="l">Strategy</th><th>Shares</th><th>Entry</th><th>Chg $</th><th>Last</th>
                <th>Profit</th><th>Profit %</th><th>Stop</th><th>Target</th>
                <th className="l">Take Now?</th><th className="l">Status</th>
              </tr>
            </thead>
            <tbody>
              {view.map((r) => {
                const win = (r.profit ?? 0) > 0;
                const loss = (r.profit ?? 0) < 0;
                const ew = entryWindow(r);
                const fresh = newIds.has(r.id);
                return (
                  <tr key={r.id} className={`${fresh ? 'st-new' : ''} ${win ? 'st-win' : loss ? 'st-loss' : ''}`}>
                    <td className="l">{r.entry_time}</td>
                    <td className="l"><span className={`st-ls ${r.side === 'LONG' ? 'long' : 'short'}`}>
                      {r.side === 'LONG' ? 'Long' : 'Short'}</span></td>
                    <td className="l"><b>{r.symbol}</b>{fresh && <span className="st-newtag">NEW</span>}</td>
                    <td className="l ma-n">{r.strategy_label?.replace(' Intraday', '')}</td>
                    <td>{r.shares}</td>
                    <td>{px(r.entry_price)}</td>
                    <td className={r.chg > 0 ? 'pos' : r.chg < 0 ? 'neg' : ''}>{signed(r.chg)}</td>
                    <td>{px(r.last_price)}</td>
                    <td className={win ? 'pos' : loss ? 'neg' : ''}><b>{money(r.profit)}</b></td>
                    <td className={win ? 'pos' : loss ? 'neg' : ''}>{signed(r.profit_pct, '%')}</td>
                    <td className="ma-n">{px(r.stop)}</td>
                    <td className="ma-n">{px(r.target)}</td>
                    <td className="l">{fresh && r.status === 'OPEN'
                      ? <span className="st-take takenow" title="Brand-new signal — enter now at the entry price.">⚡ TAKE NOW</span>
                      : ew
                        ? <span className={`st-take ${ew.cls}`} title={ew.tip}>{ew.label}</span>
                        : <span className="ma-n">—</span>}</td>
                    <td className="l"><span className={`st-stat ${r.status}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="st-note">Live P&amp;L updates every ~15s. One trade per symbol per strategy per day,
        no re-entry. Entry/stop/target come from each model's ATR levels; a position closes on stop,
        target, a 15‑point score drop, or the 4:00pm ET close (Timed Exit).
        <br /><b>Take Now?</b> tells you whether an OPEN signal is still worth entering mid‑move:
        <span className="st-take good">Good entry</span> = price still at/near entry (best);
        <span className="st-take ok">Still OK</span> = part of the move gone, reward still beats risk;
        <span className="st-take late">Late — wait</span> = most of the move to target is gone, wait for a pullback or skip.
        <br />Turn on <b>Alerts</b> to pin & blink each new trade at the top and hear a spoken call-out when a
        trade opens or hits its stop / target (this page only; your browser must allow sound).</div>
    </div>
  );
}
