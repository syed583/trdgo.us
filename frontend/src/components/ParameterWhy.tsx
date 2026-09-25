import React, { useState } from 'react';
import { ExternalLink, Loader2, Sparkles } from 'lucide-react';
import { api2 } from '../api/client';
import { safeHref } from '../lib/format';

/**
 * "What does this parameter actually mean for this company?"
 *
 * The row above already says the arithmetic -- eight points, scored plus
 * four. What it cannot say is who left, what the company raised, or why
 * options are priced where they are, because the model reads item codes and
 * ratios rather than the filings behind them.
 *
 * So this asks on demand. On demand for two reasons: every answer costs a
 * Claude request, and most parameters on most stocks raise no question worth
 * paying for. The answer is cached for the day on the server, so a second
 * look is free and reads the same.
 *
 * Claude writes the words; every figure it uses was measured by this app and
 * handed to it, and the filings it summarises are linked underneath so the
 * reader can check rather than trust.
 */
export default function ParameterWhy({ symbol, parameter, label, horizon }: {
  symbol: string; parameter: string; label: string; horizon?: string;
}) {
  const [text, setText] = useState<string | null>(null);
  const [filings, setFilings] = useState<any[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      // The outlook matters: a session reading like VWAP is scored inside
      // today's outlook and nowhere else.
      const r = await api2.explainParameter(symbol, parameter, horizon);
      if (r.status !== 'OK') {
        setError(r.detail || 'No explanation available for this reading.');
      } else {
        setText(r.text);
        setFilings(r.filings || []);
        setNote(r.note || null);
      }
    } catch (e: any) {
      setError(e?.message || 'The request failed.');
    } finally {
      setBusy(false);
    }
  };

  if (!text) {
    return (
      <div className="pw">
        <button className="pw-btn" onClick={ask} disabled={busy}>
          {busy ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />}
          {busy ? `Reading the filings for ${label}…`
            : `What does ${label} mean for ${symbol}?`}
        </button>
        {error && <span className="pw-err">{error}</span>}
      </div>
    );
  }

  return (
    <div className="pw pw-done">
      <p className="pw-text"><Sparkles size={12} /> {text}</p>
      {filings.length > 0 && (
        <div className="pw-filings">
          {filings.slice(0, 4).map((f) => (
            <a key={`${f.filed}-${f.event}`} href={safeHref(f.url)} target="_blank"
              rel="noopener noreferrer">
              {f.filed} · {f.event} <ExternalLink size={10} />
            </a>
          ))}
        </div>
      )}
      {note && <p className="pw-note">{note}</p>}
    </div>
  );
}
