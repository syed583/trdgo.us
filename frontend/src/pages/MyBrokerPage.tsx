/**
 * C.1 / C.2 - "My Broker": a user connects their own MetaTrader account, and chooses whether our intraday
 * trades are copied into it.
 *
 * /settings is admin-gated, so this cannot live there: it is its own page, reachable by any signed-in user.
 *
 * C.2 places REAL orders in the account the user connected. The page says that plainly, in the notice and
 * next to the switch, and the switch is the last of three deliberate steps - connect, consent, Auto-copy.
 * It never implies the orders are practice ones, because they are not.
 */
import { useCallback, useState } from 'react';
import { AlertTriangle, Link2, Loader2, Power, RefreshCw, Table2, Trash2 } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';

const FIELD_CSS = `
.mb-field { display: block; margin: 0 0 14px; }
.mb-field .mb-label { display: block; margin-bottom: 6px; font-size: 12px; opacity: .8; }
.mb-field .inp { display: block; width: 100%; max-width: 360px; padding: 8px 10px; box-sizing: border-box; }
.mb-consent { display: flex; gap: 8px; align-items: flex-start; margin: 14px 0 16px; max-width: 520px; }
.mb-rails { display: flex; gap: 18px; flex-wrap: wrap; margin: 10px 0 0; }
.mb-rails label { font-size: 12px; opacity: .85; }
.mb-rails input { width: 90px; padding: 6px 8px; margin-top: 4px; display: block; }
.mb-shares { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin: 8px 0 0; }
.mb-shares button.on { outline: 2px solid var(--accent, #2563eb); }
.mb-live { color: var(--danger, #b91c1c); font-weight: 600; }
.mb-log td, .mb-log th { padding: 4px 8px; font-size: 12px; white-space: nowrap; }
.mb-log td.why { white-space: normal; max-width: 320px; opacity: .85; }
`;

const SHARE_PRESETS = [10, 20, 30, 50, 100];

export default function MyBrokerPage() {
  const info = useApi<any>((s?: AbortSignal) => api2.myMt5(s), []);
  const [login, setLogin] = useState('');
  const [server, setServer] = useState('');
  const [password, setPassword] = useState('');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [preview, setPreview] = useState<any | null>(null);
  const [log, setLog] = useState<any[] | null>(null);
  const [custom, setCustom] = useState('');

  const d = info.data || {};
  const acct = d.account || null;
  const copy = d.copy_trading || {};
  const set = copy.settings || {};
  const storageReady = d.storage_ready !== false;
  const shares = Number(set.shares ?? acct?.max_shares ?? 10);

  const run = useCallback(async (fn: () => Promise<any>, ok: string) => {
    setBusy(true); setMsg(null);
    try {
      const out = await fn();
      setMsg(out?.ok === false ? (out.detail || out.reason || 'That did not work.') : ok);
      info.refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
      setPassword('');            // never leave the password sitting in the form
    }
  }, [info]);

  const setShares = (n: number) => run(() => api2.myMt5Settings({ shares: n }),
    `Every order will be ${n} shares.`);

  const toggleAutoCopy = () => {
    if (!copy.settings?.auto_copy) {
      const ok = window.confirm(
        `Switch Auto-copy ON?\n\nReal orders will be placed in your own account (${acct?.login_masked}) `
        + `automatically, ${shares} shares a trade, whenever our system trades. You are responsible for the `
        + `result. You can switch it off at any time.`);
      if (!ok) return;
    }
    run(() => api2.myMt5Settings({ auto_copy: !copy.settings?.auto_copy }),
      copy.settings?.auto_copy ? 'Auto-copy is off. No further orders will be placed.'
        : 'Auto-copy is ON. Orders will be placed in your account.');
  };

  const loadPreview = async () => {
    setBusy(true); setMsg(null);
    try { setPreview(await api2.myMt5Preview()); } catch (e) { setMsg((e as Error).message); }
    finally { setBusy(false); }
  };

  const loadLog = async () => {
    setBusy(true); setMsg(null);
    try { const r = await api2.myMt5Orders(100); setLog(r?.orders || []); }
    catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="page">
      <style>{FIELD_CSS}</style>
      <PageHead
        title="My Broker"
        subtitle="Connect your own MetaTrader account, and choose whether our trades are copied into it."
        right={<button className="ghost-btn" onClick={info.refresh}>
          <RefreshCw size={12} className={info.loading ? 'spin' : undefined} /> Refresh
        </button>}
      />

      <div className="card" style={{ borderColor: 'var(--warn, #b45309)' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
          <AlertTriangle size={16} style={{ flex: '0 0 auto', marginTop: 2 }} />
          <div>
            <b>Before you connect</b>
            <p style={{ margin: '6px 0 0' }}>{d.risk_notice}</p>
            {d.copy_notice && <p style={{ margin: '8px 0 0' }}>{d.copy_notice}</p>}
          </div>
        </div>
      </div>

      {!storageReady && (
        <div className="card">
          <b>Not available yet.</b>
          <p>This feature is not configured on the server{d.storage_problem ? ` (${d.storage_problem})` : ''}.
            Nothing can be stored until an administrator sets it up.</p>
        </div>
      )}

      {acct ? (
        <>
          <div className="card">
            <h3>Connected account</h3>
            <table className="kv">
              <tbody>
                <tr><td>Account</td><td>{acct.login_masked}</td></tr>
                <tr><td>Server</td><td>{acct.server}</td></tr>
                <tr><td>Last check</td><td>{acct.status || 'not checked yet'}{acct.last_error ? ` — ${acct.last_error}` : ''}</td></tr>
                <tr>
                  <td>Auto-copy</td>
                  <td>{copy.copying
                    ? <span className="mb-live">ON — real orders are being placed in this account</span>
                    : <b>off</b>} {copy.note ? <span style={{ opacity: .8 }}>— {copy.note}</span> : null}</td>
                </tr>
                <tr><td>Open positions</td><td>{copy.open_positions ?? 0} of {set.max_positions ?? 20}</td></tr>
                <tr><td>Realised today</td><td>{copy.realized_today ?? 0}</td></tr>
                {set.admin_paused && <tr><td>Paused</td><td>Paused by the operator. No orders are being placed.</td></tr>}
              </tbody>
            </table>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              <button className="ghost-btn" disabled={busy}
                onClick={() => run(() => api2.myMt5Test(), 'Connection tested.')}>
                {busy ? <Loader2 size={12} className="spin" /> : <Link2 size={12} />} Test connection
              </button>
              <button className="ghost-btn" disabled={busy} onClick={toggleAutoCopy}>
                <Power size={12} /> {set.auto_copy ? 'Switch Auto-copy off' : 'Switch Auto-copy ON'}
              </button>
              <button className="ghost-btn" disabled={busy}
                onClick={() => run(() => api2.myMt5Enabled(!acct.enabled),
                  acct.enabled ? 'Paused. No orders will be placed until you resume.' : 'Resumed.')}>
                <Power size={12} /> {acct.enabled ? 'Pause everything' : 'Resume'}
              </button>
              <button className="ghost-btn" disabled={busy}
                onClick={() => { if (window.confirm('Disconnect and delete the stored password?')) run(() => api2.myMt5Disconnect(), 'Disconnected.'); }}>
                <Trash2 size={12} /> Disconnect
              </button>
            </div>
          </div>

          <div className="card">
            <h3>Size</h3>
            <p style={{ margin: '0 0 4px' }}>One quantity for every stock. The lot sent to your broker is worked
              out from <i>your</i> broker's contract size for each symbol, so you never have to think in lots.</p>
            <div className="mb-shares">
              {SHARE_PRESETS.map((n) => (
                <button key={n} className={`ghost-btn${shares === n ? ' on' : ''}`} disabled={busy}
                  onClick={() => setShares(n)}>{n}</button>
              ))}
              <input className="inp" style={{ width: 90 }} value={custom} inputMode="numeric"
                placeholder="custom" aria-label="custom share count"
                onChange={(e) => setCustom(e.target.value.replace(/[^0-9]/g, ''))} />
              <button className="ghost-btn" disabled={busy || !custom}
                onClick={() => setShares(Number(custom))}>Set</button>
              <span style={{ opacity: .8, fontSize: 12 }}>now: <b>{shares}</b> shares a trade</span>
            </div>

            <div className="mb-rails">
              <label>Max open positions
                <input defaultValue={set.max_positions ?? 20} inputMode="numeric"
                  onBlur={(e) => {
                    const n = Number(e.target.value);
                    if (n && n !== set.max_positions) run(() => api2.myMt5Settings({ max_positions: n }), 'Saved.');
                  }} />
              </label>
              <label>Daily loss limit (% of your equity)
                <input defaultValue={set.daily_loss_pct ?? 1} inputMode="decimal"
                  onBlur={(e) => {
                    const n = Number(e.target.value);
                    if (n && n !== set.daily_loss_pct) run(() => api2.myMt5Settings({ daily_loss_pct: n }), 'Saved.');
                  }} />
              </label>
            </div>

            <div style={{ marginTop: 12 }}>
              <button className="ghost-btn" disabled={busy} onClick={loadPreview}>
                {busy ? <Loader2 size={12} className="spin" /> : <Table2 size={12} />} Show what will be sent
              </button>
            </div>

            {preview && (
              preview.ok === false
                ? <p style={{ marginTop: 10 }}>{preview.detail || preview.reason}</p>
                : <table className="mb-log" style={{ marginTop: 10 }}>
                  <thead><tr><th>Stock</th><th>At your broker</th><th>Shares</th><th>Lot sent</th><th /></tr></thead>
                  <tbody>
                    {(preview.rows || []).map((r: any) => (
                      <tr key={r.symbol}>
                        <td>{r.symbol}</td>
                        <td>{r.broker_symbol || '—'}</td>
                        <td>{r.shares}</td>
                        <td>{r.valid ? r.volume : '—'}</td>
                        <td className="why">{r.valid ? '' : r.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
            )}
          </div>

          <div className="card">
            <h3>My orders</h3>
            <button className="ghost-btn" disabled={busy} onClick={loadLog}>
              {busy ? <Loader2 size={12} className="spin" /> : <RefreshCw size={12} />} Load my order log
            </button>
            {log && (log.length === 0
              ? <p style={{ marginTop: 10 }}>No orders yet.</p>
              : <table className="mb-log" style={{ marginTop: 10 }}>
                <thead><tr><th>When</th><th>What</th><th>Stock</th><th>Side</th><th>Shares</th>
                  <th>Price</th><th>Slippage</th><th>P&amp;L</th><th>Why</th></tr></thead>
                <tbody>
                  {log.map((r: any, i: number) => (
                    <tr key={i}>
                      <td>{String(r.at || '').replace('T', ' ').slice(0, 19)}</td>
                      <td>{r.event}</td>
                      <td>{r.symbol}</td>
                      <td>{r.side}</td>
                      <td>{r.shares ?? '—'}</td>
                      <td>{r.price ?? '—'}</td>
                      <td>{r.slippage ?? '—'}</td>
                      <td>{r.pnl ?? '—'}</td>
                      <td className="why">{r.reason || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>)}
          </div>
        </>
      ) : (
        <div className="card">
          <h3>Connect an account</h3>
          {/* The browser was filling Server with a saved phone number and Password with a saved website
              password, because an unnamed text + password pair looks exactly like a sign-in form. Naming the
              fields something the browser has no history for, and asking it not to autofill, stops that.
              autoComplete="new-password" is the one value password managers reliably treat as "do not fill". */}
          <label className="mb-field">
            <span className="mb-label">Account number</span>
            <input className="inp" name="mt5-account" value={login} inputMode="numeric"
              autoComplete="off" autoCorrect="off" spellCheck={false} placeholder="250086"
              onChange={(e) => setLogin(e.target.value)} />
          </label>
          <label className="mb-field">
            <span className="mb-label">Server</span>
            <input className="inp" name="mt5-server" value={server}
              autoComplete="off" autoCorrect="off" spellCheck={false}
              placeholder="STGlobalMarketsLtd-Live-1"
              onChange={(e) => setServer(e.target.value)} />
          </label>
          <label className="mb-field">
            <span className="mb-label">Password</span>
            <input className="inp" name="mt5-password" type="password" value={password}
              autoComplete="new-password" placeholder="your MetaTrader password"
              onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label className="mb-consent">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>I have read the notice above. I connect this account voluntarily and I am responsible for
              my own account and for any profit or loss in it. I understand that if I switch Auto-copy on,
              real orders will be placed in this account automatically.</span>
          </label>
          <button className="ghost-btn" disabled={busy || !storageReady || !consent || !login || !server || !password}
            onClick={() => run(() => api2.myMt5Connect({ login, server, password, consent }),
              'Account connected. Nothing is copied until you switch Auto-copy on.')}>
            {busy ? <Loader2 size={12} className="spin" /> : <Link2 size={12} />} Connect
          </button>
        </div>
      )}

      {msg && <div className="card"><p style={{ margin: 0 }}>{msg}</p></div>}
    </div>
  );
}
