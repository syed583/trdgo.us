import { useState } from 'react';
import {
  ArrowUpRight, ArrowDownRight, RefreshCw, Info, ShieldAlert,
  Target, LogIn, Ban, Clock,
} from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { num } from '../lib/format';
import './trade-plan.css';

const HORIZONS = ['TODAY', 'TOMORROW', 'SWING'];

const STATUS_LABEL: Record<string, string> = {
  PENDING: 'Waiting for entry',
  ACTIVE: 'In the entry zone',
  TP1_HIT: 'Target 1 hit',
  TP2_HIT: 'Target 2 hit',
  SL_HIT: 'Stopped out',
  EXPIRED: 'Expired untouched',
  INVALIDATED: 'Invalidated',
};
const STATUS_TONE: Record<string, string> = {
  PENDING: 'wait', ACTIVE: 'live', TP1_HIT: 'win', TP2_HIT: 'win',
  SL_HIT: 'loss', EXPIRED: 'flat', INVALIDATED: 'flat',
};

function Price({ v }: { v: number | null | undefined }) {
  return <span className="tp-price">{v == null ? '--' : num(v, 2)}</span>;
}

export default function TradePlanPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const [horizon, setHorizon] = useState('SWING');

  const plan = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.tradePlan(symbol, horizon, s)),
    [symbol, horizon, demo],
    { refreshMs: demo ? undefined : 30_000 },
  );
  const hist = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.tradePlanHistory(symbol, s)),
    [symbol, demo],
  );

  const d = plan.data;
  const status = d?.status;
  const long = d?.bias === 'LONG';
  const t = d?.tracking;

  return (
    <div className="page tp">
      <div className="mf-head">
        <div>
          <h1>Trade Plan — {symbol}</h1>
          <p>
            Data-derived levels: where the setup is supported, where to stop
            chasing, where it's wrong, and where it's going — with the
            reward:risk those levels imply. <b>Analysis, not advice.</b>
          </p>
        </div>
        <div className="mf-head-right">
          <div className="tp-horizons">
            {HORIZONS.map((h) => (
              <button key={h}
                className={`tp-h-btn ${horizon === h ? 'active' : ''}`}
                onClick={() => setHorizon(h)}>{h}</button>
            ))}
          </div>
          <button className="btn-icon" onClick={() => { plan.refresh(); hist.refresh(); }}
            aria-label="Refresh" title="Refresh">
            <RefreshCw size={15} className={plan.loading ? 'tp-spin' : ''} />
          </button>
        </div>
      </div>

      {demo ? (
        <div className="tp-empty">Trade Plan is disabled in demo mode.</div>
      ) : plan.initialLoading ? (
        <div className="tp-empty">Building the plan from live levels…</div>
      ) : !d ? (
        <div className="tp-empty">No plan available.</div>
      ) : status === 'NO_SETUP' ? (
        <div className="tp-card tp-nosetup">
          <div className="tp-bias neutral">NO SETUP · {d.lean}</div>
          <p>The model doesn't point decisively enough to place a plan right now.</p>
          <ul>{(d.reasons || []).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
          <div className="tp-ctx">
            Spot <Price v={d.spot} /> · score {num(d.score, 0)} · support{' '}
            <Price v={d.support} /> · resistance <Price v={d.resistance} />
          </div>
        </div>
      ) : (
        <>
          {/* Status pill: what this plan has actually done */}
          {t && (
            <div className={`tp-status ${STATUS_TONE[t.status] || 'wait'}`}>
              <span className="tp-status-dot" />
              <b>{STATUS_LABEL[t.status] || t.status}</b>
              {t.outcome_note && <span className="tp-status-note">{t.outcome_note}</span>}
              {!t.outcome_note && t.last_price != null && (
                <span className="tp-status-note">Last <Price v={t.last_price} /></span>
              )}
            </div>
          )}

          <div className="tp-card">
            <div className="tp-top">
              <div className={`tp-bias ${long ? 'long' : 'short'}`}>
                {long ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
                {d.bias}
              </div>
              <div className="tp-meta">
                <span>{d.decision}</span>
                <span>score <b>{num(d.score, 0)}</b></span>
                <span>conf <b>{num(d.confidence, 0)}</b></span>
                <span className={`tp-rr ${d.reward_risk >= 2 ? 'good' : ''}`}>
                  R:R <b>{num(d.reward_risk, 2)}</b>
                </span>
              </div>
            </div>

            {status === 'WEAK_SETUP' && (
              <div className="tp-weak">
                <Info size={14} /> {(d.reasons || [])[0]
                  || 'Reward:risk is below threshold — poor location, better to wait.'}
              </div>
            )}

            <div className="tp-levels">
              <div className="tp-lv entry">
                <div className="tp-lv-ic"><LogIn size={16} /></div>
                <div>
                  <div className="tp-lv-l">Entry zone</div>
                  <div className="tp-lv-v">
                    <Price v={d.entry.low} /> – <Price v={d.entry.high} />
                  </div>
                </div>
              </div>
              <div className="tp-lv chase">
                <div className="tp-lv-ic"><Ban size={16} /></div>
                <div>
                  <div className="tp-lv-l">{d.do_not_chase_label}</div>
                  <div className="tp-lv-v"><Price v={d.do_not_chase} /></div>
                </div>
              </div>
              <div className="tp-lv stop">
                <div className="tp-lv-ic"><ShieldAlert size={16} /></div>
                <div>
                  <div className="tp-lv-l">Stop loss</div>
                  <div className="tp-lv-v"><Price v={d.stop} /></div>
                </div>
              </div>
              <div className="tp-lv tp1">
                <div className="tp-lv-ic"><Target size={16} /></div>
                <div>
                  <div className="tp-lv-l">Target 1</div>
                  <div className="tp-lv-v"><Price v={d.targets.tp1} /></div>
                </div>
              </div>
              <div className="tp-lv tp2">
                <div className="tp-lv-ic"><Target size={16} /></div>
                <div>
                  <div className="tp-lv-l">Target 2</div>
                  <div className="tp-lv-v"><Price v={d.targets.tp2} /></div>
                </div>
              </div>
              <div className="tp-lv spot">
                <div className="tp-lv-ic" />
                <div>
                  <div className="tp-lv-l">Spot now</div>
                  <div className="tp-lv-v"><Price v={d.spot} /></div>
                </div>
              </div>
            </div>

            <div className="tp-validity">
              <Clock size={13} /> Valid: {d.validity?.label}
              {' · '}ATR {num(d.atr, 2)}
            </div>

            {d.read && <div className="tp-read">{d.read}</div>}

            <div className="tp-disclaimer">{d.disclaimer}</div>
          </div>
        </>
      )}

      {/* History: what past plans did */}
      {!demo && hist.data?.plans?.length > 1 && (
        <div className="tp-history">
          <h2>Past plans for {symbol}</h2>
          <table>
            <thead>
              <tr>
                <th>Issued</th><th>Bias</th><th className="r">Entry</th>
                <th className="r">Stop</th><th className="r">TP1</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {hist.data.plans.slice(0, 20).map((p: any) => (
                <tr key={p.id}>
                  <td>{(p.issued_at || '').slice(0, 10)}</td>
                  <td className={p.bias === 'LONG' ? 'pos' : 'neg'}>{p.bias}</td>
                  <td className="r">{num(p.entry?.low, 1)}–{num(p.entry?.high, 1)}</td>
                  <td className="r">{num(p.stop, 1)}</td>
                  <td className="r">{num(p.targets?.tp1, 1)}</td>
                  <td>
                    <span className={`tp-chip ${STATUS_TONE[p.status] || 'flat'}`}>
                      {STATUS_LABEL[p.status] || p.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
