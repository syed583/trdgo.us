import React from 'react';
import { Bell, BellOff, ExternalLink } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';

/**
 * The bell's dropdown: the trades the intraday models opened, newest first.
 *
 * WHY THIS EXISTS. The bell used to navigate to /alerts, which is the rule builder - "tell me when
 * NVDA crosses 120". That is a useful page and it has not moved, but it is not what a bell is for.
 * The owner asked for the thing a bell implies: what did the models just do, with a sound when
 * something new arrives and a way to silence it.
 *
 * EVERY ROW IS A REAL TRADE. The source is the same Strategy Trades feed the page shows - symbol,
 * side, entry price and entry time, exactly as recorded. Nothing is invented, and if the feed is
 * empty the panel says so rather than inventing activity.
 *
 * THE SOUND IS OFF UNTIL SOMETHING IS NEW. It plays only when a trade id appears that was not in
 * the previous reading, never on the first load - otherwise opening the app would always chime,
 * which teaches people to ignore it. It is a short tone synthesised with the Web Audio API rather
 * than a bundled file: no asset to ship and nothing to fail to load.
 *
 * MUTE IS REMEMBERED (localStorage). A person who mutes an alert sound means it, and means it
 * tomorrow too.
 */

const MUTE_KEY = 'trdgo.alerts.muted.v1';
const SEEN_KEY = 'trdgo.alerts.seen.v1';

function readMuted(): boolean {
  try { return localStorage.getItem(MUTE_KEY) === '1'; } catch { return false; }
}

/** A short two-note chime. Synthesised, so there is no audio file to ship or fail to fetch. */
function chime(): void {
  try {
    const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const now = ctx.currentTime;
    [880, 1174].forEach((hz, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = hz;
      gain.gain.setValueAtTime(0.0001, now + i * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.18, now + i * 0.12 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.12 + 0.22);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.12);
      osc.stop(now + i * 0.12 + 0.24);
    });
    setTimeout(() => ctx.close().catch(() => {}), 900);
  } catch { /* a browser that will not play a tone is not an error worth surfacing */ }
}

export type TradeAlert = {
  id: string; symbol: string; side: string; price: number | null; time: string;
};

/** The rows the bell shows, from the live Strategy Trades feed. Exported for the panel and the badge. */
export function useTradeAlerts() {
  const q = useApi<any>((s) => api2.strategyTrades('', '', s), [], { refreshMs: 60_000 });
  const rows: TradeAlert[] = React.useMemo(() => {
    const out = (q.data?.rows || [])
      .filter((r: any) => r && r.symbol && r.side)
      .map((r: any) => ({
        id: String(r.id ?? `${r.symbol}-${r.entry_time}`),
        symbol: String(r.symbol),
        side: String(r.side).toUpperCase(),
        price: r.entry_price ?? null,
        time: String(r.entry_time || ''),
      }));
    // newest first: the feed is in entry order, and a bell is about what just happened
    return out.reverse();
  }, [q.data]);
  return { rows, loading: q.loading, error: q.error };
}

export default function TradeAlerts({ onClose, onSeen }: { onClose: () => void; onSeen: () => void }) {
  const { rows, loading } = useTradeAlerts();
  const [muted, setMuted] = React.useState(readMuted);
  const panel = React.useRef<HTMLDivElement | null>(null);

  // Opening the panel is what marks things seen; the dot is about things you have not looked at.
  React.useEffect(() => {
    if (!rows.length) return;
    try { localStorage.setItem(SEEN_KEY, rows[0].id); } catch { /* private window */ }
    onSeen();
  }, [rows, onSeen]);

  // Close on a click outside or on Escape - a dropdown that traps you is worse than no dropdown.
  React.useEffect(() => {
    const away = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) onClose();
    };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [onClose]);

  const toggleMute = () => {
    setMuted((m) => {
      const next = !m;
      try { localStorage.setItem(MUTE_KEY, next ? '1' : '0'); } catch { /* private window */ }
      return next;
    });
  };

  const money = (v: number | null) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);

  return (
    <div className="nu-alerts" ref={panel} role="dialog" aria-label="Trade alerts">
      <header>
        <b>Trade alerts</b>
        <button type="button" className="nu-alerts-mute" onClick={toggleMute}
          aria-pressed={muted} title={muted ? 'Sound is off' : 'Sound is on'}>
          {muted ? <BellOff size={15} /> : <Bell size={15} />}
          <span>{muted ? 'Muted' : 'Sound on'}</span>
        </button>
      </header>

      <div className="nu-alerts-body">
        {loading && !rows.length && <div className="nu-alerts-none">Reading the book…</div>}
        {!loading && !rows.length && (
          <div className="nu-alerts-none">No trade has been opened today.</div>
        )}
        {rows.slice(0, 20).map((r) => (
          <div className="nu-alert-row" key={r.id}>
            <span className={`nu-alert-side ${r.side === 'BUY' ? 'buy' : 'sell'}`}>{r.side}</span>
            <b>{r.symbol}</b>
            <span className="nu-alert-px">{money(r.price)}</span>
            <span className="nu-alert-at">{r.time}</span>
          </div>
        ))}
      </div>

      <footer>
        <NavLink to="/strategy-trades" onClick={onClose}>All trades</NavLink>
        {/* The rule builder has not gone anywhere; it is simply not what the bell is for. */}
        <NavLink to="/alerts" onClick={onClose}>Price alert rules <ExternalLink size={11} /></NavLink>
      </footer>
    </div>
  );
}

/** How many of the newest trades have not been looked at, and the chime when one arrives. */
export function useUnseenAlerts(): { unseen: number; markSeen: () => void } {
  const { rows } = useTradeAlerts();
  const [seenId, setSeenId] = React.useState<string>(() => {
    try { return localStorage.getItem(SEEN_KEY) || ''; } catch { return ''; }
  });
  const firstRead = React.useRef(true);

  const unseen = React.useMemo(() => {
    if (!rows.length) return 0;
    if (!seenId) return rows.length;
    const at = rows.findIndex((r) => r.id === seenId);
    return at < 0 ? rows.length : at;
  }, [rows, seenId]);

  // The sound: only for something that arrived AFTER this tab was already looking, and only when
  // it is not muted. Never on the first read - an app that chimes when you open it is an app
  // whose chime you learn to ignore.
  React.useEffect(() => {
    if (firstRead.current) { firstRead.current = false; return; }
    if (unseen > 0 && !readMuted()) chime();
  }, [unseen]);

  const markSeen = React.useCallback(() => {
    try { setSeenId(localStorage.getItem(SEEN_KEY) || ''); } catch { /* private window */ }
  }, []);

  return { unseen, markSeen };
}
