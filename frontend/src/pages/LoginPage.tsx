import { useState } from 'react';
import { Loader2, Phone, Eye, EyeOff, PlayCircle, Lock } from 'lucide-react';
import { api2 } from '../api/client';
import './login-page.css';

// Phone-OTP auth with a Log In / Sign Up card.
//   login         phone + password
//   signup        first/last/age/phone -> Send OTP
//   signupVerify  code + password -> create account
//   forgot        phone -> Send OTP (existing accounts only)
//   forgotVerify  code + new password -> reset
// Posts go to /auth/* (proxied to the backend in dev). On success a full
// navigation reloads the app with the fresh session cookie.
type View = 'login' | 'signup' | 'signupVerify' | 'forgot' | 'forgotVerify';

const COUNTRIES: { flag: string; dial: string; name: string }[] = [
  { flag: '🇦🇪', dial: '971', name: 'UAE' },
  { flag: '🇮🇳', dial: '91', name: 'India' },
  { flag: '🇺🇸', dial: '1', name: 'USA / Canada' },
  { flag: '🇬🇧', dial: '44', name: 'UK' },
  { flag: '🇸🇦', dial: '966', name: 'Saudi Arabia' },
  { flag: '🇶🇦', dial: '974', name: 'Qatar' },
  { flag: '🇰🇼', dial: '965', name: 'Kuwait' },
  { flag: '🇧🇭', dial: '973', name: 'Bahrain' },
  { flag: '🇴🇲', dial: '968', name: 'Oman' },
  { flag: '🇵🇰', dial: '92', name: 'Pakistan' },
  { flag: '🇧🇩', dial: '880', name: 'Bangladesh' },
  { flag: '🇱🇰', dial: '94', name: 'Sri Lanka' },
  { flag: '🇳🇵', dial: '977', name: 'Nepal' },
  { flag: '🇸🇬', dial: '65', name: 'Singapore' },
  { flag: '🇲🇾', dial: '60', name: 'Malaysia' },
  { flag: '🇦🇺', dial: '61', name: 'Australia' },
  { flag: '🇩🇪', dial: '49', name: 'Germany' },
  { flag: '🇫🇷', dial: '33', name: 'France' },
  { flag: '🇪🇸', dial: '34', name: 'Spain' },
  { flag: '🇮🇹', dial: '39', name: 'Italy' },
  { flag: '🇳🇱', dial: '31', name: 'Netherlands' },
  { flag: '🇿🇦', dial: '27', name: 'South Africa' },
  { flag: '🇳🇬', dial: '234', name: 'Nigeria' },
  { flag: '🇧🇷', dial: '55', name: 'Brazil' },
  { flag: '🇯🇵', dial: '81', name: 'Japan' },
  { flag: '🇨🇳', dial: '86', name: 'China' },
  { flag: '🇹🇷', dial: '90', name: 'Turkey' },
  { flag: '🇪🇬', dial: '20', name: 'Egypt' },
];

// A faint candlestick backdrop (decorative). Deterministic so it never jitters.
function CandleBackdrop() {
  const rnd = (i: number) => Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1;
  const candles = Array.from({ length: 26 }, (_, i) => {
    const x = 16 + i * 31;
    const up = rnd(i + 3) > 0.5;
    const bodyH = 24 + rnd(i) * 70;
    const y = 90 + rnd(i + 5) * 300;
    const wick = 18 + rnd(i + 7) * 36;
    return { x, y, bodyH, wick, up };
  });
  return (
    <svg className="lp-bg" viewBox="0 0 820 560" preserveAspectRatio="xMidYMid slice"
      aria-hidden="true">
      {candles.map((c, i) => {
        const col = c.up ? 'var(--signal-up, #3a63f0)' : 'var(--signal-down, #e5556b)';
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
  const [view, setView] = useState<View>('login');
  const [dial, setDial] = useState('971');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [code, setCode] = useState('');
  const [first, setFirst] = useState('');
  const [last, setLast] = useState('');
  const [age, setAge] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const fullPhone = () => (dial + phone).replace(/\D/g, '');
  const fail = (e: unknown) => {
    setError((e as Error)?.message || 'Something went wrong. Try again.');
    setBusy(false);
  };
  const go = (v: View) => { setView(v); setError(''); setNotice(''); };
  const clearAuth = () => { setPassword(''); setCode(''); setShowPw(false); };

  const login = async () => {
    if (busy || !phone.trim() || !password) return;
    setBusy(true); setError('');
    try { await api2.login(fullPhone(), password); window.location.href = '/'; }
    catch (e) { fail(e); }
  };

  const sendSignupOtp = async () => {
    if (busy || !first.trim() || !last.trim() || !phone.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api2.otpRequest(fullPhone());
      setNotice('We sent a code to your WhatsApp.');
      setView('signupVerify'); setBusy(false);
    } catch (e) { fail(e); }
  };

  const completeSignup = async () => {
    if (busy || !code.trim() || !password) return;
    setBusy(true); setError('');
    try {
      await api2.register(fullPhone(), code.trim(), `${first.trim()} ${last.trim()}`.trim(),
        password, age.trim());
      window.location.href = '/';
    } catch (e) { fail(e); }
  };

  const sendForgotOtp = async () => {
    if (busy || !phone.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api2.resetRequest(fullPhone());
      setNotice('We sent a reset code to your WhatsApp.');
      setView('forgotVerify'); setBusy(false);
    } catch (e) { fail(e); }
  };

  const completeForgot = async () => {
    if (busy || !code.trim() || !password) return;
    setBusy(true); setError('');
    try {
      await api2.resetPassword(fullPhone(), code.trim(), password);
      window.location.href = '/';
    } catch (e) { fail(e); }
  };

  const tryDemo = () => { window.location.href = '/dashboard?demo=1'; };

  const dialSelect = (
    <select className="lp-dial" value={dial}
      onChange={(e) => { setDial(e.target.value); setError(''); }} aria-label="Country code">
      {COUNTRIES.map((c) => (
        <option key={c.dial + c.name} value={c.dial}>{c.flag} +{c.dial}</option>
      ))}
    </select>
  );
  const phoneRow = (onEnter: () => void) => (
    <div className="lp-phone">
      {dialSelect}
      <input className="lp-input lp-phone-num" value={phone} inputMode="tel"
        placeholder="Phone number" autoComplete="tel-national"
        onChange={(e) => { setPhone(e.target.value); setError(''); }}
        onKeyDown={(e) => { if (e.key === 'Enter') onEnter(); }} />
    </div>
  );
  const pwField = (onEnter: () => void, ph = 'Password', ac = 'current-password') => (
    <div className="lp-pw">
      <input className="lp-input" type={showPw ? 'text' : 'password'} value={password}
        placeholder={ph} autoComplete={ac}
        onChange={(e) => { setPassword(e.target.value); setError(''); }}
        onKeyDown={(e) => { if (e.key === 'Enter') onEnter(); }} />
      <button className="lp-eye" type="button" onClick={() => setShowPw((v) => !v)}
        aria-label={showPw ? 'Hide password' : 'Show password'}>
        {showPw ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </div>
  );

  const isSignup = view !== 'login';

  return (
    <div className="lp-shell">
      <CandleBackdrop />
      <div className="lp-card">
        {/* Log In / Sign Up toggle */}
        <div className="lp-toggle">
          <button className={!isSignup ? 'on' : ''}
            onClick={() => { clearAuth(); go('login'); }}>Log In</button>
          <button className={isSignup ? 'on' : ''}
            onClick={() => { clearAuth(); go('signup'); }}>Sign Up</button>
        </div>

        {view === 'login' && (
          <>
            <h1 className="lp-title">Welcome back</h1>
            <p className="lp-sub">Log in with your phone number and password.</p>

            <label className="lp-label">Phone number</label>
            {phoneRow(login)}

            <label className="lp-label">Password</label>
            {pwField(login)}
            <button className="lp-forgot" onClick={() => { clearAuth(); go('forgot'); }}>
              Forgot password?
            </button>

            {error && <div className="lp-error">{error}</div>}
            <button className="lp-btn" disabled={busy || !phone.trim() || !password} onClick={login}>
              {busy ? <Loader2 size={16} className="lp-spin" /> : <Lock size={16} />}
              {busy ? 'Logging in…' : 'Log In'}
            </button>
          </>
        )}

        {view === 'signup' && (
          <>
            <h1 className="lp-title">Create your account</h1>
            <p className="lp-sub">Tell us a bit about you, then verify your phone number.</p>

            <div className="lp-2col">
              <div>
                <label className="lp-label">First name</label>
                <input className="lp-input" value={first} placeholder="Alex" autoFocus
                  onChange={(e) => { setFirst(e.target.value); setError(''); }} />
              </div>
              <div>
                <label className="lp-label">Last name</label>
                <input className="lp-input" value={last} placeholder="Trader"
                  onChange={(e) => { setLast(e.target.value); setError(''); }} />
              </div>
            </div>

            <label className="lp-label">Age</label>
            <input className="lp-input" value={age} inputMode="numeric" placeholder="28"
              onChange={(e) => { setAge(e.target.value.replace(/\D/g, '').slice(0, 3)); setError(''); }} />

            <label className="lp-label">Phone number</label>
            {phoneRow(sendSignupOtp)}

            {error && <div className="lp-error">{error}</div>}
            <button className="lp-btn"
              disabled={busy || !first.trim() || !last.trim() || !phone.trim()}
              onClick={sendSignupOtp}>
              {busy ? <Loader2 size={16} className="lp-spin" /> : <Phone size={16} />}
              {busy ? 'Sending…' : 'Send OTP'}
            </button>
          </>
        )}

        {view === 'signupVerify' && (
          <>
            <h1 className="lp-title">Verify your number</h1>
            <p className="lp-sub">Enter the code we sent and set a password.</p>
            {notice && <div className="lp-notice">{notice}</div>}

            <label className="lp-label">Verification code</label>
            <input className="lp-input" value={code} inputMode="numeric" autoFocus
              placeholder="6-digit code"
              onChange={(e) => { setCode(e.target.value); setError(''); }} />

            <label className="lp-label">Set a password</label>
            {pwField(completeSignup, 'At least 6 characters', 'new-password')}

            {error && <div className="lp-error">{error}</div>}
            <button className="lp-btn" disabled={busy || !code.trim() || !password}
              onClick={completeSignup}>
              {busy ? <Loader2 size={16} className="lp-spin" /> : <Lock size={16} />}
              {busy ? 'Creating…' : 'Create account'}
            </button>
            <button className="lp-back" onClick={() => go('signup')}>Use a different number</button>
          </>
        )}

        {view === 'forgot' && (
          <>
            <h1 className="lp-title">Reset password</h1>
            <p className="lp-sub">Enter your number — we’ll send a reset code to WhatsApp.</p>

            <label className="lp-label">Phone number</label>
            {phoneRow(sendForgotOtp)}

            {error && <div className="lp-error">{error}</div>}
            <button className="lp-btn" disabled={busy || !phone.trim()} onClick={sendForgotOtp}>
              {busy ? <Loader2 size={16} className="lp-spin" /> : <Phone size={16} />}
              {busy ? 'Sending…' : 'Send reset code'}
            </button>
            <button className="lp-back" onClick={() => { clearAuth(); go('login'); }}>
              Back to log in
            </button>
          </>
        )}

        {view === 'forgotVerify' && (
          <>
            <h1 className="lp-title">Set a new password</h1>
            <p className="lp-sub">Enter the code and choose a new password.</p>
            {notice && <div className="lp-notice">{notice}</div>}

            <label className="lp-label">Verification code</label>
            <input className="lp-input" value={code} inputMode="numeric" autoFocus
              placeholder="6-digit code"
              onChange={(e) => { setCode(e.target.value); setError(''); }} />

            <label className="lp-label">New password</label>
            {pwField(completeForgot, 'At least 6 characters', 'new-password')}

            {error && <div className="lp-error">{error}</div>}
            <button className="lp-btn" disabled={busy || !code.trim() || !password}
              onClick={completeForgot}>
              {busy ? <Loader2 size={16} className="lp-spin" /> : <Lock size={16} />}
              {busy ? 'Saving…' : 'Reset password'}
            </button>
            <button className="lp-back" onClick={() => { clearAuth(); go('login'); }}>
              Back to log in
            </button>
          </>
        )}

        <div className="lp-or"><span>OR</span></div>
        <button className="lp-demo" onClick={tryDemo}>
          <PlayCircle size={16} /> Try the demo
        </button>
        <div className="lp-fineprint">
          Look around without signing up. Shared account — don’t save anything private to it.
        </div>
      </div>
    </div>
  );
}
