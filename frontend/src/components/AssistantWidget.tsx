import { useEffect, useRef, useState } from 'react';
import { X, Send, Loader2 } from 'lucide-react';
import { api2 } from '../api/client';
import './assistant.css';

interface Msg {
  role: 'user' | 'assistant';
  content: string;
}

/** A friendly bot mark — antenna, rounded head, two eyes and a smile. */
function BotLogo({ size = 24 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3.5v2.2" />
      <circle cx="12" cy="2.6" r="1" fill="currentColor" stroke="none" />
      <rect x="4" y="6.5" width="16" height="12" rx="4" />
      <path d="M2 11v3" />
      <path d="M22 11v3" />
      <circle cx="9" cy="12" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="15" cy="12" r="1.2" fill="currentColor" stroke="none" />
      <path d="M9.5 15.4c.8.6 1.6.9 2.5.9s1.7-.3 2.5-.9" />
    </svg>
  );
}

/**
 * The Trdgo assistant: a floating chat that answers questions about the ticker
 * on screen, powered by Claude and grounded on the app's live Unusual Whales
 * data. It explains what the data shows -- it does not give trading advice, and
 * the backend's system prompt holds that line.
 */
export default function AssistantWidget({ symbol }: { symbol: string }) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, busy]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const history = msgs.slice(-6);
    const next = [...msgs, { role: 'user' as const, content: text }];
    setMsgs(next);
    setInput('');
    setBusy(true);
    try {
      const res = await api2.chat(text, symbol, history);
      const reply = res.status === 'OK' && res.text
        ? res.text
        : (res.detail || 'The assistant could not answer right now.');
      setMsgs((m) => [...m, { role: 'assistant', content: reply }]);
    } catch (err: any) {
      const msg = err?.status === 401
        ? 'Please sign in to use the assistant.'
        : (err?.message || 'The assistant is unavailable right now.');
      setMsgs((m) => [...m, { role: 'assistant', content: msg }]);
    } finally {
      setBusy(false);
    }
  }

  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const suggestions = [
    `What is the options flow saying on ${symbol}?`,
    `Explain the key levels for ${symbol}`,
    `What does net GEX mean here?`,
  ];

  if (!open) {
    return (
      <button
        className="asst-fab"
        onClick={() => setOpen(true)}
        aria-label="Open the Trdgo assistant"
        title="Ask the Trdgo assistant"
      >
        <BotLogo size={26} />
      </button>
    );
  }

  return (
    <div className="asst-panel" role="dialog" aria-label="Trdgo assistant">
      <div className="asst-head">
        <div className="asst-title">
          <span className="asst-avatar"><BotLogo size={20} /></span>
          <div>
            <div className="asst-name">Trdgo Assistant</div>
            <div className="asst-sub">
              Grounded on live data for <b>{symbol}</b>
            </div>
          </div>
        </div>
        <button className="btn-icon" onClick={() => setOpen(false)} aria-label="Close">
          <X size={18} />
        </button>
      </div>

      <div className="asst-body" ref={scrollRef}>
        {msgs.length === 0 && (
          <div className="asst-empty">
            <p>
              Ask about flow, levels, sentiment or any metric for the ticker
              you're viewing. I explain what the data shows — I don't give
              trading advice.
            </p>
            <div className="asst-chips">
              {suggestions.map((s) => (
                <button key={s} className="asst-chip" onClick={() => setInput(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`asst-msg asst-${m.role}`}>
            {m.content}
          </div>
        ))}
        {busy && (
          <div className="asst-msg asst-assistant asst-typing">
            <Loader2 size={14} className="asst-spin" /> Thinking…
          </div>
        )}
      </div>

      <div className="asst-input">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder={`Ask about ${symbol}…`}
          rows={1}
        />
        <button
          className="btn-primary asst-send"
          onClick={send}
          disabled={busy || !input.trim()}
          aria-label="Send"
        >
          <Send size={16} />
        </button>
      </div>
      <div className="asst-foot">
        Analysis only · not financial advice · powered by Claude on your UW data
      </div>
    </div>
  );
}
