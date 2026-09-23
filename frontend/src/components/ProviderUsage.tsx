import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Panel } from './common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';

/**
 * What each provider is actually for.
 *
 * The provider table said whether a key worked. It never said what the key
 * was *for*, so "Twelve Data: rate limited" meant nothing to a reader until
 * the trend parameters silently went blank -- which is what happened, and
 * took a profiling session to trace back.
 *
 * So each row answers three questions a person actually has: what does this
 * power, how old is its data, and what do I lose if it stops.
 */
interface Provider {
  key: string;
  name: string;
  env_var: string | null;
  freshness: string;
  cost: string;
  powers: string[];
  fallback: string;
  status?: string;
  status_detail?: string | null;
}

const TONE: Record<string, string> = {
  OK: 'pu-ok',
  NOT_CONFIGURED: 'pu-off',
  OFFLINE: 'pu-warn',
  RATE_LIMITED: 'pu-warn',
  ENTITLEMENT_REQUIRED: 'pu-warn',
  PROVIDER_OFFLINE: 'pu-bad',
  UNKNOWN: 'pu-off',
};

export default function ProviderUsage() {
  const [open, setOpen] = useState<string | null>(null);
  const state = useApi<any>((s) => api2.providerUsage(s), []);
  const rows: Provider[] = state.data?.providers || [];

  if (state.initialLoading) {
    return <Panel title="What each provider powers">
      <p className="ce-note">Reading the provider map…</p>
    </Panel>;
  }

  return (
    <Panel title="What each provider powers" noBody
      right={<span className="ce-count">{rows.length} providers</span>}>
      <div className="pu">
        {rows.map((p) => {
          const expanded = open === p.key;
          return (
            <div className={`pu-row ${expanded ? 'on' : ''}`} key={p.key}>
              <button className="pu-head"
                onClick={() => setOpen(expanded ? null : p.key)}>
                {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <b>{p.name}</b>
                <span className={`pu-status ${TONE[p.status || 'UNKNOWN'] || 'pu-off'}`}>
                  {(p.status || 'unknown').toLowerCase().replace(/_/g, ' ')}
                </span>
                <em className="pu-fresh">{p.freshness}</em>
              </button>

              {expanded && (
                <div className="pu-body">
                  <h6>Powers</h6>
                  <ul>{p.powers.map((x) => <li key={x}>{x}</li>)}</ul>

                  <h6>Without it</h6>
                  <p>{p.fallback}</p>

                  <div className="pu-meta">
                    <span><b>Cost</b> {p.cost}</span>
                    {p.env_var && <span><b>Key</b> {p.env_var} in backend/.env</span>}
                    {p.status_detail && <span><b>Now</b> {p.status_detail}</span>}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        <p className="ce-foot">{state.data?.detail}</p>
      </div>
    </Panel>
  );
}
