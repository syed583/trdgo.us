import React from 'react';
import {
  AlertTriangle, Info, KeyRound, LogIn, PlugZap, RefreshCw, ServerCog, WifiOff,
} from 'lucide-react';
import type { ProviderStatus } from '../api/client';
import { AUTH_ERROR, MAINTENANCE_ERROR } from '../api/client';

/** A full-page prompt shown when a request came back 401 (session ended). */
export function AuthRequired() {
  return (
    <div className="state">
      <LogIn size={18} />
      <span className="state-title">Sign in to continue</span>
      <span style={{ maxWidth: 420 }}>
        Your session has ended. Sign in to view this page.
      </span>
      <button className="btn" style={{ marginTop: 10 }}
        onClick={() => { window.location.href = '/login'; }}>Sign in</button>
    </div>
  );
}

/** Page title block, used by every non-reference screen. */
export function PageHead({
  title, subtitle, right,
}: { title: string; subtitle?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <div className="page-sub">{subtitle}</div>}
      </div>
      {right && <div className="page-head-right">{right}</div>}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  OK: 'green',
  PROVIDER_NOT_CONFIGURED: 'blue',
  PARTIAL_DATA: 'amber',
  RATE_LIMITED: 'amber',
  REQUIRES_ADVANCED_OPTIONS_DATA: 'blue',
  TEST_DATA: 'amber',
  STALE_DATA: 'amber',
  INSUFFICIENT_DATA: 'amber',
  DATA_UNAVAILABLE: 'gray',
  NO_DATA: 'gray',
  NOT_TRACKED: 'gray',
  DATA_CONFLICT: 'amber',
  ENTITLEMENT_REQUIRED: 'blue',
  PROVIDER_OFFLINE: 'red',
  SYMBOL_NOT_FOUND: 'red',
  ERROR: 'red',
};

export function StatusChip({ status, title }: { status?: ProviderStatus; title?: string }) {
  if (!status) return null;
  const tone = STATUS_TONE[status] || 'gray';
  return (
    <span className={`badge ${tone}`} title={title}>
      {String(status).replace(/_/g, ' ')}
    </span>
  );
}

/**
 * The one place that renders "we cannot show this".
 *
 * It distinguishes an entitlement gap from an outage from an empty result,
 * because those need different actions from the operator.
 */
export function Unavailable({
  status, detail, required, compact,
}: {
  status?: ProviderStatus;
  detail?: string | null;
  required?: string | null;
  compact?: boolean;
}) {
  const entitlement = status === 'ENTITLEMENT_REQUIRED';
  const notConfigured = status === 'PROVIDER_NOT_CONFIGURED';
  const advanced = status === 'REQUIRES_ADVANCED_OPTIONS_DATA';
  const offline = status === 'PROVIDER_OFFLINE';

  const title = notConfigured ? 'Provider not configured'
    : advanced ? 'Requires advanced options data'
      : entitlement ? 'Requires a data entitlement'
        : offline ? 'Provider offline'
          : status === 'TEST_DATA' ? 'Seed data only'
            : status === 'PARTIAL_DATA' ? 'Partial data'
              : 'Data unavailable';

  const Icon = (entitlement || notConfigured || advanced)
    ? KeyRound : offline ? PlugZap : AlertTriangle;

  return (
    <div className="state" style={compact ? { minHeight: 60, padding: 14 } : undefined}>
      <Icon size={17} />
      <span className="state-title">{title}</span>
      {detail && <span style={{ maxWidth: 460 }}>{detail}</span>}
      {required && (
        <span className="need-provider">
          <Info size={11} /> Needed: {required}
        </span>
      )}
      {status && <StatusChip status={status} />}
    </div>
  );
}

export function Loading({ label = 'Loading live data…' }: { label?: string }) {
  return (
    <div className="state">
      <span className="spinner" />
      <span>{label}</span>
    </div>
  );
}

/** Shown on a 5xx/maintenance response. */
export function MaintenanceState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="state">
      <ServerCog size={18} />
      <span className="state-title">Service temporarily unavailable</span>
      <span style={{ maxWidth: 420 }}>
        The server is busy or briefly down. This usually clears on its own —
        try again in a moment.
      </span>
      <button className="btn" style={{ marginTop: 10 }}
        onClick={onRetry || (() => window.location.reload())}>
        <RefreshCw size={14} /> Retry
      </button>
    </div>
  );
}

// onRetry lets a page re-run its own fetch; without it, the button reloads the
// page so there is always a recovery path from an error state.
export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  if (error === AUTH_ERROR) return <AuthRequired />;
  if (error === MAINTENANCE_ERROR) return <MaintenanceState onRetry={onRetry} />;
  return (
    <div className="state">
      <WifiOff size={17} />
      <span className="state-title">Backend unreachable</span>
      <span>{error}</span>
      <button className="btn" style={{ marginTop: 10 }}
        onClick={onRetry || (() => window.location.reload())}>
        <RefreshCw size={14} /> Retry
      </button>
    </div>
  );
}

/** Sortable column header. */
export function Th({
  label, field, sort, dir, onSort, align,
}: {
  label: string;
  field?: string;
  sort?: string;
  dir?: 'asc' | 'desc';
  onSort?: (f: string) => void;
  align?: 'r';
}) {
  const active = field && sort === field;
  return (
    <th
      className={`${align === 'r' ? 'r' : ''} ${field ? 'sortable' : ''}`}
      onClick={field && onSort ? () => onSort(field) : undefined}
    >
      {label}
      {active && <span className="sort-caret">{dir === 'asc' ? '▲' : '▼'}</span>}
    </th>
  );
}

export function useSort<T>(rows: T[], initial: string) {
  const [sort, setSort] = React.useState(initial);
  const [dir, setDir] = React.useState<'asc' | 'desc'>('desc');

  const toggle = React.useCallback((field: string) => {
    setSort((prev) => {
      if (prev === field) {
        setDir((d) => (d === 'asc' ? 'desc' : 'asc'));
        return prev;
      }
      setDir('desc');
      return field;
    });
  }, []);

  const sorted = React.useMemo(() => {
    const out = [...rows];
    out.sort((a: any, b: any) => {
      const av = a[sort];
      const bv = b[sort];
      // Nulls always sink, whichever way the column is pointing.
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string' || typeof bv === 'string') {
        return dir === 'asc'
          ? String(av).localeCompare(String(bv))
          : String(bv).localeCompare(String(av));
      }
      return dir === 'asc' ? av - bv : bv - av;
    });
    return out;
  }, [rows, sort, dir]);

  return { sorted, sort, dir, toggle };
}
