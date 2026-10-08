import { useRef, useState } from 'react';
import {
  Activity, BarChart3, CalendarDays, ArrowLeftRight, Sparkles,
  Boxes, Box, Share2, Zap, Play, ShieldCheck,
} from 'lucide-react';
import AuthCard, { type AuthView } from '../components/AuthCard';
import logoImg from '../assets/logo.png';
import './login-page.css';

const FEATURES = [
  { ic: <Activity size={18} />, t: 'Real-time Data', s: 'Live prices & flow', c: 'indigo' },
  { ic: <CalendarDays size={18} />, t: 'Earnings Insights', s: 'Calendar & analysis', c: 'emerald' },
  { ic: <ArrowLeftRight size={18} />, t: 'Options Flow', s: 'Unusual activity', c: 'amber' },
  { ic: <Sparkles size={18} />, t: 'AI Analysis', s: 'Easy explanations', c: 'pink' },
];
const STATS = [
  { ic: <Boxes size={18} />, v: '20K+', l: 'US Symbols', c: 'emerald' },
  { ic: <Box size={18} />, v: '100+', l: 'Analysis Points', c: 'indigo' },
  { ic: <Share2 size={18} />, v: '12', l: 'Data Sources', c: 'cyan' },
  { ic: <Zap size={18} />, v: 'Live', l: 'Prices & Flow', c: 'amber' },
];
// Decorative floating quote cards on the market stage.
const CALLOUTS = [
  { sym: 'NVDA', chg: '+4.26%', up: true, d: 'M0,15 L10,12 L20,16 L30,6 L40,11 L50,2', pos: 'c1' },
  { sym: 'TSLA', chg: '-1.32%', up: false, d: 'M0,4 L12,8 L22,4 L32,14 L42,9 L50,18', pos: 'c2' },
  { sym: 'AAPL', chg: '+0.95%', up: true, d: 'M0,18 L10,14 L20,15 L32,8 L40,11 L50,3', pos: 'c3' },
];

export default function LoginPage() {
  const [view, setView] = useState<AuthView>('login');
  const cardRef = useRef<HTMLDivElement>(null);

  const joinNow = () => {
    setView('signup');
    cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  const tryDemo = () => { window.location.href = '/dashboard?demo=1'; };

  return (
    <div className="ld-shell">
      <div className="ld-glow ld-glow-1" />
      <div className="ld-glow ld-glow-2" />

      <header className="ld-nav">
        <div className="ld-logo">
          <img src={logoImg} alt="Tradgo.us" className="ld-logo-img" />
        </div>
        <nav className="ld-nav-links">
          <a href="#features">Features</a>
          <a href="#markets">Markets</a>
          <a href="#pricing">Pricing</a>
          <a href="#knowledge">Knowledge</a>
          <a href="#contact">Contact Us</a>
        </nav>
        <div className="ld-nav-actions">
          <button className="ld-nav-demo" onClick={tryDemo}>
            <Play size={13} /> Try Demo
          </button>
          <button className="ld-nav-cta" onClick={joinNow}>Get Started</button>
        </div>
      </header>

      <main className="ld-main">
        <section className="ld-hero">
          <div className="ld-badge">
            <BarChart3 size={14} /> AI-POWERED MARKET INTELLIGENCE
          </div>
          <h1 className="ld-hero-h">
            Trade Smarter With <br />
            <span className="ld-grad">AI-Powered Market Intelligence</span>
          </h1>
          <p className="ld-hero-p">
            Trdgo.us reads the US market — scoring earnings setups across 100+
            points, tracking unusual options flow and dark-pool prints, and
            explaining what stocks are doing in plain language, so you can make
            smarter trades.
          </p>

          <div className="ld-pills">
            {FEATURES.map((f) => (
              <div key={f.t} className={`ld-pill ${f.c}`}>
                <div className="ld-pill-ic">{f.ic}</div>
                <div>
                  <div className="ld-pill-t">{f.t}</div>
                  <div className="ld-pill-s">{f.s}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="ld-stage">
            <div className="ld-stage-head">
              <span className="ld-live"><i /> Live Market</span>
              <span className="ld-stage-sub">Today's movers</span>
            </div>
            <div className="ld-stage-body">
              <div className="ld-candles">
                {[20, 16, 28, 36, 24, 32, 22, 30].map((h, i) => (
                  <span key={i} className={`ld-candle ${i % 3 === 1 ? 'dn' : 'up'}`}
                    style={{ height: `${h * 2.1}px` }} />
                ))}
              </div>
              <div className="ld-quotes">
                {CALLOUTS.map((c) => (
                  <div key={c.sym} className={`ld-quote ${c.up ? 'up' : 'dn'}`}>
                    <span className="ld-quote-sym">{c.sym}</span>
                    <svg className="ld-quote-spark" viewBox="0 0 50 20"><path d={c.d} /></svg>
                    <span className="ld-quote-chg">{c.chg}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="ld-stats">
            {STATS.map((s) => (
              <div key={s.l} className="ld-stat">
                <div className={`ld-stat-ic ${s.c}`}>{s.ic}</div>
                <div>
                  <div className="ld-stat-v">{s.v}</div>
                  <div className="ld-stat-l">{s.l}</div>
                </div>
              </div>
            ))}
          </div>

          <p className="ld-disclaimer">
            This platform provides analysis and decision-support information only.
            It does not guarantee profits or predict market outcomes. Not financial advice.
          </p>
        </section>

        <section className="ld-auth" ref={cardRef}>
          <AuthCard view={view} setView={setView} />
          <div className="ld-auth-trust">
            <ShieldCheck size={13} /> Your data is secure and never shared with third parties.
          </div>
        </section>
      </main>

      <footer className="ld-foot">
        © 2026 Tradgo.us Market Technologies. All rights reserved. Trade smarter with precision intelligence.
      </footer>
    </div>
  );
}
