import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import {
  Bell, BellOff, Check, Plus, RefreshCw, RotateCcw, Save, Trash2, Users,
} from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import ProviderUsage from '../components/ProviderUsage';
import { money, num, signedPct, tone } from '../lib/format';
import {
  ErrorState, Loading, PageHead, StatusChip, Unavailable,
} from './shared';

/* ========================================================================= */
/* Alerts                                                                    */
/* ========================================================================= */

export function AlertsPage({ ctx }: { ctx: PageContext }) {
  const [symbol, setSymbol] = useState(ctx.symbol);
  const [kind, setKind] = useState('PRICE');
  const [comparator, setComparator] = useState('>=');
  const [threshold, setThreshold] = useState('');
  const [busy, setBusy] = useState(false);

  const evaluated = useApi<any>((s) => api2.alertsEvaluate(s), []);
  const rows: any[] = evaluated.data?.rows || [];
  const kinds: Record<string, string> = evaluated.data?.kinds || {
    PRICE: 'Last price', SCORE: 'US-Stock Reader direction score',
    CONFIDENCE: 'Confidence score', EXPECTED_MOVE: 'Expected move %',
    CHANGE_PERCENT: 'Daily change %',
  };

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!symbol.trim() || threshold === '') return;
    setBusy(true);
    try {
      await api2.alertCreate({
        symbol: symbol.trim().toUpperCase(), kind, comparator,
        threshold: Number(threshold),
      });
      setThreshold('');
      evaluated.refresh();
    } finally {
      setBusy(false);
    }
  };

  const triggered = rows.filter((r) => r.triggered);

  return (
    <div className="page">
      <PageHead
        title="Alerts"
        subtitle="In-app alert rules, persisted in PostgreSQL and evaluated on demand against live data. No email or SMS is sent."
        right={
          <button className="ghost-btn" onClick={evaluated.refresh}>
            <RefreshCw size={12} className={evaluated.loading ? 'spin' : undefined} />
            Re-evaluate
          </button>
        }
      />

      <Panel title="New alert" noBody>
        <form className="filter-bar" style={{ borderBottom: 0 }} onSubmit={create}>
          <label>Ticker
            <input type="text" value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
          </label>
          <label>Condition
            <select className="mini" value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(kinds).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>
          <label>
            <select className="mini" value={comparator}
              onChange={(e) => setComparator(e.target.value)}>
              <option value=">=">is at or above</option>
              <option value="<=">is at or below</option>
            </select>
          </label>
          <label>Threshold
            <input type="number" step="0.01" value={threshold}
              onChange={(e) => setThreshold(e.target.value)} />
          </label>
          <button className="ghost-btn on" type="submit" disabled={busy}>
            <Plus size={11} /> Create
          </button>
        </form>
      </Panel>

      <Panel
        title={`Rules (${rows.length})`}
        noBody
        right={triggered.length
          ? <span className="badge green">{triggered.length} triggered</span>
          : <span className="badge gray">none triggered</span>}
      >
        {evaluated.error ? <ErrorState error={evaluated.error} />
          : evaluated.initialLoading ? <Loading label="Evaluating rules…" />
            : !rows.length ? (
              <div className="state">
                <Bell size={17} />
                <span className="state-title">No alerts yet</span>
                <span>Create one above — it is checked whenever you open this page.</span>
              </div>
            ) : (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Ticker</th><th>Condition</th><th className="r">Threshold</th>
                    <th className="r">Current</th><th>State</th><th>Data</th>
                    <th>Last fired</th><th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className={r.triggered ? 'alert-hit' : ''}>
                      <td><b>{r.symbol}</b></td>
                      <td>{r.kind_label} {r.comparator}</td>
                      <td className="num r">{num(r.threshold)}</td>
                      <td className="num r">
                        {r.current_value != null ? num(r.current_value) : '--'}
                      </td>
                      <td>
                        {!r.active ? <span className="badge gray">paused</span>
                          : r.triggered ? <span className="badge green">TRIGGERED</span>
                            : <span className="badge gray">armed</span>}
                      </td>
                      <td><StatusChip status={r.data_status} /></td>
                      <td className="num" style={{ color: 'var(--text-mute)' }}>
                        {r.last_triggered_at
                          ? new Date(r.last_triggered_at).toLocaleString('en-US', {
                            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                          }) : '--'}
                      </td>
                      <td style={{ display: 'flex', gap: 4 }}>
                        <button className="mini-btn" title={r.active ? 'Pause' : 'Arm'}
                          onClick={async () => {
                            await api2.alertToggle(r.id, !r.active);
                            evaluated.refresh();
                          }}>
                          {r.active ? <BellOff size={11} /> : <Bell size={11} />}
                        </button>
                        <button className="mini-btn danger" title="Delete"
                          onClick={async () => {
                            await api2.alertDelete(r.id);
                            evaluated.refresh();
                          }}>
                          <Trash2 size={11} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </Panel>
    </div>
  );
}

/* ========================================================================= */
/* Trade journal                                                             */
/* ========================================================================= */

export function JournalPage({ ctx }: { ctx: PageContext }) {
  const blank = {
    trade_date: new Date().toISOString().slice(0, 10),
    symbol: ctx.symbol, direction: 'LONG',
    entry_price: '', exit_price: '', stop_price: '', target_price: '',
    quantity: '1', notes: '', score_at_entry: '', confidence_at_entry: '',
  };
  const [form, setForm] = useState<Record<string, string>>(blank);
  const [busy, setBusy] = useState(false);

  const journal = useApi<any>((s) => api2.journal(s), []);
  const rows: any[] = journal.data?.rows || [];
  const stats = journal.data?.stats || {};

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const payload: Record<string, unknown> = { ...form };
      ['entry_price', 'exit_price', 'stop_price', 'target_price',
        'quantity', 'score_at_entry', 'confidence_at_entry'].forEach((k) => {
        payload[k] = form[k] === '' ? null : Number(form[k]);
      });
      await api2.journalCreate(payload);
      setForm({ ...blank, symbol: form.symbol });
      journal.refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <PageHead
        title="Trade Journal"
        subtitle="Personal trade record persisted in PostgreSQL, including the score and confidence as they were at entry."
        right={
          <button className="ghost-btn" onClick={journal.refresh}>
            <RefreshCw size={12} className={journal.loading ? 'spin' : undefined} />
            Refresh
          </button>
        }
      />

      <div className="stat-grid">
        <Stat label="Trades" value={stats.total} />
        <Stat label="Closed" value={stats.closed} />
        <Stat label="Win rate" value={stats.win_rate != null ? `${stats.win_rate}%` : '--'}
          tone={stats.win_rate >= 50 ? 'pos' : stats.win_rate != null ? 'neg' : ''} />
        <Stat label="Net P&L" value={stats.net_pnl != null ? money(stats.net_pnl) : '--'}
          tone={tone(stats.net_pnl)} />
        <Stat label="Avg win" value={stats.avg_win != null ? money(stats.avg_win) : '--'} tone="pos" />
        <Stat label="Avg loss" value={stats.avg_loss != null ? money(stats.avg_loss) : '--'} tone="neg" />
        <Stat label="Profit factor" value={stats.profit_factor ?? '--'}
          tone={stats.profit_factor >= 1 ? 'pos' : stats.profit_factor != null ? 'neg' : ''} />
        <Stat label="Open" value={stats.open} />
      </div>

      <Panel title="Log a trade" noBody>
        <form className="filter-bar journal-form" style={{ borderBottom: 0 }} onSubmit={submit}>
          <label>Date<input type="date" value={form.trade_date}
            onChange={(e) => set('trade_date', e.target.value)} style={{ width: 118 }} /></label>
          <label>Ticker<input type="text" value={form.symbol}
            onChange={(e) => set('symbol', e.target.value.toUpperCase())} /></label>
          <label>Direction
            <select className="mini" value={form.direction}
              onChange={(e) => set('direction', e.target.value)}>
              <option>LONG</option><option>SHORT</option>
            </select>
          </label>
          <label>Qty<input type="number" value={form.quantity}
            onChange={(e) => set('quantity', e.target.value)} /></label>
          <label>Entry<input type="number" step="0.01" value={form.entry_price}
            onChange={(e) => set('entry_price', e.target.value)} /></label>
          <label>Exit<input type="number" step="0.01" value={form.exit_price}
            onChange={(e) => set('exit_price', e.target.value)} /></label>
          <label>Stop<input type="number" step="0.01" value={form.stop_price}
            onChange={(e) => set('stop_price', e.target.value)} /></label>
          <label>Target<input type="number" step="0.01" value={form.target_price}
            onChange={(e) => set('target_price', e.target.value)} /></label>
          <label>Score<input type="number" value={form.score_at_entry}
            onChange={(e) => set('score_at_entry', e.target.value)} /></label>
          <label>Conf.<input type="number" value={form.confidence_at_entry}
            onChange={(e) => set('confidence_at_entry', e.target.value)} /></label>
          <label style={{ flex: 1, minWidth: 180 }}>Notes
            <input type="text" value={form.notes} style={{ width: '100%' }}
              onChange={(e) => set('notes', e.target.value)} /></label>
          <button className="ghost-btn on" type="submit" disabled={busy}>
            <Save size={11} /> Save
          </button>
        </form>
      </Panel>

      <Panel title={`Entries (${rows.length})`} noBody>
        {journal.error ? <ErrorState error={journal.error} />
          : journal.initialLoading ? <Loading />
            : !rows.length ? (
              <div className="state">
                <span className="state-title">No entries yet</span>
                <span>Log a trade above to start building the record.</span>
              </div>
            ) : (
              <div className="tbl-scroll" style={{ maxHeight: 420 }}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Date</th><th>Ticker</th><th>Side</th>
                      <th className="r">Entry</th><th className="r">Exit</th>
                      <th className="r">Stop</th><th className="r">Target</th>
                      <th className="r">P&L</th><th>Result</th>
                      <th className="r">Score</th><th className="r">Conf.</th>
                      <th>Notes</th><th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id}>
                        <td className="num">{r.trade_date}</td>
                        <td><b>{r.symbol}</b></td>
                        <td style={{ color: r.direction === 'LONG' ? 'var(--green)' : 'var(--red)' }}>
                          {r.direction}
                        </td>
                        <td className="num r">{num(r.entry_price)}</td>
                        <td className="num r">{num(r.exit_price)}</td>
                        <td className="num r">{num(r.stop_price)}</td>
                        <td className="num r">{num(r.target_price)}</td>
                        <td className={`num r ${tone(r.pnl)}`}>{r.pnl != null ? money(r.pnl) : '--'}</td>
                        <td>
                          <span className={`badge ${r.result === 'WIN' ? 'green'
                            : r.result === 'LOSS' ? 'red' : 'gray'}`}>{r.result}</span>
                        </td>
                        <td className="num r">{num(r.score_at_entry, 0)}</td>
                        <td className="num r">{num(r.confidence_at_entry, 0)}</td>
                        <td style={{ color: 'var(--text-dim)', maxWidth: 220,
                          overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.notes}</td>
                        <td>
                          <button className="mini-btn danger" title="Delete"
                            onClick={async () => { await api2.journalDelete(r.id); journal.refresh(); }}>
                            <Trash2 size={11} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </Panel>
    </div>
  );
}

/* ========================================================================= */
/* Strategy                                                                  */
/* ========================================================================= */

export function StrategyPage({ ctx }: { ctx: PageContext }) {
  const config = useApi<any>((s) => api2.strategy(s), []);
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const d = config.data;
  const values = { ...(d?.values || {}), ...draft };
  const meta = d?.meta || {};
  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const out = await api2.strategyUpdate(draft);
      setDraft({});
      config.refresh();
      const rejected = Object.keys(out.rejected || {});
      setMsg(rejected.length
        ? `Saved. Rejected: ${rejected.join(', ')}`
        : 'Configuration saved.');
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const weightKeys = Object.keys(values).filter((k) => k.startsWith('weight_'));
  const weightTotal = weightKeys.reduce((a, k) => a + Number(values[k] || 0), 0);

  return (
    <div className="page">
      <PageHead
        title="Strategy"
        subtitle="Personal US-Stock Reader configuration. Editing these values changes your gates and risk settings — it cannot place an order."
        right={
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="ghost-btn" disabled={!dirty || saving} onClick={save}>
              <Save size={11} /> {saving ? 'Saving…' : 'Save changes'}
            </button>
            <button className="ghost-btn" onClick={async () => {
              await api2.strategyReset(); setDraft({}); config.refresh();
              setMsg('Reset to defaults.');
            }}>
              <RotateCcw size={11} /> Reset
            </button>
          </div>
        }
      />

      {msg && <div className="insight-banner"><Check size={14} /><div>{msg}</div></div>}

      {config.error ? <ErrorState error={config.error} />
        : config.initialLoading || !d ? <Loading />
          : (
            <div className="two-col">
              <Panel title="Score & confidence gates" noBody>
                <div className="cfg-list">
                  {['min_score_long', 'min_score_short', 'min_confidence'].map((k) => (
                    <Field key={k} k={k} meta={meta[k]} value={values[k]}
                      onChange={(v) => setDraft((p) => ({ ...p, [k]: v }))} />
                  ))}
                </div>
                <div className="hint">
                  A high score with confidence below the gate stays a WATCH.
                  This gating lives in the scoring engine and is not bypassable
                  from here.
                </div>
              </Panel>

              <Panel title="Risk" noBody>
                <div className="cfg-list">
                  {['max_risk_per_trade_pct', 'max_open_positions',
                    'allow_long', 'allow_short'].map((k) => (
                    <Field key={k} k={k} meta={meta[k]} value={values[k]}
                      onChange={(v) => setDraft((p) => ({ ...p, [k]: v }))} />
                  ))}
                </div>
              </Panel>

              <Panel
                title="Component weights"
                noBody
                right={
                  <span className={`badge ${weightTotal === 100 ? 'green' : 'amber'}`}>
                    total {weightTotal}
                  </span>
                }
              >
                <div className="cfg-list">
                  {weightKeys.map((k) => (
                    <Field key={k} k={k} meta={meta[k]} value={values[k]}
                      onChange={(v) => setDraft((p) => ({ ...p, [k]: v }))} />
                  ))}
                </div>
                <div className="hint">{d.note}</div>
              </Panel>
            </div>
          )}
    </div>
  );
}

function Field({
  k, meta, value, onChange,
}: { k: string; meta: any; value: any; onChange: (v: any) => void }) {
  const label = meta?.label || k;
  if (meta?.type === 'bool') {
    return (
      <div className="cfg-row">
        <span className="cfg-label">{label}</span>
        <button
          className={`toggle ${value ? 'on' : ''}`}
          onClick={() => onChange(!value)}
          aria-pressed={!!value}
        >
          <span />
        </button>
      </div>
    );
  }
  return (
    <div className="cfg-row">
      <span className="cfg-label">{label}</span>
      <span className="cfg-input">
        <input
          type="number"
          value={value ?? ''}
          min={meta?.min} max={meta?.max}
          step={k === 'max_risk_per_trade_pct' ? 0.1 : 1}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        {meta?.unit && <i>{meta.unit}</i>}
      </span>
    </div>
  );
}

/* ========================================================================= */
/* Settings                                                                  */
/* ========================================================================= */

export function SettingsPage({ ctx }: { ctx: PageContext }) {
  const health = useApi<any>((s) => api2.health(true, s), []);
  const provs = useApi<any>((s) => api2.providers(s), []);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const d = health.data;
  const providers: Record<string, any> = d?.providers || {};

  const runSync = async (which: 'benzinga' | 'alpha') => {
    setSyncing(which);
    setSyncMsg(null);
    try {
      const out = which === 'benzinga'
        ? await api2.benzingaSync({ force: true })
        : await api2.alphaVantageSync({ symbol: ctx.symbol, force: true });
      setSyncMsg(`${out.provider || which}: ${out.status} — ${out.detail || ''}`);
      health.refresh();
      provs.refresh();
    } catch (err) {
      setSyncMsg((err as Error).message);
    } finally {
      setSyncing(null);
    }
  };

  return (
    <div className="page">
      <PageHead
        title="Settings"
        subtitle="Provider status and connection configuration. Secrets are never displayed."
        right={
          <button className="ghost-btn" onClick={health.refresh}>
            <RefreshCw size={12} className={health.loading ? 'spin' : undefined} />
            Re-probe
          </button>
        }
      />

      {/* What a provider is for comes before whether its key works: a status
          nobody can interpret is not information. */}
      <ProviderUsage />

      {health.error ? <ErrorState error={health.error} />
        : health.initialLoading ? <Loading label="Probing providers…" />
          : (
            <div className="two-col">
              <Panel title="Provider health" noBody>
                <table className="tbl">
                  <thead>
                    <tr><th>Provider</th><th>Status</th><th>Detail</th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(providers).map(([key, p]) => (
                      <tr key={key}>
                        <td><b>{key.replace(/_/g, ' ')}</b></td>
                        <td><StatusChip status={p.status} /></td>
                        <td style={{ color: 'var(--text-dim)', maxWidth: 420,
                          whiteSpace: 'normal', lineHeight: 1.45 }}>
                          {p.detail}
                          {p.note && <div className="hint" style={{ padding: '3px 0 0' }}>{p.note}</div>}
                          {p.required_provider && (
                            <div className="need-provider" style={{ marginTop: 4 }}>
                              Needed: {p.required_provider}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>

              <div className="stack">
                <Panel
                  title="External providers"
                  noBody
                  right={<StatusChip status={provs.data ? 'OK' : undefined} />}
                >
                  {[
                    { key: 'benzinga', which: 'benzinga' as const,
                      p: provs.data?.benzinga },
                    { key: 'alpha_vantage', which: 'alpha' as const,
                      p: provs.data?.alpha_vantage },
                  ].map(({ key, which, p }) => (
                    <div className="prov-block" key={key}>
                      <div className="prov-head">
                        <b>{p?.provider || key}</b>
                        <StatusChip status={p?.status} />
                      </div>
                      <div className="prov-detail">{p?.detail}</div>
                      {p?.note && <div className="prov-note">{p.note}</div>}
                      <div className="prov-meta">
                        <code>{p?.env_var}</code>
                        {p?.cached_rows != null && (
                          <span>{p.cached_rows} cached rows</span>
                        )}
                        {p?.last_fetch && (
                          <span>last sync {new Date(p.last_fetch)
                            .toLocaleString('en-US', {
                              month: 'short', day: 'numeric',
                              hour: '2-digit', minute: '2-digit',
                            })}</span>
                        )}
                      </div>
                      {p?.configured ? (
                        <button className="ghost-btn" disabled={syncing === which}
                          onClick={() => runSync(which)}>
                          <RefreshCw size={11}
                            className={syncing === which ? 'spin' : undefined} />
                          {syncing === which ? 'Syncing…' : 'Sync now'}
                        </button>
                      ) : (
                        <a className="ghost-btn" href={p?.signup}
                          target="_blank" rel="noreferrer">
                          Get an API key
                        </a>
                      )}
                    </div>
                  ))}
                  {syncMsg && <div className="hint">{syncMsg}</div>}
                  <div className="hint">
                    Keys are read from <code>backend/.env</code> and are never
                    returned by any endpoint. Provider data is cached in
                    PostgreSQL — opening a page never calls a provider.
                  </div>
                </Panel>

                <Panel title="IBKR connection" noBody>
                  <div className="kv">
                    <div className="kv-row">
                      <span className="k">Host</span>
                      <span className="v">{providers.ibkr?.host || '--'}</span>
                    </div>
                    <div className="kv-row">
                      <span className="k">Port</span>
                      <span className="v">{providers.ibkr?.port || '--'}</span>
                    </div>
                    <div className="kv-row">
                      <span className="k">Client ID</span>
                      <span className="v">{providers.ibkr?.client_id ?? '--'}</span>
                    </div>
                    <div className="kv-row">
                      <span className="k">Market data type</span>
                      <span className="v">
                        {providers.ibkr?.market_data_type === 2 ? '2 (frozen)'
                          : providers.ibkr?.market_data_type ?? '--'}
                      </span>
                    </div>
                    <div className="kv-row">
                      <span className="k">Market session</span>
                      <span className="v">{d?.market?.label} · {d?.market?.time_et}</span>
                    </div>
                  </div>
                  <div className="hint">
                    Read-only connection. This application never places orders.
                  </div>
                </Panel>

                <Panel title="Demo mode" noBody>
                  <div className="kv">
                    <div className="kv-row">
                      <span className="k">Current mode</span>
                      <span className={`v ${ctx.demo ? 'neg' : 'pos'}`}>
                        {ctx.demo ? 'DEMO FIXTURES' : 'LIVE DATA'}
                      </span>
                    </div>
                  </div>
                  <div className="hint">
                    Demo fixtures load only when the URL carries
                    <code> ?demo=1</code>, and never as a fallback when a
                    provider is down. Set <code>VITE_DEMO_MODE=false</code> to
                    disable the switch entirely in a build.
                  </div>
                </Panel>
              </div>
            </div>
          )}
    </div>
  );
}

/* ========================================================================= */
/* Community                                                                 */
/* ========================================================================= */

export function CommunityPage({ ctx }: { ctx: PageContext }) {
  return (
    <div className="page">
      <PageHead title="Community" subtitle="Planned for a later release." />
      <Panel title="Community — coming later" noBody>
        <div className="state" style={{ minHeight: 260 }}>
          <Users size={30} />
          <span className="state-title">Not part of the personal edition</span>
          <span style={{ maxWidth: 480 }}>
            US-Stock Reader is currently a single-operator tool. Shared watchlists,
            published setups and discussion would need accounts, moderation and
            hosting, none of which exist yet — so rather than ship a hollow
            page, this section is explicitly deferred.
          </span>
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <NavLink to={`/dashboard${ctx.search}`} className="ghost-btn">
              Back to dashboard
            </NavLink>
            <NavLink to={`/trade-journal${ctx.search}`} className="ghost-btn">
              Trade journal
            </NavLink>
          </div>
        </div>
      </Panel>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: any; tone?: string }) {
  return (
    <div className="panel stat-card">
      <div className="st-label">{label}</div>
      <div className={`st-value ${tone || ''}`}>{value ?? '--'}</div>
    </div>
  );
}
