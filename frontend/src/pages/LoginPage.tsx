import { useRef, useState } from 'react';
import AuthCard, { type AuthView } from '../components/AuthCard';
import './login-page.css';

// A faint candlestick backdrop (decorative). Deterministic so it never jitters.
function CandleBackdrop() {
  const rnd = (i: number) => Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1;
  const candles = Array.from({ length: 60 }, (_, i) => {
    const x = 12 + i * 32;
    const up = rnd(i + 3) > 0.5;
    const bodyH = 26 + rnd(i) * 90;
    const y = 70 + rnd(i + 5) * 420;
    const wick = 20 + rnd(i + 7) * 44;
    return { x, y, bodyH, wick, up };
  });
  return (
    <svg className="ld-bg" viewBox="0 0 1920 600" preserveAspectRatio="xMidYMid slice"
      aria-hidden="true">
      {candles.map((c, i) => {
        const col = c.up ? 'var(--signal-up, #6f86ff)' : 'var(--signal-down, #f08a9a)';
        return (
          <g key={i} stroke={col} fill={col}>
            <line x1={c.x + 6} x2={c.x + 6} y1={c.y - c.wick} y2={c.y + c.bodyH + c.wick}
              strokeWidth="1.5" />
            <rect x={c.x} y={c.y} width="12" height={c.bodyH} rx="2" opacity="0.9" />
          </g>
        );
      })}
    </svg>
  );
}

export default function LoginPage() {
  const [view, setView] = useState<AuthView>('login');
  const cardRef = useRef<HTMLDivElement>(null);

  const joinNow = () => {
    setView('signup');
    cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  return (
    <div className="ld-shell">
      <CandleBackdrop />

      <header className="ld-nav">
        <div className="ld-logo">
          <span className="ld-logo-mark">▶</span>
          <span className="ld-logo-word">Trdgo<b>.us</b></span>
        </div>
        <nav className="ld-nav-links">
          <a href="#knowledge">Knowledge</a>
          <a href="#pricing">Pricing</a>
          <a href="#contact">Contact Us</a>
        </nav>
      </header>

      <main className="ld-main">
        <section className="ld-hero">
          <h1 className="ld-hero-h">Trade Smarter With AI-Powered Market Intelligence</h1>
          <p className="ld-hero-p">
            Trdgo.us reads the US market — scoring earnings setups across 100
            points, tracking unusual options flow and dark-pool prints, and
            explaining what stocks are doing in plain language, so you can weigh
            a trade instead of guessing at it.
          </p>
          <p className="ld-hero-p">
            Follow upcoming earnings, options straddles, insider and
            institutional moves in one place — with live quotes and the
            catalysts that move your watchlist.
          </p>
          <button className="ld-cta" onClick={joinNow}>Join Now</button>
          <p className="ld-disclaimer">
            This platform provides analysis and decision-support information only.
            It does not guarantee profits or predict market outcomes. Not
            financial advice.
          </p>
        </section>

        <section className="ld-auth" ref={cardRef}>
          <AuthCard view={view} setView={setView} />
        </section>
      </main>
    </div>
  );
}
