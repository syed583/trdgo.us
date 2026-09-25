import React, { useState } from 'react';
import { Copy, KeyRound, Plus, ShieldCheck, Trash2, UserPlus } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState } from './shared';
import { Panel } from '../components/common';

function fmt(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * Admin-only: create the accounts you hand out, see who has logged in and
 * from where, and disable or remove access. The admin (you) is not listed
 * here -- it is the ACCESS_PASSWORD account.
 */
export default function UsersPage({ ctx }: { ctx: PageContext }) {
  const users = useApi<any>((s) => api2.adminUsers(s), []);
  const logins = useApi<any>((s) => api2.adminLogins(60, s), []);
  const [newName, setNewName] = useState('');
  const [newPass, setNewPass] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [issued, setIssued] = useState<{ username: string; password: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = () => { users.refresh(); logins.refresh(); };

  const create = async () => {
    setErr(null); setBusy(true); setIssued(null); setCopied(false);
    try {
      const r = await api2.adminCreateUser(newName.trim().toLowerCase(), newPass.trim() || undefined);
      if (r.status !== 'OK') { setErr(r.detail || 'Could not create the user.'); }
      else { setIssued({ username: r.username, password: r.password }); setNewName(''); setNewPass(''); refresh(); }
    } catch (e: any) { setErr(e?.message || 'Request failed.'); }
    finally { setBusy(false); }
  };

  const reset = async (u: string) => {
    const pw = window.prompt(
      `New password for ${u} (leave blank to auto-generate one):`, '');
    if (pw === null) return; // cancelled
    const r = await api2.adminResetUser(u, pw.trim() || undefined).catch(() => null);
    if (r?.status === 'OK') { setIssued({ username: r.username, password: r.password }); setCopied(false); }
    else if (r?.detail) { setErr(r.detail); }
  };
  const toggle = async (u: string, active: boolean) => {
    await api2.adminSetActive(u, active).catch(() => undefined); refresh();
  };
  const remove = async (u: string) => {
    if (!window.confirm(`Remove ${u}? They will lose access immediately.`)) return;
    await api2.adminDeleteUser(u).catch(() => undefined); refresh();
  };

  const rows: any[] = users.data?.users || [];
  const events: any[] = logins.data?.events || [];

  return (
    <div className="page">
      <PageHead title="Users & Access"
        subtitle="Create the logins you hand out, and see who has signed in and from where. You (admin) sign in with the app password and are not listed here." />

      {users.error && (users.error.includes('403') || users.error.includes('Admin'))
        ? <ErrorState error="Admin only. Sign in as the owner to manage users." />
        : (
          <>
            <Panel title="Create a user" icon={<UserPlus size={13} />}>
              <div className="usr-create">
                <input className="usr-input" placeholder="username (e.g. jordan)"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && create()} />
                <input className="usr-input" placeholder="password (optional — auto if blank)"
                  value={newPass}
                  onChange={(e) => setNewPass(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && create()} />
                <button className="usr-btn primary" onClick={create} disabled={busy || !newName.trim()}>
                  <Plus size={13} /> Create
                </button>
              </div>
              <div className="usr-hint">
                Set a password to hand out, or leave it blank to generate a strong one.
              </div>
              {err && <div className="usr-err">{err}</div>}
              {issued && (
                <div className="usr-issued">
                  <ShieldCheck size={14} />
                  <div>
                    <b>{issued.username}</b> created. One-time password — copy it now, it is not shown again:
                    <div className="usr-pw">
                      <code>{issued.password}</code>
                      <button className="usr-copy" onClick={() => {
                        navigator.clipboard?.writeText(issued.password); setCopied(true);
                      }}><Copy size={12} /> {copied ? 'Copied' : 'Copy'}</button>
                    </div>
                  </div>
                </div>
              )}
            </Panel>

            <Panel title="Accounts" noBody>
              {users.initialLoading ? <Loading />
                : rows.length === 0 ? <div className="usr-empty">No users yet. Create one above.</div>
                  : (
                    <div className="table-wrap">
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>User</th><th>Status</th><th>Last login</th>
                            <th>From IP</th><th className="r">Logins</th><th>Created</th><th></th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((u) => (
                            <tr key={u.username}>
                              <td><b>{u.username}</b></td>
                              <td>
                                <span className={`badge ${u.active ? 'green' : 'gray'}`}>
                                  {u.active ? 'Active' : 'Disabled'}
                                </span>
                              </td>
                              <td className="num">{fmt(u.last_login_at)}</td>
                              <td className="num mf-dim">{u.last_login_ip || '—'}</td>
                              <td className="num r">{u.login_count}</td>
                              <td className="num mf-dim">{fmt(u.created_at)}</td>
                              <td className="usr-actions">
                                <button title="New password" onClick={() => reset(u.username)}>
                                  <KeyRound size={13} />
                                </button>
                                <button title={u.active ? 'Disable' : 'Enable'}
                                  onClick={() => toggle(u.username, !u.active)}>
                                  {u.active ? 'Disable' : 'Enable'}
                                </button>
                                <button title="Delete" className="danger"
                                  onClick={() => remove(u.username)}>
                                  <Trash2 size={13} />
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
            </Panel>

            <Panel title="Recent logins" noBody>
              {logins.initialLoading ? <Loading />
                : events.length === 0 ? <div className="usr-empty">No login activity yet.</div>
                  : (
                    <div className="table-wrap">
                      <table className="tbl">
                        <thead>
                          <tr><th>When</th><th>User</th><th>Result</th><th>IP</th><th>Device</th></tr>
                        </thead>
                        <tbody>
                          {events.map((e, i) => (
                            <tr key={i}>
                              <td className="num">{fmt(e.at)}</td>
                              <td><b>{e.username}</b></td>
                              <td>
                                <span className={`badge ${e.ok ? 'green' : 'red'}`}>
                                  {e.ok ? 'OK' : 'Failed'}
                                </span>
                              </td>
                              <td className="num mf-dim">{e.ip || '—'}</td>
                              <td className="mf-dim" title={e.user_agent || ''}>
                                {(e.user_agent || '—').slice(0, 42)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
            </Panel>
          </>
        )}
    </div>
  );
}
