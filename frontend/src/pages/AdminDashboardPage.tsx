import { useNavigate } from 'react-router-dom';
import { Users, UserPlus, ShieldCheck, Ban, Activity, AlertTriangle } from 'lucide-react';
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

/** Admin home: today's key numbers + recent signups. */
export default function AdminDashboardPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const s = useApi<any>((sig) => api2.adminStats(sig), [], { refreshMs: 60_000 });
  const d = s.data;

  if (s.error && (s.error.includes('403') || s.error.includes('ermitted') || s.error.includes('Admin'))) {
    return <div className="page"><ErrorState error="You do not have access to the dashboard." /></div>;
  }

  const cards = [
    { k: 'Total users', v: d?.total_users, ic: <Users size={16} />, tone: '' },
    { k: 'New · 24h', v: d?.new_24h, ic: <UserPlus size={16} />, tone: 'blue' },
    { k: 'New · 7d', v: d?.new_7d, ic: <UserPlus size={16} />, tone: '' },
    { k: 'Full access', v: d?.full_access, ic: <ShieldCheck size={16} />, tone: 'green' },
    { k: 'Blocked', v: d?.blocked, ic: <Ban size={16} />, tone: d?.blocked ? 'red' : '' },
    { k: 'Logins · 24h', v: d?.logins_24h, ic: <Activity size={16} />, tone: '' },
    { k: 'Failed logins · 24h', v: d?.failed_logins_24h, ic: <AlertTriangle size={16} />, tone: d?.failed_logins_24h ? 'amber' : '' },
    { k: 'Staff / admins', v: d?.admins, ic: <ShieldCheck size={16} />, tone: '' },
  ];

  return (
    <div className="page">
      <PageHead title="Admin Dashboard"
        subtitle="Today's key numbers for the business. Updates every minute." />

      {s.initialLoading ? <Loading /> : (
        <>
          <div className="adm-cards">
            {cards.map((c) => (
              <div key={c.k} className={`adm-card ${c.tone}`}>
                <div className="adm-card-ic">{c.ic}</div>
                <div className="adm-card-v">{c.v ?? '—'}</div>
                <div className="adm-card-k">{c.k}</div>
              </div>
            ))}
          </div>

          <Panel title="Recent signups" noBody>
            {(d?.recent_signups || []).length === 0
              ? <div className="usr-empty">No signups yet.</div>
              : (
                <div className="table-wrap">
                  <table className="tbl">
                    <thead>
                      <tr><th>User</th><th>Name</th><th>Access</th><th>Status</th><th>Joined</th></tr>
                    </thead>
                    <tbody>
                      {(d.recent_signups).map((u: any) => (
                        <tr key={u.username} className="adm-rowlink"
                          onClick={() => navigate(`/admin/users${ctx.search}`)}>
                          <td><b>{u.username}</b></td>
                          <td className="mf-dim">{u.display_name || '—'}</td>
                          <td><span className={`badge ${u.full_access ? 'green' : 'gray'}`}>
                            {u.full_access ? 'Full' : 'View-only'}</span></td>
                          <td><span className={`badge ${u.active ? 'green' : 'gray'}`}>
                            {u.active ? 'Active' : 'Blocked'}</span></td>
                          <td className="num mf-dim">{fmt(u.created_at)}</td>
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
