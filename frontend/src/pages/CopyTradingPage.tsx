/**
 * C.2 - the operator's view of copy trading: who is copying, how they are doing, and the pause.
 *
 * Operator-gated by the route AND by the server (/api/admin/copy-trading/* refuses a non-operator), so a
 * normal user who guesses the URL sees nothing. Account numbers arrive masked; no password ever reaches here.
 *
 * Pausing does not close anything. A paused user's open positions still close when our trade closes - that is
 * deliberate: the alternative is leaving somebody holding a position our book has already exited.
 */
import { useCallback, useState } from 'react';
import { Loader2, PauseCircle, PlayCircle, RefreshCw } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';

const CSS = `
.ct td, .ct th { padding: 5px 10px; font-size: 12px; white-space: nowrap; }
.ct tr.paused { opacity: .6; }
.ct .on { color: var(--danger, #b91c1c); font-weight: 600; }
`;

export default function CopyTradingPage() {
  const info = useApi<any>((s?: AbortSignal) => api2.copyUsers(s), []);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [orders, setOrders] = useState<{ user: string; rows: any[] } | null>(null);

  const d = info.data || {};
  const users = d.users || [];
  // C.2a: never state a status we do not have. The previous version printed "worker: NOT running" whenever
  // `worker_running` was undefined - which is what a FAILED REQUEST looks like - and that is exactly what
  // happened when the route was not mounted: the page reported the engine as down while it was fine.
  const answered = !!info.data && d.ok !== false;
  const workerText = !answered
    ? (info.loading ? 'checking...' : 'unknown - this page could not reach the server')
    : (d.worker_running ? 'running' : `NOT running${d.worker_error ? ` - ${d.worker_error}` : ''}`);

  const run = useCallback(async (fn: () => Promise<any>, ok: string) => {
    setBusy(true); setMsg(null);
    try {
      const out = await fn();
      setMsg(out?.ok === false ? (out.detail || out.reason || 'That did not work.') : ok);
      info.refresh();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }, [info]);

  const showOrders = async (username: string) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api2.copyUserOrders(username);
      setOrders({ user: username, rows: r?.orders || [] });
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  };

  const copying = users.filter((u: any) => u.copying).length;

  return (
    <div className="page">
      <style>{CSS}</style>
      <PageHead
        title="Copy trading"
        subtitle={answered
          ? `${copying} of ${users.length} connected accounts are copying our trades.`
          : 'Could not read the copy-trading status from the server.'}
        right={<button className="ghost-btn" onClick={info.refresh}>
          <RefreshCw size={12} className={info.loading ? 'spin' : undefined} /> Refresh
        </button>}
      />

      <div className="card">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="ghost-btn" disabled={busy}
            onClick={() => { if (window.confirm('Pause copy trading for EVERY user? Open positions still close with ours.')) run(() => api2.copyPause({ all: true, paused: true }), 'Every user is paused.'); }}>
            <PauseCircle size={12} /> Pause everyone
          </button>
          <button className="ghost-btn" disabled={busy}
            onClick={() => run(() => api2.copyPause({ all: true, paused: false }), 'Every user is resumed.')}>
            <PlayCircle size={12} /> Resume everyone
          </button>
          <span style={{ fontSize: 12, alignSelf: 'center' }}
            className={answered && !d.worker_running ? 'on' : undefined}>
            worker: {workerText} · queue: {answered ? d.queue_depth ?? 0 : '—'}
          </span>
        </div>
      </div>

      <div className="card">
        <table className="ct">
          <thead><tr><th>User</th><th>Account</th><th>Server</th><th>Copying</th><th>Shares</th>
            <th>Open</th><th>Max</th><th>Daily limit</th><th>Realised today</th><th>Last check</th><th /></tr></thead>
          <tbody>
            {users.length === 0 && <tr><td colSpan={11}>No user has connected an account yet.</td></tr>}
            {users.map((u: any) => (
              <tr key={u.username} className={u.admin_paused ? 'paused' : undefined}>
                <td>{u.username}</td>
                <td>{u.login_masked}</td>
                <td>{u.server}</td>
                <td className={u.copying ? 'on' : undefined}>
                  {u.copying ? 'ON' : (u.admin_paused ? 'paused by you' : (u.auto_copy ? 'off (user paused)' : 'off'))}
                </td>
                <td>{u.shares}</td>
                <td>{u.open_positions}</td>
                <td>{u.max_positions}</td>
                <td>{u.daily_loss_pct}%</td>
                <td>{u.realized_today}</td>
                <td>{u.status || '—'}{u.last_error ? ` — ${u.last_error}` : ''}</td>
                <td>
                  <button className="ghost-btn" disabled={busy} onClick={() => showOrders(u.username)}>Orders</button>
                  {' '}
                  <button className="ghost-btn" disabled={busy}
                    onClick={() => run(() => api2.copyPause({ username: u.username, paused: !u.admin_paused }),
                      u.admin_paused ? `${u.username} resumed.` : `${u.username} paused.`)}>
                    {u.admin_paused ? 'Resume' : 'Pause'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {orders && (
        <div className="card">
          <h3>{orders.user} — last orders</h3>
          {orders.rows.length === 0 ? <p>No orders yet.</p> : (
            <table className="ct">
              <thead><tr><th>When</th><th>What</th><th>Stock</th><th>At broker</th><th>Side</th>
                <th>Shares</th><th>Lot</th><th>Price</th><th>P&amp;L</th><th>Why</th></tr></thead>
              <tbody>
                {orders.rows.map((r: any, i: number) => (
                  <tr key={i}>
                    <td>{String(r.at || '').replace('T', ' ').slice(0, 19)}</td>
                    <td>{r.event}</td><td>{r.symbol}</td><td>{r.broker_symbol || '—'}</td>
                    <td>{r.side}</td><td>{r.shares ?? '—'}</td><td>{r.volume ?? '—'}</td>
                    <td>{r.price ?? '—'}</td><td>{r.pnl ?? '—'}</td>
                    <td style={{ whiteSpace: 'normal', maxWidth: 340 }}>{r.reason || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {!answered && !info.loading && (
        <div className="card" style={{ borderColor: 'var(--danger, #b91c1c)' }}>
          <b>This page could not reach the copy-trading status.</b>
          <p style={{ margin: '6px 0 0' }}>{info.error ? String(info.error) : 'The request failed.'} Until it
            answers, treat the worker state above as unknown rather than as off.</p>
        </div>
      )}

      {msg && <div className="card"><p style={{ margin: 0 }}>{msg}</p></div>}
    </div>
  );
}
