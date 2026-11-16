/**
 * C.1 - "My Broker": a user connects their own MetaTrader account, and sees it. Nothing else.
 *
 * /settings is admin-gated, so this cannot live there: it is its own page, reachable by any signed-in user.
 *
 * What this page will NOT do in C.1: place an order, close one, or copy anything. Connecting an account and
 * letting it trade are two separate decisions, and the second one does not exist yet. The page says so rather
 * than implying more than it does.
 */
import { useCallback, useState } from 'react';
import { AlertTriangle, Link2, Loader2, Power, RefreshCw, Trash2 } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';

const FIELD_CSS = `
.mb-field { display: block; margin: 0 0 14px; }
.mb-field .mb-label { display: block; margin-bottom: 6px; font-size: 12px; opacity: .8; }
.mb-field .inp { display: block; width: 100%; max-width: 360px; padding: 8px 10px; box-sizing: border-box; }
.mb-consent { display: flex; gap: 8px; align-items: flex-start; margin: 14px 0 16px; max-width: 520px; }
`;

export default function MyBrokerPage() {
  const info = useApi<any>((s?: AbortSignal) => api2.myMt5(s), []);
  const [login, setLogin] = useState('');
  const [server, setServer] = useState('');
  const [password, setPassword] = useState('');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const d = info.data || {};
  const acct = d.account || null;
  const storageReady = d.storage_ready !== false;

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

  return (
    <div className="page">
      <style>{FIELD_CSS}</style>
      <PageHead
        title="My Broker"
        subtitle="Connect your own MetaTrader account to see it here. Read-only."
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
        <div className="card">
          <h3>Connected account</h3>
          <table className="kv">
            <tbody>
              <tr><td>Account</td><td>{acct.login_masked}</td></tr>
              <tr><td>Server</td><td>{acct.server}</td></tr>
              <tr><td>Last check</td><td>{acct.status || 'not checked yet'}{acct.last_error ? ` — ${acct.last_error}` : ''}</td></tr>
              <tr><td>Copy trading</td><td><b>off</b> — {d.copy_trading?.note}</td></tr>
              <tr><td>Shares per trade</td><td>{acct.max_shares}</td></tr>
            </tbody>
          </table>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
            <button className="ghost-btn" disabled={busy}
              onClick={() => run(() => api2.myMt5Test(), 'Connection tested.')}>
              {busy ? <Loader2 size={12} className="spin" /> : <Link2 size={12} />} Test connection
            </button>
            <button className="ghost-btn" disabled={busy}
              onClick={() => run(() => api2.myMt5Enabled(!acct.enabled),
                acct.enabled ? 'Switched off.' : 'Switched on. Nothing trades yet in this release.')}>
              <Power size={12} /> {acct.enabled ? 'Switch off' : 'Switch on'}
            </button>
            <button className="ghost-btn" disabled={busy}
              onClick={() => { if (window.confirm('Disconnect and delete the stored password?')) run(() => api2.myMt5Disconnect(), 'Disconnected.'); }}>
              <Trash2 size={12} /> Disconnect
            </button>
          </div>
        </div>
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
              my own account and for any profit or loss in it.</span>
          </label>
          <button className="ghost-btn" disabled={busy || !storageReady || !consent || !login || !server || !password}
            onClick={() => run(() => api2.myMt5Connect({ login, server, password, consent }), 'Account connected.')}>
            {busy ? <Loader2 size={12} className="spin" /> : <Link2 size={12} />} Connect
          </button>
        </div>
      )}

      {msg && <div className="card"><p style={{ margin: 0 }}>{msg}</p></div>}
    </div>
  );
}
