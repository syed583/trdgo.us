import React, { useState } from 'react';
import { Ban, Check, Copy, KeyRound, RotateCcw, Search, ShieldCheck, Trash2 } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState } from './shared';
import { Panel } from '../components/common';
import './admin.css';

const ROLE_OPTIONS = ['user', 'support', 'marketing', 'finance', 'operations', 'super_admin'];
const STAFF = new Set(['super_admin', 'operations', 'finance', 'marketing', 'support']);

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
  const [search, setSearch] = useState('');
  const [showDeleted, setShowDeleted] = useState(false);
  const users = useApi<any>((s) => api2.adminUsers(s, search, showDeleted), [search, showDeleted]);
  const logins = useApi<any>((s) => api2.adminLogins(60, s), []);
  const [err, setErr] = useState<string | null>(null);
  const [issued, setIssued] = useState<{ username: string; password: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = () => { users.refresh(); logins.refresh(); };

  const reset = async (u: string) => {
    const pw = window.prompt(
      `New password for ${u} (leave blank to auto-generate one):`, '');
    if (pw === null) return; // cancelled
    const r = await api2.adminResetUser(u, pw.trim() || undefined).catch(() => null);
    if (r?.status === 'OK') { setIssued({ username: r.username, password: r.password }); setCopied(false); }
    else if (r?.detail) { setErr(r.detail); }
  };
  const toggle = async (u: string, active: boolean) => {
    let reason: string | undefined;
    if (!active) {
      const r = window.prompt(`Block ${u}? Optional reason (shown in their record):`, '');
      if (r === null) return; // cancelled
      reason = r.trim() || undefined;
    }
    await api2.adminSetActive(u, active, reason).catch(() => undefined); refresh();
  };
  const toggleAccess = async (u: string, full: boolean) => {
    await api2.adminSetAccess(u, full).catch(() => undefined); refresh();
  };
  const changeRole = async (u: string, role: string) => {
    if (STAFF.has(role) && !window.confirm(
      `Make ${u} a ${role.replace('_', ' ')}? They will gain admin access.`)) return;
    const r = await api2.adminSetRole(u, role).catch(() => null);
    if (r?.status && r.status !== 'OK') window.alert(r.detail || `Could not set role: ${r.status}`);
    refresh();
  };
  const restore = async (u: string) => {
    await api2.adminRestoreUser(u).catch(() => undefined); refresh();
  };
  const remove = async (u: string) => {
    if (!window.confirm(`Delete ${u}? They lose access immediately. This is a soft `
      + `delete — you can restore them from "Show deleted".`)) return;
    try {
      const r = await api2.adminDeleteUser(u);
      if (r?.status && r.status !== 'OK') window.alert(`Could not delete ${u}: ${r.status}`);
    } catch (e: any) {
      window.alert(`Could not delete ${u}: ${e?.message || 'request failed'}`);
    }
    refresh();
  };

  const rows: any[] = users.data?.users || [];
  const events: any[] = logins.data?.events || [];

  return (
    <div className="page">
      <PageHead title="Users & Access"
        subtitle="People sign up themselves with their phone number and an OTP. Here you grant or revoke access, change roles, block or remove accounts, and see who has signed in. You (admin) sign in with the app password and are not listed here." />

      {users.error && (users.error.includes('403') || users.error.includes('Admin') || users.error.includes('ermitted'))
        ? <ErrorState error="Admin only. Sign in as the owner to manage users." />
        : (
          <>
            {err && <div className="usr-err">{err}</div>}
            {issued && (
              <div className="usr-issued">
                <ShieldCheck size={14} />
                <div>
                  New password for <b>{issued.username}</b> — copy it now, it is not shown again:
                  <div className="usr-pw">
                    <code>{issued.password}</code>
                    <button className="usr-copy" onClick={() => {
                      navigator.clipboard?.writeText(issued.password); setCopied(true);
                    }}><Copy size={12} /> {copied ? 'Copied' : 'Copy'}</button>
                  </div>
                </div>
              </div>
            )}

            <div className="adm-filters">
              <div className="usr-search">
                <Search size={13} />
                <input className="usr-input" placeholder="Search username or name…"
                  value={search} onChange={(e) => setSearch(e.target.value)} />
              </div>
              <label className="usr-checkbox">
                <input type="checkbox" checked={showDeleted}
                  onChange={(e) => setShowDeleted(e.target.checked)} /> Show deleted
              </label>
            </div>

            <Panel title="Accounts" noBody>
              {users.initialLoading ? <Loading />
                : rows.length === 0 ? <div className="usr-empty">
                    {search ? `No accounts match “${search}”.` : 'No users yet. Create one above.'}
                  </div>
                  : (
                    <div className="table-wrap">
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>User</th><th>Role</th><th>Status</th><th>Access</th><th>Last login</th>
                            <th className="r">Logins</th><th>Created</th><th></th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((u) => (
                            <tr key={u.username} className={u.deleted ? 'usr-row-deleted' : ''}>
                              <td>
                                <b>{u.username}</b>
                                {u.display_name && <span className="mf-dim"> · {u.display_name}</span>}
                                {!u.active && u.blocked_reason && (
                                  <div className="usr-reason" title="Block reason">
                                    blocked: {u.blocked_reason}
                                  </div>
                                )}
                              </td>
                              <td>
                                <select className="usr-role"
                                  value={u.role}
                                  onChange={(e) => changeRole(u.username, e.target.value)}>
                                  {ROLE_OPTIONS.map((r) => (
                                    <option key={r} value={r}>{r.replace('_', ' ')}</option>
                                  ))}
                                </select>
                              </td>
                              <td>
                                {u.deleted
                                  ? <span className="badge red">Deleted</span>
                                  : <span className={`badge ${u.active ? 'green' : 'gray'}`}>
                                      {u.active ? 'Active' : 'Blocked'}
                                    </span>}
                              </td>
                              <td>
                                <span className={`badge ${u.full_access ? 'green' : 'gray'}`}>
                                  {u.full_access ? 'Full' : 'View-only'}
                                </span>
                              </td>
                              <td className="num" title={u.last_login_ip ? `from ${u.last_login_ip}` : ''}>
                                {fmt(u.last_login_at)}</td>
                              <td className="num r">{u.login_count}</td>
                              <td className="num mf-dim">{fmt(u.created_at)}</td>
                              <td className="usr-actions">
                                {u.deleted ? (
                                  <button title="Restore account" className="primary"
                                    onClick={() => restore(u.username)}>
                                    <RotateCcw size={13} /> Restore
                                  </button>
                                ) : (
                                  <>
                                    <button
                                      title={u.full_access
                                        ? 'Full access — click to make view-only'
                                        : 'View-only — click to give full access'}
                                      className={`ico ${u.full_access ? 'on' : ''}`}
                                      onClick={() => toggleAccess(u.username, !u.full_access)}>
                                      <ShieldCheck size={15} />
                                    </button>
                                    <button className="ico" title="Reset password"
                                      onClick={() => reset(u.username)}>
                                      <KeyRound size={15} />
                                    </button>
                                    <button className="ico"
                                      title={u.active ? 'Block account' : 'Unblock account'}
                                      onClick={() => toggle(u.username, !u.active)}>
                                      {u.active ? <Ban size={15} /> : <Check size={15} />}
                                    </button>
                                    <button className="ico danger" title="Delete account"
                                      onClick={() => remove(u.username)}>
                                      <Trash2 size={15} />
                                    </button>
                                  </>
                                )}
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
