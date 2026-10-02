import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowUpRight, ArrowDownRight, Target, ShieldAlert, Ban,
  Clock, RefreshCw, Plus, X, Bell,
} from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { num } from '../lib/format';
import { PageHead } from './shared';
import './signals.css';

const TYPE_META: Record<string, { label: string; tone: string; icon: any }> = {
  BIAS_FLIP: { label: 'View changed', tone: 'flip', icon: RefreshCw },
  NEW_SETUP: { label: 'New setup', tone: 'new', icon: Target },
  TP1_HIT: { label: 'Target 1 hit', tone: 'win', icon: Target },
  TP2_HIT: { label: 'Target 2 hit', tone: 'win', icon: Target },
  SL_HIT: { label: 'Stopped out', tone: 'loss', icon: ShieldAlert },
  EXPIRED: { label: 'Expired', tone: 'flat', icon: Clock },
  INVALIDATED: { label: 'Invalidated', tone: 'flat', icon: Ban },
};

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function BiasTag({ b }: { b: string | null }) {
  if (!b) return null;
  const long = b === 'LONG';
  return (
    <span className={`sg-bias ${long ? 'long' : b === 'SHORT' ? 'short' : 'flat'}`}>
      {long ? <ArrowUpRight size={11} /> : b === 'SHORT' ? <ArrowDownRight size={11} /> : null}
      {b}
    </span>
  );
}

export default function SignalsPage({ ctx }: { ctx: PageContext }) {
  const { demo } = ctx;
  const navigate = useNavigate();
  const [add, setAdd] = useState('');
  const [scanning, setScanning] = useState(false);

  const feed = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.signals(80, s)),
    [demo],
    { refreshMs: demo ? undefined : 20_000 },
  );
  const wl = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.watchlist(s)),
    [demo],
  );

  // Opening the page clears the unread badge.
  useEffect(() => {
    if (!demo) api2.signalsSeen().catch(() => {});
  }, [demo, feed.data]);

  const events = feed.data?.events || [];
  const watchlist = wl.data?.rows || [];

  async function addSym() {
    const sym = add.trim().toUpperCase();
    if (!sym) return;
    setAdd('');
    try { await api2.watchlistAdd(sym); wl.refresh(); } catch { /* ignore */ }
  }
  async function removeSym(sym: string) {
    try { await api2.watchlistRemove(sym); wl.refresh(); } catch { /* ignore */ }
  }
  async function scanNow() {
    setScanning(true);
    try { await api2.signalsScan(); feed.refresh(); } finally { setScanning(false); }
  }

  return (
    <div className="page sg">
      <PageHead
        title="Signals"
        subtitle={<>When a stock's view flips (Buy ⇄ Sell ⇄ Neutral) or a plan
          hits its target or stop, it shows up here — across your watchlist.
          <b> Analysis, not advice.</b></>}
        right={
          <button className="btn-icon" onClick={scanNow} title="Scan now">
            <RefreshCw size={15} className={scanning || feed.loading ? 'sg-spin' : ''} />
          </button>
        }
      />

      <div className="sg-grid">
        {/* Feed */}
        <div className="sg-feed">
          {demo ? (
            <div className="sg-empty">Signals are disabled in demo mode.</div>
          ) : feed.initialLoading ? (
            <div className="sg-empty">Loading signals…</div>
          ) : !events.length ? (
            <div className="sg-empty">
              <Bell size={22} />
              <p>No signals yet. Add stocks to your watchlist and they'll appear
                here the moment their view changes or a plan resolves.</p>
            </div>
          ) : (
            events.map((e: any) => {
              const m = TYPE_META[e.type] || { label: e.type, tone: 'flat', icon: Bell };
              const Icon = m.icon;
              return (
                <button key={e.id} className={`sg-item ${m.tone}`}
                  onClick={() => navigate(`/trade-plan/${e.symbol}${ctx.search}`)}>
                  <div className={`sg-ic ${m.tone}`}><Icon size={15} /></div>
                  <div className="sg-body">
                    <div className="sg-line1">
                      <span className="sg-sym">{e.symbol}</span>
                      <span className="sg-type">{m.label}</span>
                      {e.type === 'BIAS_FLIP' && (
                        <span className="sg-flip">
                          <BiasTag b={e.from_bias} /> → <BiasTag b={e.to_bias} />
                        </span>
                      )}
                      {e.type === 'NEW_SETUP' && <BiasTag b={e.to_bias} />}
                      <span className="sg-ago">{ago(e.created_at)}</span>
                    </div>
                    <div className="sg-note">{e.note}</div>
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* Watchlist manager */}
        <aside className="sg-side">
          <h2>Watching {watchlist.length ? `(${watchlist.length})` : ''}</h2>
          <div className="sg-add">
            <input value={add} onChange={(e) => setAdd(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addSym()}
              placeholder="Add ticker…" />
            <button className="btn-icon" onClick={addSym} aria-label="Add"><Plus size={16} /></button>
          </div>
          {!watchlist.length && (
            <p className="sg-side-empty">No stocks watched yet.</p>
          )}
          <ul className="sg-wl">
            {watchlist.map((r: any) => (
              <li key={r.symbol}>
                <button className="sg-wl-sym"
                  onClick={() => navigate(`/trade-plan/${r.symbol}${ctx.search}`)}>
                  {r.symbol}
                </button>
                {r.change_percent != null && (
                  <span className={`sg-wl-chg ${r.change_percent >= 0 ? 'pos' : 'neg'}`}>
                    {r.change_percent >= 0 ? '+' : ''}{num(r.change_percent, 2)}%
                  </span>
                )}
                <button className="sg-wl-rm" onClick={() => removeSym(r.symbol)}
                  aria-label={`Remove ${r.symbol}`}><X size={13} /></button>
              </li>
            ))}
          </ul>
          <p className="sg-hint">Scanned automatically every few minutes while
            the market is open.</p>
        </aside>
      </div>
    </div>
  );
}
