import React, { useState } from 'react';
import {
  CheckCircle2, Loader2, MinusCircle, ShieldAlert, Sparkles, TrendingDown, TrendingUp,
} from 'lucide-react';
import { api2 } from '../api/client';
import { money } from '../lib/format';

/**
 * Why a stored call was made.
 *
 * Read from the call as it was saved -- the points each parameter scored at
 * the moment of the decision -- never recomputed from today's data. That is
 * the point of storing it: the explanation on screen is the one that actually
 * produced the call, and it will still be the same explanation next week when
 * the scorecard checks whether the call was right.
 */
export interface StoredCall {
  id: number;
  symbol: string;
  horizon: string;
  origin: string;
  made_at: string | null;
  target_date: string | null;
  decision: string;
  direction_score: number | null;
  confidence: number | null;
  agreement_pct: number | null;
  coverage_pct: number | null;
  price_at_call: number | null;
  model_version?: string | null;
  why: {
    for: WhyItem[];
    against: WhyItem[];
    missing: string[];
    blocked: string[];
    explanation?: string | null;
    buy_sell?: BuySell | null;
  };
  outcome?: {
    evaluated_at: string | null;
    return_pct: number | null;
    excess_pct: number | null;
    correct: boolean | null;
  };
}

interface WhyItem {
  label: string;
  points: number | null;
  points_label?: string | null;
  detail?: string | null;
}

interface BuySellRow {
  name: string; label: string;
  buy_weight: number; sell_weight: number;
  buy_points: number | null; sell_points: number | null;
  available: boolean;
}
interface BuySell {
  buy_score: number | null; sell_score: number | null;
  buy_points_total: number; buy_weight_total: number;
  sell_points_total: number; sell_weight_total: number;
  rows: BuySellRow[];
}

function when(iso: string | null): string {
  if (!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export default function WhyCall({ call, compact = false }: {
  call: StoredCall; compact?: boolean;
}) {
  const decision = (call.decision || '').toUpperCase();
  const sell = decision.includes('SELL');
  const withheld = decision === 'DO NOT TRADE' || decision === 'WAIT';
  const side = sell ? 'sell' : withheld ? 'wait' : 'buy';
  const w = call.why;

  return (
    <div className={`why why-${side} ${compact ? 'why-compact' : ''}`}>
      <div className="why-head">
        <b className="why-decision">
          {sell ? <TrendingDown size={14} /> : <TrendingUp size={14} />}
          Why {decision}
        </b>
        <span className="why-figs">
          <em>Score <b>{call.direction_score == null ? '--' : Math.round(call.direction_score)}</b></em>
          <em>Conf <b>{Math.round(call.confidence ?? 0)}%</b></em>
          <em>Agree <b>{Math.round(call.agreement_pct ?? 0)}%</b></em>
          {call.price_at_call != null && <em>At <b>{money(call.price_at_call)}</b></em>}
        </span>
      </div>

      <PlainEnglish call={call} />

      {w.blocked.length > 0 && (
        <div className="why-block">
          <ShieldAlert size={13} />
          <div>{w.blocked.map((b) => <span key={b}>{b}</span>)}</div>
        </div>
      )}

      <div className="why-cols">
        <div>
          <h5><CheckCircle2 size={12} /> Pushed toward {sell ? 'sell' : 'buy'}</h5>
          {w.for.length === 0 ? <p className="why-none">Nothing strongly.</p>
            : w.for.map((i) => <Item key={i.label} item={i} good />)}
        </div>
        <div>
          <h5><MinusCircle size={12} /> Pushed against it</h5>
          {w.against.length === 0 ? <p className="why-none">Nothing against.</p>
            : w.against.map((i) => <Item key={i.label} item={i} />)}
        </div>
      </div>

      {w.buy_sell && <BuySellBreakdown bd={w.buy_sell} />}

      {w.missing.length > 0 && (
        <p className="why-missing">No data for: {w.missing.join(', ')}</p>
      )}

      <p className="why-foot">
        {call.id ? `Saved ${when(call.made_at)} · #${call.id} · ` : ''}{call.horizon.toLowerCase()}
        {call.outcome?.evaluated_at && call.outcome.correct != null && (
          <> · <b className={call.outcome.correct ? 'pos' : 'neg'}>
            {call.outcome.correct ? 'Right' : 'Wrong'}
            {call.outcome.excess_pct != null
              ? ` (${call.outcome.excess_pct > 0 ? '+' : ''}${call.outcome.excess_pct.toFixed(2)}% vs SPY)`
              : ''}
          </b></>
        )}
      </p>
    </div>
  );
}

/** The dual-model view: how many points each parameter gives the BUY score and
 *  the SELL score, with the two totals. */
function BuySellBreakdown({ bd }: { bd: BuySell }) {
  const [open, setOpen] = useState(true);
  const rows = bd.rows.filter((r) => r.available);
  if (rows.length === 0) return null;
  return (
    <div className="why-bs">
      <button className="why-bs-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>Buy / Sell score breakdown</span>
        <span className="why-bs-tot">
          <b className="pos">BUY {bd.buy_score == null ? '--' : Math.round(bd.buy_score)}</b>
          <b className="neg">SELL {bd.sell_score == null ? '--' : Math.round(bd.sell_score)}</b>
        </span>
      </button>
      {open && (
        <table className="why-bs-tbl">
          <thead>
            <tr><th>Parameter</th><th>Buy</th><th>Sell</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td>{r.label}</td>
                <td className="pos">{r.buy_points == null ? '·' : `+${r.buy_points}`}
                  <em> / {r.buy_weight}</em></td>
                <td className="neg">{r.sell_points == null ? '·' : `+${r.sell_points}`}
                  <em> / {r.sell_weight}</em></td>
              </tr>
            ))}
            <tr className="why-bs-sum">
              <td>Total</td>
              <td className="pos">{bd.buy_points_total} <em>/ {bd.buy_weight_total}</em></td>
              <td className="neg">{bd.sell_points_total} <em>/ {bd.sell_weight_total}</em></td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}

function Item({ item, good = false }: { item: WhyItem; good?: boolean }) {
  return (
    <div className="why-item">
      <div className="why-item-top">
        <span>{item.label}</span>
        <b className={good ? 'pos' : 'neg'}>{item.points_label || item.points}</b>
      </div>
      {item.detail && <i>{item.detail}</i>}
    </div>
  );
}


/**
 * The call in two plain sentences, and the tone of the symbol's headlines.
 *
 * Written by Claude from the stored call's own figures, only when asked:
 * every scan would otherwise pay for explanations nobody reads. Once written
 * it is saved with the call, so opening it again is instant and reads the
 * same. Claude supplies words here, never numbers -- every figure it mentions
 * comes from the call it was handed.
 */
function PlainEnglish({ call }: { call: StoredCall }) {
  const [text, setText] = useState<string | null>(call.why.explanation || null);
  const [news, setNews] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api2.explainCall(call.id);
      if (r.status !== 'OK') {
        setError(r.detail || 'The explanation is not available right now.');
      } else {
        setText(r.explanation);
        setNews(r.news || null);
      }
    } catch (e: any) {
      setError(e?.message || 'The explanation request failed.');
    } finally {
      setBusy(false);
    }
  };

  // No stored id yet (the call is still being written) -- the explanation
  // request needs one, so offer it only once the call exists.
  if (!text && !call.id) return null;

  if (!text) {
    return (
      <div className="why-ai">
        <button className="why-ai-btn" onClick={ask} disabled={busy}>
          {busy ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />}
          {busy ? 'Writing the explanation…' : 'Explain in plain English'}
        </button>
        {error && <span className="why-ai-err">{error}</span>}
      </div>
    );
  }

  const tone = news?.status === 'OK' ? String(news.text || '') : '';
  const toneKind = tone.split(':')[0].toLowerCase();

  return (
    <div className="why-ai why-ai-done">
      <p className="why-text"><Sparkles size={12} /> {text}</p>
      {tone && (
        <p className={`why-news why-news-${toneKind}`}>
          <b>News</b> {tone}
          {news.headline_count ? <em> · {news.headline_count} headlines</em> : null}
        </p>
      )}
      {!tone && news && news.status !== 'OK' && (
        <p className="why-news"><b>News</b> {news.detail}</p>
      )}
      {!news && (
        <button className="why-ai-link" onClick={ask} disabled={busy}>
          {busy ? 'Reading the news…' : 'Add news tone'}
        </button>
      )}
      <p className="why-ai-note">Written by Claude from the figures above; it adds no data of its own.</p>
    </div>
  );
}
