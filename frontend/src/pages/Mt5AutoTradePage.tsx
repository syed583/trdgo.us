import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { AlertTriangle, Info, RefreshCw } from 'lucide-react';
import { mt5 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead } from './shared';
import './smart-move.css';

/** MT5 auto-trading (operator account only): account connection, symbol mapping, audit report. Everything shown here comes
 * from the server's own state files; the page never computes a trade and never sees a password after submit. */

const TABS = [
  { to: '/mt5', label: 'Account', testid: 'tab-mt5-account' },
  { to: '/mt5/symbols', label: 'Symbol mapping', testid: 'tab-mt5-symbols' },
  { to: '/mt5/audit', label: 'Audit report', testid: 'tab-mt5-audit' },
];

function Tabs() {
  const { pathname } = useLocation();
  return (
    <div className="sm-tabs" role="tablist" aria-label="MT5 auto-trading">
      {TABS.map((t) => <Link key={t.to} to={t.to} role="tab" aria-selected={pathname === t.to} className={`sm-tab ${pathname === t.to ? 'on' : ''}`} data-testid={t.testid}>{t.label}</Link>)}
    </div>
  );
}

function SwitchBanner({ st }: { st: any }) {
  if (!st) return null;
  return (
    <div className={`sm-q-banner ${st.auto_trade && !st.paused ? '' : 'stale'}`} role="note" data-testid="mt5-switch">
      <Info size={16} />
      <div>
        <b>AUTO_TRADE = {st.auto_trade ? '1 (ON)' : '0 (OFF)'}</b>{st.paused ? ' · entries PAUSED by the operator' : ''}{st.halted ? ' · HALTED (kill switch file present)' : ''}{st.dry_run ? ' · DRY RUN: orders are logged, nothing is sent' : ''}{st.margin_mode === 'NETTING' ? ' · NETTING account: orders refused, HEDGING is required' : ''}
        {' · '}{st.provider || 'mtapi.io MT5 Client REST API'}
        <div className="sm-q-muted">The master switch is the server setting AUTO_TRADE (default 0). With it off nothing is ever sent, whatever this page shows. Only MAPPED symbols trade; quantity = the Strategy Trades share count rounded down to the broker step.</div>
      </div>
    </div>
  );
}

export function Mt5AccountView() {
  const st = useApi<any>((s) => mt5.status(s), [], { refreshMs: 60_000 });
  const d = st.data;
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [server, setServer] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const srv = server || d?.server || d?.default_server || 'STGlobalMarketsLtd-Live-1';
  const url = baseUrl || d?.base_url || 'https://mt5.mtapi.io/';
  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setMsg(null);
    try { const r = await fn(); setMsg(r?.status === 'OK' ? 'Done.' : `${r?.reason || 'FAILED'}: ${r?.detail || r?.last_error || ''}`); setPassword(''); st.refresh(); }
    catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  };
  return (
    <div className="sm" data-testid="mt5-account-view">
      <PageHead title="MT5 auto-trading — Account" subtitle="Connect the operator's MetaTrader 5 account (demo or live) through the cloud MT API: no terminal on the server. Credentials are stored on the server in an owner-only file and never shown again." />
      <Tabs />
      {st.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {st.error}</div>}
      <SwitchBanner st={d} />
      {d && (
        <div className="sm-summary" data-testid="mt5-status">
          <div className="sm-card"><div className="sm-card-k">Connection</div><div className="sm-card-v">{d.connected ? <span className="sm-q-badge green">Connected</span> : <span className="sm-q-badge amber">{d.last_error ? 'Failed' : 'Not connected'}</span>}</div></div>
          <div className="sm-card"><div className="sm-card-k">Account</div><div className="sm-card-v">{d.account?.login ?? '—'}</div><div className="sm-q-muted">{d.account ? `${d.account.account_kind ?? ''} · ${d.account.company ?? ''} · ${d.account.currency ?? ''}` : (d.credentials_saved ? 'credentials saved' : 'no credentials')}</div></div>
          <div className="sm-card"><div className="sm-card-k">Server</div><div className="sm-card-v" style={{ fontSize: 14 }}>{d.server}</div></div>
          <div className="sm-card"><div className="sm-card-k">Balance / equity</div><div className="sm-card-v">{d.account ? `${d.account.balance} / ${d.account.equity}` : '—'}</div></div>
          <div className="sm-card"><div className="sm-card-k">Open MT5 positions (ours)</div><div className="sm-card-v">{typeof d.open_positions === 'number' ? d.open_positions : (d.open_positions?.length ?? 0)} / {d.settings?.max_open_positions ?? '—'}</div></div>
          <div className="sm-card"><div className="sm-card-k">Margin mode</div><div className="sm-card-v">{d.margin_mode ? <span className={`sm-q-badge ${d.margin_mode === 'HEDGING' ? 'green' : d.margin_mode === 'NETTING' ? 'red' : 'amber'}`} data-testid="mt5-margin-mode">{d.margin_mode}</span> : <span className="sm-q-badge amber" data-testid="mt5-margin-mode">not reported</span>}</div><div className="sm-q-muted">HEDGING required for one order per model</div></div>
          <div className="sm-card"><div className="sm-card-k">Pending closes</div><div className="sm-card-v" data-testid="mt5-pending">{d.pending_close?.length ?? 0}</div><div className="sm-q-muted">{d.pending_close?.length ? `broker refused (market closed); retried from 09:31 ET: ${d.pending_close.map((p: any) => p.symbol).join(', ')}` : 'EOD sweep 15:45-15:58 ET on the MT5 clock'}</div></div>
          <div className="sm-card"><div className="sm-card-k">Realized today</div><div className="sm-card-v" data-testid="mt5-daily">{d.daily ? `${d.daily.realized ?? 0}` : '—'}</div><div className="sm-q-muted">{d.daily?.loss_limit_usd != null ? `limit −${d.daily.loss_limit_usd} (${d.settings?.daily_loss_pct}% of ${d.daily.loss_basis ?? 'balance'} ${d.daily.loss_basis_value ?? ''})${d.daily.limit_reached ? ' · REACHED: no new orders today' : ''}` : 'limit needs a positive balance or equity'}</div></div>
        </div>
      )}
      {d?.last_error && !d.connected && <div className="sm-fresh stale" role="alert" data-testid="mt5-last-error"><AlertTriangle size={14} /> Last connection error: {d.last_error}</div>}
      <form className="sm-filters" onSubmit={(e) => { e.preventDefault(); act(() => mt5.connect({ login, password, server: srv, base_url: url })); }} data-testid="mt5-connect-form" style={{ flexWrap: 'wrap', gap: 8 }}>
        <label>MT API endpoint <input value={url} onChange={(e) => setBaseUrl(e.target.value)} style={{ width: 200 }} /></label>
        <label>Account ID <input value={login} onChange={(e) => setLogin(e.target.value)} inputMode="numeric" autoComplete="off" required /></label>
        <label>Password <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required /></label>
        <label>Server <input value={srv} onChange={(e) => setServer(e.target.value)} /></label>
        <button className="ghost-btn" type="submit" disabled={busy}>Connect</button>
        <button className="ghost-btn" type="button" disabled={busy} onClick={() => act(() => mt5.reconnect())}>Reconnect (saved)</button>
        <button className="ghost-btn" type="button" disabled={busy || !d?.connected} onClick={() => act(() => mt5.disconnect(false))}>Disconnect</button>
        <button className="ghost-btn" type="button" disabled={busy} onClick={() => { if (window.confirm('Forget the saved MT5 credentials on the server?')) act(() => mt5.disconnect(true)); }}>Disconnect &amp; forget</button>
        <button className="ghost-btn" type="button" disabled={busy} onClick={() => act(() => mt5.pause(!d?.paused))}>{d?.paused ? 'Resume entries' : 'Pause entries'}</button>
        <button className="ghost-btn" type="button" disabled={busy || !d?.connected} data-testid="mt5-close-all" onClick={() => { if (window.confirm('Close every open MT5 position of ours now? Closes the broker refuses (market closed) are queued and retried from 09:31 ET.')) act(() => mt5.closeAll()); }}>Close our open MT5 positions</button>
      </form>
      <Mt5SettingsForm st={d} onSaved={() => st.refresh()} />
      <form className="sm-filters" style={{ display: 'none' }}>
        <button className="ghost-btn" type="button" disabled={busy || !d?.connected} onClick={() => act(() => mt5.reconcile())}>Reconcile with broker</button>
        <button className="ghost-btn" type="button" onClick={() => st.refresh()}><RefreshCw size={14} /> Refresh</button>
      </form>
      {msg && <div className="sm-fresh" role="status" data-testid="mt5-msg">{msg}</div>}
      {d?.terminal && <div className="sm-q-muted">Terminal: {d.terminal.name} build {d.terminal.build} · connected {String(d.terminal.connected)} · trade allowed {String(d.terminal.trade_allowed)} · ping {d.terminal.ping_last} µs</div>}
      {d?.open_positions?.length > 0 && (
        <div className="sm-table-wrap"><table className="sm-table sm-q-table" data-testid="mt5-open-table">
          <thead><tr><th>Paper trade</th><th>Symbol</th><th>Broker symbol</th><th>Side</th><th>Volume</th><th>Ticket</th><th>Price</th><th>SL</th><th>TP</th><th>Opened</th></tr></thead>
          <tbody>{d.open_positions.map((p: any) => <tr key={p.trade_id}><td>#{p.trade_id} {p.strategy}</td><td><b>{p.symbol}</b></td><td>{p.broker_symbol}</td><td>{p.side}</td><td>{p.volume}</td><td>{p.ticket}</td><td>{p.price}</td><td>{p.sl}</td><td>{p.tp}</td><td>{p.opened_at}</td></tr>)}</tbody>
        </table></div>
      )}
    </div>
  );
}

/** Operator settings (admin / MT5 page, no deploy): shares per MT5 order, max open positions, daily loss % of balance. Validated and audited by the server. */
export function Mt5SettingsForm({ st, onSaved }: { st: any; onSaved?: () => void }) {
  const cur = st?.settings || {};
  const [shares, setShares] = useState<string>('');
  const [maxOpen, setMaxOpen] = useState<string>('');
  const [lossPct, setLossPct] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null);
    const patch: Record<string, number> = {};
    if (shares !== '') patch.shares_per_trade = Number(shares);
    if (maxOpen !== '') patch.max_open_positions = Number(maxOpen);
    if (lossPct !== '') patch.daily_loss_pct = Number(lossPct);
    try {
      const r: any = await mt5.saveSettings(patch);
      setMsg(r?.ok ? `Saved: ${Object.entries(r.changed || {}).map(([k, v]: any) => `${k} ${v.from} → ${v.to}`).join(', ') || 'no change'}` : `Not saved: ${Object.entries(r?.errors || {}).map(([k, v]) => `${k} ${v}`).join('; ')}`);
      if (r?.ok) { setShares(''); setMaxOpen(''); setLossPct(''); onSaved?.(); }
    } catch (err: any) { setMsg(String(err?.message || err)); } finally { setBusy(false); }
  };
  return (
    <form className="sm-filters" onSubmit={save} data-testid="mt5-settings-form">
      <b>MT5 order settings</b>
      <label>Shares per trade <input value={shares} placeholder={String(cur.shares_per_trade ?? 10)} onChange={(e) => setShares(e.target.value)} inputMode="numeric" style={{ width: 70 }} data-testid="mt5-shares" /></label>
      <label>Max open positions <input value={maxOpen} placeholder={String(cur.max_open_positions ?? 20)} onChange={(e) => setMaxOpen(e.target.value)} inputMode="numeric" style={{ width: 70 }} /></label>
      <label>Daily loss limit % of balance <input value={lossPct} placeholder={String(cur.daily_loss_pct ?? 1)} onChange={(e) => setLossPct(e.target.value)} inputMode="decimal" style={{ width: 70 }} /></label>
      <button className="ghost-btn" type="submit" disabled={busy || (shares === '' && maxOpen === '' && lossPct === '')}>Save</button>
      <span className="sm-q-muted">current: {cur.shares_per_trade ?? 10} shares · max {cur.max_open_positions ?? 20} open · daily loss {cur.daily_loss_pct ?? 1}%{st?.dry_run ? ' · DRY RUN' : ''}</span>
      {msg && <div className="sm-fresh" role="status" data-testid="mt5-settings-msg">{msg}</div>}
    </form>
  );
}

export function Mt5SymbolsView() {
  const [polling, setPolling] = useState(false);
  const sy = useApi<any>((s) => mt5.symbols(s), [], { refreshMs: polling ? 5_000 : 120_000 });
  const sz = useApi<any>((s) => mt5.sizing(s), [], { refreshMs: 120_000 });
  const sizingHead = sz.data ? `${sz.data.shares_per_trade} shares per order · ${sz.data.valid} valid / ${sz.data.invalid} invalid of ${sz.data.total}` : '';
  const sizing: Record<string, any> = Object.fromEntries(((sz.data?.rows || []) as any[]).map((r) => [r.symbol, r]));
  const d = sy.data;
  const job = d?.automap;
  if (job && polling && !job.running) setPolling(false);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true); setMsg(null);
    try { const r = await fn(); setMsg(r?.status === 'OK' ? (r?.summary ? `${ok}: ${r.summary}` : ok) : `${r?.reason || 'FAILED'}: ${r?.detail || ''}`); sy.refresh(); }
    catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  };
  return (
    <div className="sm" data-testid="mt5-symbols-view">
      <PageHead title="MT5 auto-trading — Symbol mapping" subtitle={`Every Strategy Trades symbol and the broker symbol it trades as. An UNMAPPED, UNVERIFIED or EXCLUDED symbol never sends an order; the auto-map skips excluded symbols.${sizingHead ? ` Sizing: ${sizingHead} (volume = shares / contract size, rounded to the step; an inexact share count is never sent).` : ''}`} />
      <Tabs />
      {sy.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {sy.error}</div>}
      {d && (
        <>
          <div className="sm-filters">
            <b data-testid="mt5-map-summary">{d.summary}</b>
            <button className="ghost-btn" disabled={busy || !d.connected || job?.running} onClick={() => { setPolling(true); act(() => mt5.automap(), 'Auto-map started'); }} title={d.connected ? '' : 'connect the account first'}>{job?.running ? `Auto-mapping ${job.done}/${job.total}…` : 'Auto-map with the broker'}</button>
            {job?.finished_at && !job.running && <span className="sm-q-muted" data-testid="mt5-automap-result">Last auto-map: {job.mapped?.length ?? 0} mapped, {job.unmapped?.length ?? 0} not on the broker{job.broker_symbols != null ? ` (broker lists ${job.broker_symbols} symbols)` : ''}{job.error ? ` · ${job.error}` : ''}</span>}
            <button className="ghost-btn" onClick={() => sy.refresh()}><RefreshCw size={14} /> Reload</button>
            <button className="ghost-btn" disabled={busy || !d?.connected} data-testid="mt5-refresh-rules" onClick={() => act(async () => { const r: any = await mt5.refreshRules(); sz.refresh(); return r; }, 'broker rules refreshed')}>Refresh broker rules</button>
            {!d.connected && <span className="sm-q-muted">Not connected: new mappings are saved UNVERIFIED and will not trade until verified.</span>}
          </div>
          {msg && <div className="sm-fresh" role="status" data-testid="mt5-map-msg">{msg}</div>}
          <div className="sm-table-wrap"><table className="sm-table sm-q-table" data-testid="mt5-map-table">
            <thead><tr><th>Software symbol</th><th>Broker symbol</th><th>Status</th><th>Min / step</th><th>Contract size</th><th>Volume sent</th><th>= shares</th><th>Valid</th><th>Trade mode</th><th>Note</th><th>Override</th></tr></thead>
            <tbody>{d.rows.map((r: any) => (
              <tr key={r.symbol} data-testid="mt5-map-row" data-status={r.status}>
                <td><b>{r.symbol}</b></td>
                <td>{r.broker_symbol || '—'}</td>
                <td><span className={`sm-q-badge ${r.status === 'MAPPED' ? 'green' : r.status === 'UNVERIFIED' ? 'amber' : r.status === 'EXCLUDED' ? 'red' : 'gray'}`}>{r.status}</span></td>
                <td>{r.volume_min != null ? `${r.volume_min} / ${r.volume_step}` : '—'}</td>
                <td>{sizing[r.symbol]?.contract_size != null ? `${sizing[r.symbol].contract_size}${sizing[r.symbol].contract_size_source === 'fallback-100' ? ' (fallback)' : ''}` : '—'}</td>
                <td>{sizing[r.symbol]?.volume ?? '—'}</td>
                <td>{sizing[r.symbol]?.shares_sent ?? '—'}</td>
                <td data-testid={`mt5-size-${r.symbol}`}>{sizing[r.symbol] ? (sizing[r.symbol].valid ? <span className="sm-q-badge green">yes</span> : <span className="sm-q-badge red" title={sizing[r.symbol].reason || ''}>no</span>) : '—'}</td>
                <td>{r.trade_mode ?? '—'}</td>
                <td className="sm-q-muted">{r.note ?? (r.source ? `${r.source}${r.verified_at ? ` · ${r.verified_at}` : ''}` : '')}</td>
                <td>
                  <input value={edit[r.symbol] ?? ''} placeholder={r.broker_symbol || r.symbol} onChange={(e) => setEdit({ ...edit, [r.symbol]: e.target.value })} style={{ width: 110 }} />
                  {' '}<button className="ghost-btn" disabled={busy} onClick={() => act(() => mt5.mapSymbol(r.symbol, edit[r.symbol] || r.broker_symbol || r.symbol), `${r.symbol} saved`)}>Save</button>
                  {r.broker_symbol && <> <button className="ghost-btn" disabled={busy} onClick={() => act(() => mt5.mapSymbol(r.symbol, ''), `${r.symbol} unmapped`)}>Unmap</button></>}
                  {' '}<button className="ghost-btn" disabled={busy} data-testid={`mt5-exclude-${r.symbol}`} title={r.excluded ? 'Allow this symbol to be mapped and traded again' : 'Never map or trade this symbol (the auto-map skips it)'} onClick={() => act(() => mt5.excludeSymbol(r.symbol, !r.excluded), r.excluded ? `${r.symbol} allowed again` : `${r.symbol} excluded`)}>{r.excluded ? 'Allow' : 'Exclude'}</button>
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        </>
      )}
    </div>
  );
}

const EVENT_TONE: Record<string, string> = { FILLED: 'green', PARTIAL_FILL: 'amber', CLOSED: 'green', CLOSED_BY_BROKER: 'blue', REJECTED: 'red', CLOSE_FAILED: 'red', CLOSE_RETRY: 'amber', SKIPPED: 'gray', REPRICE: 'amber', ERROR: 'red', CONNECT_FAILED: 'red', CONNECTED: 'green', DISCONNECTED: 'gray' };

export function Mt5AuditView() {
  const [event, setEvent] = useState('');
  const au = useApi<any>((s) => mt5.audit(400, event, s), [event], { refreshMs: 30_000 });
  const d = au.data;
  const rows: any[] = d?.rows || [];
  return (
    <div className="sm" data-testid="mt5-audit-view">
      <PageHead title="MT5 auto-trading — Audit report" subtitle="Every event, newest first: connections, mappings, orders sent, fills, partial fills, rejections with the broker's reason, closes, retries, broker-side SL/TP hits." />
      <Tabs />
      {au.error && <div className="sm-fresh stale" role="alert"><AlertTriangle size={14} /> {au.error}</div>}
      <div className="sm-filters">
        <div className="sm-tabs" role="group" aria-label="Event filter">
          {['', 'FILLED', 'PARTIAL_FILL', 'REJECTED', 'SKIPPED', 'CLOSED', 'CLOSED_BY_BROKER', 'CLOSE_FAILED'].map((k) => <button key={k || 'all'} className={`sm-tab ${event === k ? 'on' : ''}`} aria-pressed={event === k} onClick={() => setEvent(k)}>{k || 'All'}</button>)}
        </div>
        <button className="ghost-btn" onClick={() => au.refresh()}><RefreshCw size={14} /> Reload</button>
        {d && <span className="sm-q-muted">AUTO_TRADE {d.auto_trade ? 'ON' : 'OFF'}{d.paused ? ' · paused' : ''}</span>}
      </div>
      {!rows.length && <div className="sm-none" data-testid="mt5-audit-none">No MT5 event recorded yet.</div>}
      {rows.length > 0 && (
        <div className="sm-table-wrap"><table className="sm-table sm-q-table" data-testid="mt5-audit-table">
          <thead><tr><th>Time (UTC)</th><th>Event</th><th>Symbol</th><th>Broker symbol</th><th>Action</th><th>Quantity</th><th>Price</th><th>Result</th><th>Ticket</th><th>Paper trade</th><th>Reason / detail</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i} data-testid="mt5-audit-row" data-event={r.event}>
              <td>{r.at}</td>
              <td><span className={`sm-q-badge ${EVENT_TONE[r.event] || 'gray'}`}>{r.event}</span></td>
              <td><b>{r.symbol ?? ''}</b></td><td>{r.broker_symbol ?? ''}</td><td>{r.action ?? ''}</td><td>{r.quantity ?? ''}</td><td>{r.price ?? ''}</td>
              <td>{r.result ?? ''}</td><td>{r.ticket ?? ''}</td><td>{r.trade_id != null ? `#${r.trade_id} ${r.strategy ?? ''}` : ''}</td>
              <td className="sm-q-muted">{r.reason ?? ''}{r.detail ? ` ${typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail)}` : ''}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}
