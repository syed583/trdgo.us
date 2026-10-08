import { useState } from 'react';
import { ScrollText } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { PageHead, Loading, ErrorState } from './shared';
import { Panel } from '../components/common';
import './admin.css';

function fmt(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const ACTION_LABEL: Record<string, string> = {
  'user.create': 'Created user',
  'user.reset_password': 'Reset password',
  'user.block': 'Blocked user',
  'user.unblock': 'Unblocked user',
  'user.grant_access': 'Granted full access',
  'user.revoke_access': 'Revoked full access',
  'user.set_role': 'Changed role',
  'user.delete': 'Deleted user',
  'user.restore': 'Restored user',
  'settings.provider_key': 'Replaced provider key',
};

/** Admin audit log: who did what, to whom, when. Append-only, newest first. */
export default function AuditLogPage({ ctx }: { ctx: PageContext }) {
  const [action, setAction] = useState('');
  const [actor, setActor] = useState('');
  const q = useApi<any>(
    (s) => api2.adminAudit(s, { limit: 200, action: action || undefined, actor: actor || undefined }),
    [action, actor], { refreshMs: 60_000 });

  if (q.error && (q.error.includes('403') || q.error.includes('ermitted') || q.error.includes('Admin'))) {
    return <div className="page"><ErrorState error="You do not have access to the audit log." /></div>;
  }

  const rows: any[] = q.data?.rows || [];

  return (
    <div className="page">
      <PageHead title="Audit Log"
        subtitle="Every admin action — who did what, to whom, and when. Append-only." />

      <div className="adm-filters">
        <input className="usr-input" placeholder="Filter by admin (actor)…"
          value={actor} onChange={(e) => setActor(e.target.value.trim().toLowerCase())} />
        <select className="usr-input" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">All actions</option>
          <option value="user.create">Created user</option>
          <option value="user.block">Blocked</option>
          <option value="user.unblock">Unblocked</option>
          <option value="user.grant_access">Granted access</option>
          <option value="user.revoke_access">Revoked access</option>
          <option value="user.set_role">Role change</option>
          <option value="user.delete">Deleted</option>
          <option value="user.restore">Restored</option>
          <option value="user.reset_password">Password reset</option>
          <option value="settings">Settings</option>
        </select>
      </div>

      <Panel title="Actions" icon={<ScrollText size={13} />} noBody>
        {q.initialLoading ? <Loading />
          : rows.length === 0 ? <div className="usr-empty">No matching actions.</div>
            : (
              <div className="table-wrap">
                <table className="tbl">
                  <thead>
                    <tr><th>When</th><th>Admin</th><th>Action</th><th>Target</th><th>Detail</th><th>IP</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id}>
                        <td className="num mf-dim">{fmt(r.at)}</td>
                        <td><b>{r.actor}</b>{r.actor_role && r.actor_role !== 'admin'
                          ? <span className="adm-role-tag"> {r.actor_role}</span> : null}</td>
                        <td>{ACTION_LABEL[r.action] || r.action}</td>
                        <td className="mf-dim">{r.target || '—'}</td>
                        <td className="mf-dim">{r.detail || '—'}</td>
                        <td className="num mf-dim">{r.ip || '—'}</td>
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
