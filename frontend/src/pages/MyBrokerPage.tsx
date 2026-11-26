/**
 * "My Broker" — a user connects their own MetaTrader account and decides whether our intraday signals are
 * traded in it. Real orders, their money, their responsibility.
 *
 * E (redesign). The page had grown by accretion: a key/value table, four identical grey buttons, a wall of
 * consent text above everything, and the one fact that matters — is my account trading? — rendered as the
 * word "off" in a table row. That last one cost the owner an afternoon (see C.2b). So the shape now answers
 * that question first, in a status band with a coloured badge, and only then offers the three things a person
 * actually does here: set the size, see the activity, manage the account.
 *
 * Behaviour is deliberately unchanged from C.2b. Two rules earned the hard way are preserved exactly:
 *   - a status is never shown without its reason (`blocked_reason` from the server);
 *   - the connect form is named so password managers do not fill it with a phone number (C.1a).
 */
import { useCallback, useState } from 'react';
import {
  Activity, AlertTriangle, Layers, Link2, Loader2, Power, RefreshCw, Table2, Trash2, TrendingUp, Wallet,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import MyBrokerSymbols from './MyBrokerSymbols';
import heroArt from '../assets/analysis-hero.webp';
import './my-broker.css';

const SHARE_PRESETS = [10, 20, 30, 50, 100];

function Badge({ state, children }: { state: 'on' | 'off' | 'warn'; children: React.ReactNode }) {
  return <span className={`mb-badge ${state}`}><i className="dot" />{children}</span>;
}

export default function MyBrokerPage() {
  const info = useApi<any>((s?: AbortSignal) => api2.myMt5(s), []);
  const [login, setLogin] = useState('');
  const [server, setServer] = useState('');
  const [password, setPassword] = useState('');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; bad: boolean } | null>(null);
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
      const bad = out?.ok === false;
      setMsg({ text: bad ? (out.detail || out.reason || 'That did not work.') : ok, bad });
      info.refresh();
    } catch (e) {
      setMsg({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(false);
      setPassword('');            // never leave the password sitting in the form
    }
  }, [info]);

  const setShares = (n: number) => run(() => api2.myMt5Settings({ shares: n }),
    `Every order will be ${n} shares.`);

  const toggleAutoTrade = () => {
    if (!set.auto_copy) {
      const ok = window.confirm(
        `Switch Auto-trade ON?\n\nReal orders will be placed in your own account (${acct?.login_masked}) `
        + `automatically, ${shares} shares a trade, whenever our system trades. You are responsible for the `
        + `result. You can switch it off at any time.`);
      if (!ok) return;
    }
    run(() => api2.myMt5Settings({ auto_copy: !set.auto_copy }),
      set.auto_copy ? 'Auto-trade is off. No further orders will be placed.'
        : 'Auto-trade is ON and this account is no longer paused. Orders will be placed in your account.');
  };

  const load = async (what: 'preview' | 'log') => {
    setBusy(true); setMsg(null);
    try {
      if (what === 'preview') setPreview(await api2.myMt5Preview());
      else setLog((await api2.myMt5Orders(100))?.orders || []);
    } catch (e) { setMsg({ text: (e as Error).message, bad: true }); } finally { setBusy(false); }
  };

  // The three states the band can be in. "warn" is for a connected account that is NOT trading for a reason
  // the user can act on - which is the case C.2b existed to make visible.
  const bandState: 'on' | 'off' | 'warn' = copy.copying ? 'on' : (acct ? 'warn' : 'off');

  return (
    <div className="page mb">
      {/* Kit U: the redesigned shell's hero and tiles. Presentation only - the stylesheet shows them in the
          new design and hides the classic page head there; the classic design never renders them visibly.
          Nothing below this block changed: every handler, confirmation and field name is as it was. */}
      <section className="nd-hero mb-hero">
        <img className="nd-hero-img" src={heroArt} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow"><Link2 size={13} /> Your own account</span>
          <h1>My <em>Broker</em></h1>
          <p>Connect your own MetaTrader account and choose whether our trades are placed in it.
            Real orders, your money, your responsibility.</p>
          <button className="mb-btn mb-hero-refresh" onClick={info.refresh} disabled={info.loading}>
            <RefreshCw size={13} className={info.loading ? 'spin' : undefined} /> Refresh
          </button>
        </div>
      </section>
      {acct && (
        <div className="nd-stats mb-tiles">
          <div className="nd-stat">
            <span className={`nd-stat-ico ${copy.copying ? 'green' : 'amber'}`}><Power size={18} /></span>
            <div><b className={copy.copying ? 'up' : undefined}>{copy.copying ? 'On' : 'Off'}</b><span>Auto-trade</span></div>
          </div>
          <div className="nd-stat">
            <span className="nd-stat-ico blue"><Layers size={18} /></span>
            <div><b>{shares}</b><span>Shares a trade</span></div>
          </div>
          <div className="nd-stat">
            <span className="nd-stat-ico violet"><Activity size={18} /></span>
            <div><b>{copy.open_positions ?? 0} <small>of {set.max_positions ?? 20}</small></b><span>Open positions</span></div>
          </div>
          <div className="nd-stat">
            <span className="nd-stat-ico blue"><TrendingUp size={18} /></span>
            <div><b className={(copy.realized_today ?? 0) > 0 ? 'up' : (copy.realized_today ?? 0) < 0 ? 'down' : undefined}>
              {copy.realized_today ?? 0}</b><span>Realised today</span></div>
          </div>
        </div>
      )}
      <PageHead
        title="My Broker"
        subtitle="Connect your own MetaTrader account and choose whether our trades are placed in it."
        right={<button className="mb-btn" onClick={info.refresh} disabled={info.loading}>
          <RefreshCw size={13} className={info.loading ? 'spin' : undefined} /> Refresh
        </button>}
      />

      {acct && (
        <div className={`mb-band ${bandState}`}>
          <Badge state={bandState}>{copy.copying ? 'Auto-trade on' : 'Not trading'}</Badge>
          <div className="mb-band-text">
            <b>{copy.copying
              ? 'Real orders are being placed in your account'
              : 'No orders are being placed'}</b>
            <span>{copy.copying
              ? `${shares} shares a trade · account ${acct.login_masked} · ${acct.server}`
              : copy.blocked_reason /* never a status without its reason - see C.2b */}</span>
          </div>
          <button className={`mb-btn ${set.auto_copy ? '' : 'primary'}`} disabled={busy}
            onClick={toggleAutoTrade}>
            <Power size={13} /> {set.auto_copy ? 'Switch Auto-trade off' : 'Switch Auto-trade ON'}
          </button>
        </div>
      )}

      {!storageReady && (
        <div className="mb-msg bad">
          <b>Not available yet.</b> This feature is not configured on the server
          {d.storage_problem ? ` (${d.storage_problem})` : ''}. Nothing can be stored until an administrator
          sets it up.
        </div>
      )}

      <details className="mb-notice">
        <summary><AlertTriangle size={14} /> Before you connect — your money, your responsibility</summary>
        <div>
          <p>{d.risk_notice}</p>
          {d.copy_notice && <p>{d.copy_notice}</p>}
        </div>
      </details>

      {acct ? (
        <>
          <div className="mb-card">
            <h3>Size</h3>
            <p className="mb-hint">One quantity for every stock. The lot sent to your broker is worked out
              from <i>your</i> broker&apos;s contract size for each symbol, so you never have to think in lots.</p>
            <div className="mb-shares">
              {SHARE_PRESETS.map((n) => (
                <button key={n} className={`mb-chip${shares === n ? ' on' : ''}`} disabled={busy}
                  onClick={() => setShares(n)}>{n}</button>
              ))}
              <input className="mb-custom" value={custom} inputMode="numeric" placeholder="custom"
                aria-label="custom share count"
                onChange={(e) => setCustom(e.target.value.replace(/[^0-9]/g, ''))} />
              <button className="mb-btn" disabled={busy || !custom}
                onClick={() => setShares(Number(custom))}>Set</button>
              <span className="mb-now">now <b>{shares}</b> shares a trade</span>
            </div>

            <div className="mb-rails">
              <div className="mb-rail">
                <label htmlFor="mb-maxpos">Most positions at once</label>
                <input id="mb-maxpos" defaultValue={set.max_positions ?? 20} inputMode="numeric"
                  onBlur={(e) => {
                    const n = Number(e.target.value);
                    if (n && n !== set.max_positions) run(() => api2.myMt5Settings({ max_positions: n }), 'Saved.');
                  }} />
                <span className="note">New trades stop above this.</span>
              </div>
              <div className="mb-rail">
                <label htmlFor="mb-loss">Daily loss limit (% of your equity)</label>
                <input id="mb-loss" defaultValue={set.daily_loss_pct ?? 1} inputMode="decimal"
                  onBlur={(e) => {
                    const n = Number(e.target.value);
                    if (n && n !== set.daily_loss_pct) run(() => api2.myMt5Settings({ daily_loss_pct: n }), 'Saved.');
                  }} />
                <span className="note">No new trades once you lose this much in a day.</span>
              </div>
            </div>

            <div className="mb-actions">
              <button className="mb-btn" disabled={busy} onClick={() => load('preview')}>
                {busy ? <Loader2 size={13} className="spin" /> : <Table2 size={13} />} Show what will be sent
              </button>
            </div>

            {preview && (preview.ok === false
              ? <p className="mb-empty">{preview.detail || preview.reason}</p>
              : <table className="mb-table">
                <thead><tr><th>Stock</th><th>At your broker</th><th className="num">Shares</th>
                  <th className="num">Lot sent</th><th /></tr></thead>
                <tbody>
                  {(preview.rows || []).map((r: any) => (
                    <tr key={r.symbol}>
                      <td>{r.symbol}</td>
                      <td>{r.broker_symbol || '—'}</td>
                      <td className="num">{r.shares}</td>
                      <td className="num">{r.valid ? r.volume : '—'}</td>
                      <td className="why">{r.valid ? '' : r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>)}
          </div>

          {/* G: what the share size applies to. Placed after Size and before Activity because that is the
              order a person decides in: how much, on what, then what happened. */}
          <MyBrokerSymbols onChanged={info.refresh} />

          <div className="mb-card">
            <h3>Activity</h3>
            <div className="mb-facts">
              <div className="mb-fact"><span className="k">Open positions</span>
                <span className="v">{copy.open_positions ?? 0} of {set.max_positions ?? 20}</span></div>
              <div className="mb-fact"><span className="k">Realised today</span>
                <span className={`v ${(copy.realized_today ?? 0) > 0 ? 'mb-pos' : (copy.realized_today ?? 0) < 0 ? 'mb-neg' : ''}`}>
                  {copy.realized_today ?? 0}</span></div>
              <div className="mb-fact"><span className="k">Shares a trade</span>
                <span className="v">{shares}</span></div>
            </div>
            <div className="mb-actions">
              <button className="mb-btn" disabled={busy} onClick={() => load('log')}>
                {busy ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />} Load my orders
              </button>
            </div>
            {log && (log.length === 0
              ? <p className="mb-empty">No orders yet.</p>
              : <table className="mb-table">
                <thead><tr><th>When</th><th>What</th><th>Stock</th><th>Side</th>
                  <th className="num">Shares</th><th className="num">Price</th><th className="num">P&amp;L</th>
                  <th>Why</th></tr></thead>
                <tbody>
                  {log.map((r: any, i: number) => (
                    <tr key={i}>
                      <td>{String(r.at || '').replace('T', ' ').slice(0, 16)}</td>
                      <td>{r.event}</td>
                      <td>{r.symbol}</td>
                      <td>{r.side}</td>
                      <td className="num">{r.shares ?? '—'}</td>
                      <td className="num">{r.price ?? '—'}</td>
                      <td className={`num ${(r.pnl ?? 0) > 0 ? 'mb-pos' : (r.pnl ?? 0) < 0 ? 'mb-neg' : ''}`}>
                        {r.pnl ?? '—'}</td>
                      <td className="why">{r.reason || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>)}
          </div>

          <div className="mb-card">
            <h3>Account</h3>
            <div className="mb-facts">
              <div className="mb-fact"><span className="k">Account</span><span className="v">{acct.login_masked}</span></div>
              <div className="mb-fact"><span className="k">Server</span><span className="v">{acct.server}</span></div>
              <div className="mb-fact"><span className="k">Last check</span>
                <span className="v">{acct.status || 'not checked yet'}</span></div>
            </div>
            {acct.last_error && <p className="mb-empty">{acct.last_error}</p>}
            <div className="mb-actions">
              <button className="mb-btn" disabled={busy}
                onClick={() => run(() => api2.myMt5Test(), 'Connection tested.')}>
                <Link2 size={13} /> Test connection
              </button>
              <button className="mb-btn" disabled={busy}
                onClick={() => run(() => api2.myMt5Enabled(!acct.enabled),
                  acct.enabled ? 'Paused. No orders will be placed until you resume.' : 'Resumed.')}>
                <Power size={13} /> {acct.enabled ? 'Pause trading' : 'Resume trading'}
              </button>
              <button className="mb-btn danger" disabled={busy}
                onClick={() => { if (window.confirm('Disconnect and delete the stored password?')) run(() => api2.myMt5Disconnect(), 'Disconnected.'); }}>
                <Trash2 size={13} /> Disconnect
              </button>
            </div>
          </div>
        </>
      ) : (
        <div className="mb-card">
          <h3><Wallet size={15} style={{ verticalAlign: '-2px', marginRight: 6 }} />Connect an account</h3>
          <p className="mb-hint">Your MetaTrader account number, its server, and the password you sign in
            with. The password is encrypted and never shown again.</p>
          {/* The browser was filling Server with a saved phone number and Password with a saved website
              password, because an unnamed text + password pair looks exactly like a sign-in form. Naming the
              fields something the browser has no history for, and asking it not to autofill, stops that.
              autoComplete="new-password" is the one value password managers reliably treat as "do not fill". */}
          <div className="mb-form">
            <div className="mb-field">
              <label htmlFor="mb-account">Account number</label>
              <input id="mb-account" name="mt5-account" value={login} inputMode="numeric"
                autoComplete="off" autoCorrect="off" spellCheck={false} placeholder="250086"
                onChange={(e) => setLogin(e.target.value)} />
            </div>
            <div className="mb-field">
              <label htmlFor="mb-server">Server</label>
              <input id="mb-server" name="mt5-server" value={server}
                autoComplete="off" autoCorrect="off" spellCheck={false}
                placeholder="STGlobalMarketsLtd-Live-1"
                onChange={(e) => setServer(e.target.value)} />
            </div>
            <div className="mb-field">
              <label htmlFor="mb-password">Password</label>
              <input id="mb-password" name="mt5-password" type="password" value={password}
                autoComplete="new-password" placeholder="your MetaTrader password"
                onChange={(e) => setPassword(e.target.value)} />
            </div>
            <label className="mb-consent">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>I have read the notice above. I connect this account voluntarily and I am responsible for
                my own account and for any profit or loss in it. I understand that if I switch Auto-trade on,
                real orders will be placed in this account automatically.</span>
            </label>
            <div>
              <button className="mb-btn primary"
                disabled={busy || !storageReady || !consent || !login || !server || !password}
                onClick={() => run(() => api2.myMt5Connect({ login, server, password, consent }),
                  'Account connected. Nothing is traded until you switch Auto-trade on.')}>
                {busy ? <Loader2 size={13} className="spin" /> : <Link2 size={13} />} Connect
              </button>
            </div>
          </div>
        </div>
      )}

      {msg && <div className={`mb-msg${msg.bad ? ' bad' : ''}`}>{msg.text}</div>}
    </div>
  );
}
