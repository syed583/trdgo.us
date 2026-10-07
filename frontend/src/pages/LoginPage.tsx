import { useState } from 'react';
import {
  LogIn, Loader2, Phone, ArrowLeft, ShieldCheck, Gauge, Activity, TrendingUp,
  CheckCircle2,
} from 'lucide-react';
import { api2 } from '../api/client';
import './login-page.css';

// Phone-OTP auth. New users: phone -> WhatsApp code -> name + password (created
// with full access, signed in). Returning users: phone + password. Posts go to
// /auth/* (proxied to the backend in dev). On success a full navigation reloads
// the app with the fresh session cookie.
type Mode = 'signin' | 'phone' | 'verify';

const FEATURES = [
  { icon: Gauge, title: 'Earnings Trade scoring', text: '100-point equity & options setups before every report.' },
  { icon: Activity, title: 'Options flow & straddles', text: 'Unusual activity, IV crush and expected-move analysis.' },
  { icon: TrendingUp, title: 'Live market intelligence', text: 'Dark pool, insider and sector signals in real time.' },
  { icon: ShieldCheck, title: 'Private & secure', text: 'WhatsApp-verified sign-in. Your workspace stays yours.' },
];

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>('signin');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const fail = (e: unknown) => {
    setError((e as Error)?.message || 'Something went wrong. Try again.');
    setBusy(false);
  };

  const signIn = async () => {
    if (busy || !phone.trim() || !password) return;
    setBusy(true); setError('');
    try { await api2.login(phone.trim(), password); window.location.href = '/'; }
    catch (e) { fail(e); }
  };

  const sendCode = async () => {
    if (busy || !phone.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api2.otpRequest(phone.trim());
      setNotice('We sent a code to your WhatsApp. Enter it below.');
      setMode('verify'); setBusy(false);
    } catch (e) { fail(e); }
  };

  const createAccount = async () => {
    if (busy || !code.trim() || !name.trim() || !password) return;
    setBusy(true); setError('');
    try {
      await api2.register(phone.trim(), code.trim(), name.trim(), password);
      window.location.href = '/';
    } catch (e) { fail(e); }
  };

  const reset = (m: Mode) => {
    setMode(m); setError(''); setNotice(''); setCode(''); setPassword('');
  };

  return (
    <div className="lp-shell">
      {/* Brand / value panel */}
      <aside className="lp-hero">
        <div className="lp-hero-top">
          <div className="lp-logo">
            <span className="lp-logo-mark">T</span>
            <span className="lp-logo-word">Trdgo<b>.us</b></span>
          </div>
          <div className="lp-tag">TRADE SMARTER. FASTER.</div>
        </div>
        <div className="lp-hero-mid">
          <h2 className="lp-hero-h">The edge before earnings.</h2>
          <p className="lp-hero-p">
            Scored equity & options setups, live flow and risk alerts — one
            private workspace for your US-market research.
          </p>
          <ul className="lp-feat">
            {FEATURES.map((f) => (
              <li key={f.title}>
                <span className="lp-feat-ic"><f.icon size={16} /></span>
                <span>
                  <b>{f.title}</b>
                  <i>{f.text}</i>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div className="lp-hero-foot">“Data. Discipline. Edge.” — Trdgo.us</div>
      </aside>

      {/* Form panel */}
      <main className="lp-panel">
        <div className="lp-card">
          <div className="lp-steps">
            <span className={`lp-dot ${mode === 'signin' ? 'on' : ''}`} />
            <span className={`lp-dot ${mode !== 'signin' ? 'on' : ''}`} />
          </div>

          {mode === 'signin' && (
            <>
              <h1 className="lp-title">Welcome back</h1>
              <p className="lp-sub">Sign in with your phone number and password.</p>

              <label className="lp-label">Phone number</label>
              <input className="lp-input" value={phone} autoFocus inputMode="tel"
                placeholder="e.g. 919876543210 (with country code)"
                autoComplete="username"
                onChange={(e) => { setPhone(e.target.value); setError(''); }}
                onKeyDown={(e) => { if (e.key === 'Enter') signIn(); }} />

              <label className="lp-label">Password</label>
              <input className="lp-input" type="password" value={password}
                autoComplete="current-password"
                onChange={(e) => { setPassword(e.target.value); setError(''); }}
                onKeyDown={(e) => { if (e.key === 'Enter') signIn(); }} />

              {error && <div className="lp-error">{error}</div>}

              <button className="lp-btn" disabled={busy || !phone.trim() || !password}
                onClick={signIn}>
                {busy ? <Loader2 size={16} className="lp-spin" /> : <LogIn size={16} />}
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
              <div className="lp-switch">
                New to Trdgo.us?{' '}
                <button className="lp-link" onClick={() => reset('phone')}>Create an account</button>
              </div>
            </>
          )}

          {mode === 'phone' && (
            <>
              <h1 className="lp-title">Create your account</h1>
              <p className="lp-sub">We’ll send a verification code to your WhatsApp.</p>

              <label className="lp-label">Phone number</label>
              <input className="lp-input" value={phone} autoFocus inputMode="tel"
                placeholder="e.g. 919876543210 (with country code)"
                onChange={(e) => { setPhone(e.target.value); setError(''); }}
                onKeyDown={(e) => { if (e.key === 'Enter') sendCode(); }} />

              {error && <div className="lp-error">{error}</div>}

              <button className="lp-btn" disabled={busy || !phone.trim()} onClick={sendCode}>
                {busy ? <Loader2 size={16} className="lp-spin" /> : <Phone size={16} />}
                {busy ? 'Sending…' : 'Send WhatsApp code'}
              </button>
              <div className="lp-switch">
                <button className="lp-link" onClick={() => reset('signin')}>
                  <ArrowLeft size={13} /> Back to sign in
                </button>
              </div>
            </>
          )}

          {mode === 'verify' && (
            <>
              <h1 className="lp-title">Verify & finish</h1>
              <p className="lp-sub">Enter your code, your name, and a password to keep.</p>
              {notice && (
                <div className="lp-notice"><CheckCircle2 size={14} /> {notice}</div>
              )}

              <label className="lp-label">Verification code</label>
              <input className="lp-input" value={code} autoFocus inputMode="numeric"
                placeholder="6-digit code"
                onChange={(e) => { setCode(e.target.value); setError(''); }} />

              <label className="lp-label">Your name</label>
              <input className="lp-input" value={name} placeholder="e.g. Alex Carter"
                onChange={(e) => { setName(e.target.value); setError(''); }} />

              <label className="lp-label">Set a password</label>
              <input className="lp-input" type="password" value={password}
                placeholder="At least 6 characters" autoComplete="new-password"
                onChange={(e) => { setPassword(e.target.value); setError(''); }}
                onKeyDown={(e) => { if (e.key === 'Enter') createAccount(); }} />

              {error && <div className="lp-error">{error}</div>}

              <button className="lp-btn"
                disabled={busy || !code.trim() || !name.trim() || !password}
                onClick={createAccount}>
                {busy ? <Loader2 size={16} className="lp-spin" /> : <LogIn size={16} />}
                {busy ? 'Creating…' : 'Create account'}
              </button>
              <div className="lp-switch">
                <button className="lp-link" onClick={() => reset('phone')}>
                  <ArrowLeft size={13} /> Use a different number
                </button>
              </div>
            </>
          )}

          <div className="lp-fineprint">
            By continuing you agree this is a private research tool — not
            financial advice.
          </div>
        </div>
      </main>
    </div>
  );
}
