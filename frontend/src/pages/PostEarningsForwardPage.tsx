import { Loader2, Radar } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './forward-scoreboard.css';

/**
 * POST-EARNINGS AI — FORWARD VALIDATION (dev-only).
 * Forward-recorded signals, simulated execution, NO live trading. Starts near-
 * empty and accrues as earnings report; pending predictions are not trades.
 */
export default function PostEarningsForwardPage() {
  const q = useApi<any>((s) => api2.postEarningsDashboard(s), [], { refreshMs: 30_000 });
  const d = q.data;
  if (q.initialLoading) return (
    <div className="page"><div className="es-empty"><Loader2 size={18} className="spin" /> Loading forward validation…</div></div>
  );
  if (!d || d.status !== 'OK') return (
    <div className="page"><div className="ma-note">{d?.detail || 'Forward recorder not initialised yet.'}</div></div>
  );
  const stat = (label: string, val: any) => (
    <div className="fsb-stat"><span>{label}</span><b>{val ?? '—'}</b></div>
  );
  return (
    <div className="page fsb-wrap">
      <div className="ts-head">
        <h1><Radar size={20} /> Post-Earnings AI — Forward Validation</h1>
        <p><b>FORWARD-RECORDED SIGNALS · SIMULATED EXECUTION · NO LIVE TRADING</b> — model {d.model_version}.</p>
      </div>
      <div className="fsb-note warn" style={{ marginBottom: 12 }}>{d.note}</div>
      <div className="fsb-stats">
        {stat('Events observed', d.events_observed)}
        {stat('Verified timestamps', d.verified_timestamps)}
        {stat('Events w/ results', d.events_with_results)}
        {stat('BUY', d.buy)}
        {stat('SELL', d.sell)}
        {stat('WAIT', d.wait)}
      </div>
      <h3 className="fsb-h">Simulated outcomes (graded) <small className="fsb-sub">DAILY proxy · no live trading · tiny sample</small></h3>
      <div className="fsb-stats">
        {stat('Pending outcomes', d.pending_outcomes)}
        {stat('Completed sim trades', d.completed_sim_trades)}
        {stat('Win rate', d.win_rate == null ? '—' : `${d.win_rate}%`)}
        {stat('Avg net', d.avg_net_pct == null ? '—' : `${d.avg_net_pct}%`)}
        {stat('Verified ts', `${d.verified_pct ?? 0}%`)}
      </div>
      <div className="fsb-stats">
        {d.buy_performance && stat('BUY graded', `${d.buy_performance.n} · ${d.buy_performance.win_rate}% · ${d.buy_performance.avg_net}%`)}
        {d.sell_performance && stat('SELL graded', `${d.sell_performance.n} · ${d.sell_performance.win_rate}% · ${d.sell_performance.avg_net}%`)}
        {stat('Scheduler', d.scheduler?.status || '—')}
        {stat('Last success', d.scheduler?.last_success ? String(d.scheduler.last_success).slice(0, 16) : '—')}
      </div>
      <div className="fsb-notes">
        <div className="fsb-note warn"><b>Missing data (recorded, never fabricated):</b> {(d.missing || []).join(', ')}.</div>
        <div className="fsb-note">Verified-timestamp = provider gave a real release time; otherwise TIMESTAMP_UNVERIFIED. A positive surprise never auto-buys. Immutable snapshots; outcomes graded separately.</div>
      </div>
    </div>
  );
}
