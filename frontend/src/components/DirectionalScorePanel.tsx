import React, { useState } from 'react';
import { ChevronRight, Info, TrendingDown, TrendingUp, X } from 'lucide-react';
import { api, api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { HORIZON_COPY, defaultHorizon } from './HorizonSwitch';
import type { Horizon } from './HorizonSwitch';
import { Panel, StateBlock } from './common';

const GREEN = '#21d07a';
const RED = '#f2465a';
const DIM = '#8892a8';

/**
 * The twenty points the model reads from the company rather than the market:
 * who owns it, what it earned, what it filed. Named here because the payload
 * carries weights rather than which list a parameter belongs to, and this
 * panel is where a reader checks that the eighty and the twenty add up.
 */
const COMPANY_PARAMS = new Set([
  'insider_activity', 'fund_flows', 'earnings_results', 'merger_activity',
  'funding_activity', 'dividend_trend', 'event_radar',
]);

interface DirSignal {
  name: string;
  label: string;
  weight: number;
  available: boolean;
  bias: number | null;
  points: number;
  points_label: string;
  directional: boolean;
  detail: string;
  rule: string;
  evidence: Record<string, any>;
  source: string;
  unavailable_reason: string;
  leaning: string | null;
}

interface Directional {
  symbol: string;
  direction_score: number | null;
  decision: string;
  confidence: number;
  coverage_pct: number;
  lean?: string;
  actionable?: boolean;
  blocked_reasons?: string[];
  agreement_pct?: number;
  conviction_pct?: number;
  weight_available?: number;
  weight_possible?: number;
  signals: DirSignal[];
  volatility_context?: { parameters: DirSignal[]; notes: string[] };
  reasons?: string[];
  note?: string;
  status: string;
}

function toneOf(decision: string): string {
  if (decision === 'DO NOT TRADE') return 'block';
  if (decision.includes('BUY')) return 'pos';
  if (decision.includes('SELL')) return 'neg';
  return '';
}

/**
 * The general directional model.
 *
 * Every parameter is clickable. The panel shows what each one scored; the
 * drawer behind it shows the rule that produced that score and the raw
 * numbers the rule was applied to -- so "why is this a buy" is answered with
 * the actual arithmetic rather than a restated label.
 */
export default function DirectionalScorePanel({ symbol, horizon }: {
  symbol: string; horizon?: string;
}) {
  const [open, setOpen] = useState<DirSignal | null>(null);

  // The outlook, defaulted the same way the analysis screen defaults it --
  // today during the session, tomorrow otherwise. Asking for the swing score
  // here while the analysis screen showed today's meant the two pages
  // reported different decisions for the same stock at the same moment, with
  // nothing on either to say why.
  const clock = useApi<any>((s) => api.status(s), []);
  const session = clock.data?.market?.session as string | undefined;
  const outlook = horizon || defaultHorizon(session);

  const state = useApi<Directional>(
    (signal) => api2.directional(symbol, outlook, signal),
    [symbol, outlook],
  );
  const d = state.data;

  if (state.loading && !d) {
    return (
      <Panel title="US-Stock Reader Directional Score" icon={<Info size={12} />} noBody>
        <StateBlock loading />
      </Panel>
    );
  }
  if (state.error || !d) {
    return (
      <Panel title="US-Stock Reader Directional Score" icon={<Info size={12} />} noBody>
        <StateBlock error={state.error || 'Unavailable'} />
      </Panel>
    );
  }
  if (d.status !== 'OK' || d.direction_score === null) {
    return (
      <Panel title="US-Stock Reader Directional Score" icon={<Info size={12} />} noBody>
        <StateBlock status={d.status} />
      </Panel>
    );
  }

  const tone = toneOf(d.decision);
  const gaugeColour = tone === 'pos' ? GREEN
    : tone === 'neg' ? RED
      : 'var(--amber)';
  // The model's own two lists, in the order it weighs them: eighty points of
  // market and price, twenty of company and ownership. Splitting the panel by
  // directional-or-not instead put implied volatility in a section of its own
  // while burying the eighty/twenty split the weights are actually built
  // around -- and this panel is the one place a reader checks the arithmetic.
  const inCompany = (s: DirSignal) => COMPANY_PARAMS.has(s.name);
  const byWeight = (a: DirSignal, b: DirSignal) => (b.weight || 0) - (a.weight || 0);
  const market = d.signals.filter((s) => !inCompany(s)).sort(byWeight);
  const company = d.signals.filter(inCompany).sort(byWeight);

  // What each half of the panel is worth, and how much of it was measured.
  // The rows already carry their own weights; without the totals a reader
  // has to add twenty numbers to learn that eighty-two of the hundred points
  // vote on direction and the rest only size the trade.
  const sum = (rows: DirSignal[], only?: (s: DirSignal) => boolean) =>
    rows.filter((s) => !only || only(s))
      .reduce((total, s) => total + (s.weight || 0), 0);
  const marketTotal = sum(market);
  const marketMeasured = sum(market, (s) => !!s.available);
  const companyTotal = sum(company);
  const companyMeasured = sum(company, (s) => !!s.available);
  // How much of the whole reading actually votes, stated once rather than
  // implied by a section heading.
  const sizingTotal = sum(d.signals, (s) => !s.directional);

  return (
    <>
      <Panel
        title="US-Stock Reader Directional Score"
        icon={<Info size={12} />}
        noBody
        right={(
          <span className="dir-coverage">
            {HORIZON_COPY[outlook as Horizon]?.label || outlook} outlook ·
            {' '}{d.coverage_pct}% covered
          </span>
        )}
      >
        <div className="score-wrap">
          <div>
            <div className="gauge-box">
              <Gauge value={d.direction_score} colour={gaugeColour} />
              <div className="gauge-center">
                <div className="gauge-num" style={{ color: gaugeColour }}>
                  {Math.round(d.direction_score)}
                </div>
                <div className="gauge-den">/100</div>
              </div>
            </div>
            <div className="gauge-verdict" style={{ color: gaugeColour }}>
              {d.decision.includes('SELL') ? <TrendingDown size={13} />
                : d.decision.includes('BUY') ? <TrendingUp size={13} /> : null}
              {d.decision}
            </div>
          </div>

          <div className="comp-list">
            <div className="dir-section first">
              Market &amp; price
              <span className="dir-note">
                {' '}— {marketTotal} points, {marketMeasured} measured
                {sizingTotal > 0 && `, of which ${sizingTotal} size only`}
              </span>
            </div>
            {market.map((s) => (
              <SignalRow key={s.name} signal={s} onOpen={() => setOpen(s)} />
            ))}

            <div className="dir-section">
              Company &amp; ownership
              <span className="dir-note">
                {' '}— {companyTotal} points, {companyMeasured} measured
              </span>
            </div>
            {company.map((s) => (
              <SignalRow key={s.name} signal={s} onOpen={() => setOpen(s)} />
            ))}
          </div>
        </div>

        {/* When the reading is not solid enough to act on, that comes first
            and states why. Burying it under the parameter list would let the
            direction number read as a recommendation it is not. */}
        {d.actionable === false && (
          <div className="dir-block">
            <div className="db-head">Not a tradeable setup right now</div>
            <ul>
              {(d.blocked_reasons || []).map((r) => <li key={r}>{r}</li>)}
            </ul>
            {d.lean && d.lean !== 'NO_DATA' && (
              <div className="db-lean">
                The evidence leans <b>{d.lean}</b>, but not strongly or
                consistently enough to act on.
              </div>
            )}
          </div>
        )}

        <div className="dir-meters">
          <Meter label="Coverage" value={d.coverage_pct}
            hint="How much of the model's weight actually returned data" />
          <Meter label="Agreement" value={d.agreement_pct ?? 0}
            hint="How much of the available weight leans the same way" />
          <Meter label="Conviction" value={d.conviction_pct ?? 0}
            hint="How far the reading sits from neutral" />
        </div>

        <div className="confidence">
          <div className="conf-top">
            <span className="lbl">Confidence Score <Info size={11} color="var(--text-mute)" /></span>
            <span className="mono">
              <b style={{ fontSize: 13 }}>{Math.round(d.confidence)}</b>
              <span style={{ color: 'var(--text-mute)' }}>/100</span>
            </span>
          </div>
          <div className="conf-track">
            <div className="conf-fill" style={{ width: `${d.confidence}%` }} />
          </div>
          <div className="dir-meters">
            <Meter label="Coverage" value={d.coverage_pct}
              hint="How much of the model's weight actually returned data" />
            <Meter label="Agreement" value={d.agreement_pct ?? 0}
              hint="How much of the available weight leans the same way" />
            <Meter label="Conviction" value={d.conviction_pct ?? 0}
              hint="How far the reading sits from neutral" />
          </div>
        </div>

        <div className="hint">{d.note}</div>
      </Panel>

      {open && <ExplainDrawer signal={open} onClose={() => setOpen(null)} />}
    </>
  );
}

/** Ring gauge. Nothing clever -- one arc for the track, one for the value. */
function Gauge({ value, colour }: { value: number; colour: string }) {
  const r = 46;
  const c = 2 * Math.PI * r;
  const filled = Math.max(0, Math.min(100, value)) / 100 * c;
  return (
    <svg viewBox="0 0 110 110" width="110" height="110">
      <circle cx="55" cy="55" r={r} fill="none" strokeWidth="9"
        stroke="var(--panel-2)" />
      <circle cx="55" cy="55" r={r} fill="none" strokeWidth="9"
        stroke={colour} strokeLinecap="round"
        strokeDasharray={`${filled} ${c - filled}`}
        transform="rotate(-90 55 55)" />
    </svg>
  );
}

function Meter({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="dir-meter" title={hint}>
      <div className="dm-top">
        <span>{label}</span><b>{Math.round(value)}%</b>
      </div>
      <div className="dm-track">
        <i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </div>
    </div>
  );
}

function SignalRow({ signal, onOpen }: { signal: DirSignal; onOpen: () => void }) {
  const colour = !signal.available ? DIM
    : signal.points > 0 ? GREEN
      : signal.points < 0 ? RED : DIM;

  // Bar is drawn against this parameter's own maximum, so a reader can see
  // how much of its available weight it actually used.
  const fill = signal.available && signal.weight
    ? Math.abs(signal.points) / signal.weight * 100
    : 0;

  return (
    <button className={`dir-row ${signal.available ? '' : 'off'}`} onClick={onOpen}>
      <span className="dr-label">
        {signal.label}
        <i className="dr-weight">{signal.weight}</i>
      </span>
      <span className="dr-bar">
        <i style={{ width: `${fill}%`, background: colour }} />
      </span>
      <span className="dr-points" style={{ color: colour }}>
        {signal.available
          ? (signal.directional ? signal.points_label : 'sizing only')
          : 'no data'}
      </span>
      <ChevronRight size={12} color="var(--text-mute)" />
    </button>
  );
}

/**
 * The reasoning behind one parameter.
 *
 * Shows the rule before the numbers on purpose: a figure without the rule
 * that consumed it invites the reader to draw their own conclusion, which is
 * exactly what an explanation is supposed to remove.
 */
function ExplainDrawer({ signal, onClose }: { signal: DirSignal; onClose: () => void }) {
  const colour = !signal.available ? DIM
    : signal.points > 0 ? GREEN : signal.points < 0 ? RED : DIM;

  return (
    <div className="dir-overlay" onClick={onClose}>
      <div className="dir-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="dd-head">
          <div>
            <div className="dd-title">{signal.label}</div>
            <div className="dd-sub">
              Worth up to {signal.weight} points
              {signal.directional ? '' : ' · does not vote on direction'}
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={15} />
          </button>
        </div>

        <div className="dd-score" style={{ color: colour }}>
          {signal.available
            ? (signal.directional ? signal.points_label : 'Sizing input')
            : 'No data'}
          {signal.leaning && signal.available && (
            <span className="dd-lean">{signal.leaning}</span>
          )}
        </div>

        {signal.available ? (
          <>
            <div className="dd-block">
              <div className="dd-h">What it found</div>
              <p>{signal.detail || '--'}</p>
            </div>
            <div className="dd-block">
              <div className="dd-h">How it is scored</div>
              <p>{signal.rule}</p>
            </div>
            <div className="dd-block">
              <div className="dd-h">Figures used</div>
              <table className="tbl dd-table">
                <tbody>
                  {Object.entries(signal.evidence || {}).map(([k, v]) => (
                    <tr key={k}>
                      <td className="dd-k">{k.replace(/_/g, ' ')}</td>
                      <td className="num r">{format(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="dd-block">
            <div className="dd-h">Why there is no score</div>
            <p>{signal.unavailable_reason}</p>
            <p className="dd-muted">
              A missing parameter lowers confidence rather than scoring
              neutral, so this reading is thinner than it would otherwise be
              — it is not being treated as balanced evidence.
            </p>
          </div>
        )}

        <div className="dd-source">
          <Info size={10} /> Source: {signal.source || 'unknown'}
        </div>
      </div>
    </div>
  );
}

function format(value: any): string {
  if (value === null || value === undefined) return '--';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') {
    if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
    if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
    if (Math.abs(value) >= 1e3) return value.toLocaleString();
    return String(Math.round(value * 100) / 100);
  }
  if (Array.isArray(value)) return value.slice(0, 4).map(format).join(', ');
  if (typeof value === 'object') {
    return Object.entries(value)
      .slice(0, 4)
      .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${format(v)}`)
      .join(' · ');
  }
  return String(value);
}
