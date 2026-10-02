import React from 'react';
import type { PageContext } from '../App';
import { PageHead } from './shared';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';

/**
 * Admin · Workflow — how a request moves through Trdgo.us.
 *
 * Three diagrams: the overall request lifecycle, what happens when a contract
 * is opened, and the live-tape polling loop. Drawn as inline SVG that inherits
 * the app's theme tokens so they read in light and dark mode. Admin-only.
 */

const ARROW = (
  <marker id="wf-arrow" viewBox="0 0 10 10" refX="8" refY="5"
    markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke"
      strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
  </marker>
);

const C = {
  gray: 'var(--panel-2)', grayBd: 'var(--border-2)',
  blueBg: 'var(--blue-bg)', blue: 'var(--blue)',
  greenBg: 'var(--green-bg)', green: 'var(--green)',
  amberBg: 'var(--amber-bg)', amber: 'var(--amber)',
  redBg: 'var(--red-bg)', red: 'var(--red)',
  line: 'var(--text-mute)', text: 'var(--text)', dim: 'var(--text-dim)',
};

function Box({ x, y, w = 320, h = 50, bg, bd, title, sub, tc }: {
  x: number; y: number; w?: number; h?: number;
  bg: string; bd: string; title: string; sub?: string; tc: string;
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={8} fill={bg} stroke={bd} strokeWidth={1} />
      <text x={x + w / 2} y={y + (sub ? 21 : h / 2 + 4)} textAnchor="middle"
        fontSize={13} fontWeight={600} fill={tc}>{title}</text>
      {sub && (
        <text x={x + w / 2} y={y + 38} textAnchor="middle"
          fontSize={11.5} fill={C.dim}>{sub}</text>
      )}
    </g>
  );
}

function VArrow({ x, y1, y2, dash, stroke = C.line }: {
  x: number; y1: number; y2: number; dash?: boolean; stroke?: string;
}) {
  return (
    <line x1={x} y1={y1} x2={x} y2={y2} stroke={stroke} strokeWidth={1.5}
      strokeDasharray={dash ? '5 4' : undefined} markerEnd="url(#wf-arrow)" />
  );
}

/* ---- 1. overall request lifecycle ---- */
function RequestFlow({ m }: { m?: any }) {
  const cx = 340;
  // Live sub-labels: the cache tier shows its current hit rate, and the
  // provider client shows latency -- or a red "PAUSED" when it is riding out
  // the rate limit, which is exactly where the pipeline gets stuck.
  const cacheSub = m?.cache_hit_rate != null
    ? `hit rate ${m.cache_hit_rate}% · most reads stop here`
    : 'swr + Supabase · fresh? serve now';
  let clientSub = 'token · paced · budget · cached';
  let clientBg = C.amberBg; let clientBd = C.amber; let clientTc = C.amber;
  if (m) {
    if (m.blocked) {
      clientSub = `PAUSED ${m.blocked_for_seconds}s · rate limit`;
      clientBg = C.redBg; clientBd = C.red; clientTc = C.red;
    } else if (m.avg_latency_ms != null) {
      clientSub = `~${m.avg_latency_ms}ms avg · ${m.net_calls} calls`;
    }
  }
  const rows: Array<[string, string, string, string, string]> = [
    ['User (browser)', 'searches a ticker, opens a panel', C.gray, C.grayBd, C.text],
    ['React app (UI)', 'pages · useApi · client.ts', C.blueBg, C.blue, C.blue],
    ['FastAPI + auth', '/api/… · session cookie checked', C.blueBg, C.blue, C.blue],
    ['Service layer', 'overview · flow · levels services', C.blueBg, C.blue, C.blue],
    ['Cache tier', cacheSub, C.greenBg, C.green, C.green],
    ['Unusual Whales client', clientSub, clientBg, clientBd, clientTc],
    ['Unusual Whales API', 'api.unusualwhales.com', C.redBg, C.red, C.red],
  ];
  const ys = rows.map((_, i) => 40 + i * 72);
  return (
    <svg width="100%" viewBox="0 0 680 540" role="img" style={{ display: 'block' }}>
      <title>Request lifecycle</title>
      <defs>{ARROW}</defs>
      {ys.slice(0, -1).map((y, i) => (
        <VArrow key={i} x={cx} y1={y + 50} y2={ys[i + 1]} />
      ))}
      <line x1={110} y1={ys[4] + 25} x2={110} y2={ys[1] + 25} stroke={C.green}
        strokeWidth={1.5} strokeDasharray="5 4" markerEnd="url(#wf-arrow)" />
      <text x={100} y={ys[2] + 30} textAnchor="end" fontSize={11.5} fill={C.dim}>cache hit</text>
      {rows.map(([t, s, bg, bd, tc], i) => (
        <Box key={t} x={180} y={ys[i]} bg={bg} bd={bd} title={t} sub={s} tc={tc} />
      ))}
    </svg>
  );
}

/* ---- 2. contract popup ---- */
function ContractFlow() {
  return (
    <svg width="100%" viewBox="0 0 680 600" role="img" style={{ display: 'block' }}>
      <title>Contract popup</title>
      <defs>{ARROW}</defs>
      <Box x={180} y={40} bg={C.gray} bd={C.grayBd} title="Click a contract" sub="flow tape or Overview table" tc={C.text} />
      <VArrow x={340} y1={90} y2={112} />
      <Box x={180} y={112} bg={C.blueBg} bd={C.blue} title="ContractModal opens" sub="setContract(occ) → render" tc={C.blue} />
      <VArrow x={340} y1={162} y2={184} />
      <Box x={180} y={184} bg={C.blueBg} bd={C.blue} title="GET /api/options/contract/{occ}" sub="swr cache · 20s" tc={C.blue} />
      <VArrow x={340} y1={234} y2={256} />
      <Box x={180} y={256} bg={C.blueBg} bd={C.blue} title="get_contract()" sub="parse OCC · 4 reads in parallel" tc={C.blue} />
      {[112, 264, 416, 568].map((cx) => (
        <VArrow key={cx} x={cx} y1={306} y2={340} />
      ))}
      {([['/flow', '→ Time & Sales', 42], ['/intraday', '→ Summary', 194],
        ['/volume-profile', '→ Volume', 346], ['/historic', '→ History', 498]] as const)
        .map(([t, s, x]) => (
          <Box key={t} x={x} y={340} w={140} h={58} bg={C.amberBg} bd={C.amber} title={t} sub={s} tc={C.amber} />
        ))}
      {[112, 264, 416, 568].map((cx) => (
        <line key={cx} x1={cx} y1={398} x2={340} y2={450} stroke={C.line} strokeWidth={1.5} markerEnd="url(#wf-arrow)" />
      ))}
      <Box x={180} y={450} bg={C.greenBg} bd={C.green} title="Build payload + analysis" sub="spot · Black-Scholes · Kelly" tc={C.green} />
      <VArrow x={340} y1={500} y2={522} />
      <Box x={180} y={522} bg={C.blueBg} bd={C.blue} title="Modal renders" sub="five tabs from one payload" tc={C.blue} />
    </svg>
  );
}

/* ---- 3. live tape loop ---- */
function TapeLoop() {
  return (
    <svg width="100%" viewBox="0 0 680 500" role="img" style={{ display: 'block' }}>
      <title>Live tape polling loop</title>
      <defs>{ARROW}</defs>
      <path d="M185 455 H110 V65 H185" fill="none" stroke={C.green} strokeWidth={1.5}
        strokeDasharray="5 4" markerEnd="url(#wf-arrow)" />
      <text x={100} y={258} textAnchor="end" fontSize={11.5} fill={C.dim}>wait 4s</text>
      <Box x={185} y={40} w={310} bg={C.gray} bd={C.grayBd} title="Timer tick" sub="useApi · refresh 4s" tc={C.text} />
      <VArrow x={340} y1={90} y2={118} />
      <Box x={185} y={118} w={310} bg={C.amberBg} bd={C.amber} title="Tab visible?" sub="hidden → skip this tick" tc={C.amber} />
      <VArrow x={340} y1={168} y2={196} />
      <Box x={185} y={196} w={310} bg={C.blueBg} bd={C.blue} title="GET /flow/market/tape" sub="limit = 200" tc={C.blue} />
      <VArrow x={340} y1={246} y2={274} />
      <Box x={185} y={274} w={310} bg={C.greenBg} bd={C.green} title="swr cache — fresh?" sub="≤4s old → serve cached" tc={C.green} />
      <VArrow x={340} y1={324} y2={352} />
      <path d="M495 299 H560 V455 H495" fill="none" stroke={C.green} strokeWidth={1.5}
        strokeDasharray="5 4" markerEnd="url(#wf-arrow)" />
      <text x={568} y={375} textAnchor="start" fontSize={11.5} fill={C.dim}>cache hit</text>
      <Box x={185} y={352} w={310} bg={C.redBg} bd={C.red} title="Unusual Whales flow-alerts" sub="on miss · paced · map rows" tc={C.red} />
      <VArrow x={340} y1={402} y2={430} />
      <Box x={185} y={430} w={310} bg={C.blueBg} bd={C.blue} title="Table re-renders" sub="newest prints at the top" tc={C.blue} />
    </svg>
  );
}

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="wf-stat">
      <span className="wf-stat-label">{label}</span>
      <b className="wf-stat-value" style={tone ? { color: tone } : undefined}>{value}</b>
    </div>
  );
}

function LivePipeline({ m, loading }: { m?: any; loading: boolean }) {
  const dim = 'var(--text-mute)';
  if (loading && !m) {
    return <div className="hint" style={{ padding: 12 }}>Reading live pipeline…</div>;
  }
  if (!m) return <div className="hint" style={{ padding: 12 }}>Metrics unavailable.</div>;
  const provTone = m.blocked ? 'var(--red)'
    : m.provider_state === 'slow' ? 'var(--amber)' : 'var(--green)';
  const gateTone = m.gate_state === 'stuck' ? 'var(--red)'
    : m.gate_state === 'pacing' ? 'var(--amber)' : 'var(--green)';
  return (
    <div className="wf-live">
      <Stat label="Cache hit rate"
        value={m.cache_hit_rate != null ? `${m.cache_hit_rate}%` : '--'}
        tone={m.cache_hit_rate != null && m.cache_hit_rate >= 70 ? 'var(--green)' : dim} />
      <Stat label="Provider latency"
        value={m.avg_latency_ms != null ? `${m.avg_latency_ms}ms` : '--'} tone={provTone} />
      <Stat label="p95 latency" value={m.p95_latency_ms != null ? `${m.p95_latency_ms}ms` : '--'} />
      <Stat label="Pacing wait"
        value={m.avg_wait_ms != null ? `${m.avg_wait_ms}ms` : '--'} tone={gateTone} />
      <Stat label="Provider calls" value={`${m.net_calls}${m.net_errors ? ` · ${m.net_errors} err` : ''}`}
        tone={m.net_errors ? 'var(--red)' : undefined} />
      <Stat label="Per-minute left"
        value={m.per_minute_remaining != null ? Number(m.per_minute_remaining).toLocaleString() : '--'} />
      <Stat label="Status"
        value={m.blocked ? `Paused ${m.blocked_for_seconds}s` : (m.last_status || '--')}
        tone={m.blocked ? 'var(--red)' : m.last_status === 'OK' ? 'var(--green)' : dim} />
    </div>
  );
}

export default function WorkflowPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const metrics = useApi<any>((s) => api2.flowMetrics(s), [], { refreshMs: 3000 });
  const m = metrics.data;
  return (
    <div className="page">
      <PageHead
        title="Workflow"
        subtitle="How a request moves through Trdgo.us — live. The strip below updates every few seconds; the diagram marks where it slows or pauses. Admin only." />
      <Panel title={<span>● Live pipeline</span>} noBody>
        <LivePipeline m={m} loading={metrics.initialLoading} />
      </Panel>
      <div className="wf-grid">
        <Panel title="1 · Request lifecycle (live)"><RequestFlow m={m} /></Panel>
        <Panel title="2 · Opening a contract"><ContractFlow /></Panel>
        <Panel title="3 · Live tape polling loop"><TapeLoop /></Panel>
      </div>
      <p className="hint" style={{ padding: '2px 4px', marginTop: 8 }}>
        Market data comes from one provider (Unusual Whales). Supabase holds
        sessions, watchlists and the shared cache. Most page loads are served
        from the cache tier and never hit the provider — that is what keeps the
        app clear of the per-minute rate limit.
      </p>
    </div>
  );
}
