import { useState } from 'react';
import { AlertTriangle, Check, FileText, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { earningsMonitor } from '../api/client';
import { useApi } from '../hooks/useApi';

/** One gate-refusal alert as the server reports it (/api/earnings-monitor/smart-move/alerts). Facts only; nothing is computed here. */
export type SmartMoveAlert = {
  id: string; task: string; variant: string; at: string; at_et?: string | null; at_vps?: string | null; minutes_ago?: number; exit_code?: number | null;
  exit_code_confirmed?: boolean; blocked_record_confirmed?: boolean; schedule_log_checked?: boolean; reasons: string[]; reason_text?: string | null;
  consecutive: number; last_success_before_et?: string | null; next_attempt_et?: string | null; gates_output?: string | null;
  acknowledged: boolean; acknowledged_by?: string | null; acknowledged_at?: string | null; severity: 'critical' | 'acknowledged' | 'ok';
};

/** Operator / admin only: the alerts feed, polled once a minute; the hook is a no-op (no request) for anyone else. */
export function useSmartMoveAlerts(enabled: boolean) {
  return useApi<any>((s) => earningsMonitor.smartAlerts(s), [enabled], { refreshMs: 60_000, enabled });
}

function vpsTime(iso?: string | null) {
  // The VPS runs on UTC+1 (owner's server clock); shown beside ET so the operator can match Task Scheduler's own times.
  if (!iso) return null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  const v = new Date(t.getTime() + 60 * 60 * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${v.getUTCFullYear()}-${p(v.getUTCMonth() + 1)}-${p(v.getUTCDate())} ${p(v.getUTCHours())}:${p(v.getUTCMinutes())} VPS`;
}

/** The one-line strip under the header (every page, operator / admin). Red while unacknowledged, grey once acknowledged; gone when a LIVE pass succeeds. */
export function SmartMoveAlertStrip({ alerts }: { alerts: SmartMoveAlert[] }) {
  if (!alerts || alerts.length === 0) return null;
  const a = alerts[0];
  return (
    <div className={`sm-alert-strip ${a.acknowledged ? 'ack' : ''}`} role={a.acknowledged ? 'status' : 'alert'} data-testid="sm-alert-strip">
      <AlertTriangle size={14} /> Smart Money Move <b>{a.task}</b> refused to run at {a.at_et ?? a.at} ET{a.exit_code != null ? ` (exit ${a.exit_code})` : ''}
      {a.consecutive > 1 ? `, ${a.consecutive} refusals in a row` : ''}: {a.reasons.slice(0, 2).join('; ')}{a.reasons.length > 2 ? '; …' : ''}
      {a.acknowledged ? <> · acknowledged by {a.acknowledged_by}</> : null}
      {' '}<Link to="/smart-move/engine20">Open the LIVE scanner</Link>
    </div>
  );
}

/** The red state card on the LIVE scanner page, above the predictions. Acknowledge turns it grey; only a later LIVE pass of the same variant removes it. */
export function SmartMoveAlertCard({ alert, canAck, onChanged }: { alert: SmartMoveAlert; canAck: boolean; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [showOut, setShowOut] = useState(false);
  const out = useApi<any>((s) => (showOut ? earningsMonitor.smartAlertGatesOutput(s) : Promise.resolve(null)), [showOut]);
  const ack = async () => {
    setBusy(true); setMsg(null);
    try {
      const r: any = await earningsMonitor.smartAlertAck();
      setMsg(r?.ok ? 'Acknowledged. The card stays until a LIVE pass of this variant succeeds.' : `Could not acknowledge: ${r?.reason ?? r?.status ?? 'unknown'}`);
      onChanged?.();
    } catch (e: any) {
      setMsg(`Could not acknowledge: ${e?.message ?? e}`);
    } finally { setBusy(false); }
  };
  const a = alert;
  return (
    <div className={`sm-q-state ${a.acknowledged ? 'ack' : 'bad'}`} role={a.acknowledged ? 'status' : 'alert'} data-testid="sm-alert-card" data-severity={a.severity}>
      <b><AlertTriangle size={14} /> {a.acknowledged ? 'Gate refusal (acknowledged)' : 'Gate refusal: the scheduled LIVE pass did not run'}</b>
      <div className="sm-alert-grid">
        <span>Task</span><span><b>{a.task}</b> · variant {a.variant}</span>
        <span>When</span><span>{a.at_et ?? a.at} ET{vpsTime(a.at) ? ` · ${vpsTime(a.at)}` : ''}{a.minutes_ago != null ? ` · ${a.minutes_ago} min ago` : ''}</span>
        <span>Exit code</span><span>{a.exit_code != null ? a.exit_code : 'not confirmed'}{a.exit_code_confirmed ? ' (confirmed by the schedule log)' : a.schedule_log_checked ? ' (no exit-3 record found in the schedule log)' : ' (schedule log not readable here)'}{a.blocked_record_confirmed ? ' · BLOCKED record confirmed' : ' · no matching BLOCKED record'}</span>
        <span>Gate reasons</span><span>{a.reasons.length ? <ul className="sm-alert-reasons">{a.reasons.map((r) => <li key={r}>{r}</li>)}</ul> : (a.reason_text ?? 'not recorded')}</span>
        <span>Consecutive</span><span>{a.consecutive} refusal{a.consecutive === 1 ? '' : 's'} since the last successful pass{a.last_success_before_et ? ` (${a.last_success_before_et} ET)` : ''}</span>
        <span>Next attempt</span><span>{a.next_attempt_et ? `${a.next_attempt_et} ET` : 'unknown'}</span>
        {a.acknowledged && <><span>Acknowledged</span><span>by {a.acknowledged_by} at {a.acknowledged_at}</span></>}
      </div>
      <div className="sm-alert-actions">
        {canAck && !a.acknowledged && <button className="ghost-btn" disabled={busy} onClick={ack} data-testid="sm-alert-ack"><Check size={14} /> Acknowledge</button>}
        {a.gates_output && <button className="ghost-btn" onClick={() => setShowOut((v) => !v)} data-testid="sm-alert-gates"><FileText size={14} /> {showOut ? 'Hide gates output' : 'Gates output'} ({a.gates_output})</button>}
        {onChanged && <button className="ghost-btn" onClick={onChanged}><RefreshCw size={14} /> Reload</button>}
      </div>
      {msg && <div className="sm-q-muted" role="status">{msg}</div>}
      {showOut && (out.data?.ok ? <pre className="sm-alert-output">{out.data.text}</pre> : <div className="sm-q-muted">{out.data ? `No transcript: ${out.data.detail ?? out.data.reason}` : 'Loading…'}</div>)}
    </div>
  );
}
