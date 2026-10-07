import { useState } from 'react';
import { Loader2, Phone, Eye, EyeOff, PlayCircle, Lock } from 'lucide-react';
import { api2 } from '../api/client';
import CountrySelect from './CountrySelect';

// The Log In / Sign Up card (phone-OTP). `view` is controlled by the parent so
// the landing hero's "Join Now" can jump straight to Sign Up. All other field
// state lives here. No Google login; captcha to be added later.
export type AuthView = 'login' | 'signup' | 'signupVerify' | 'forgot' | 'forgotVerify';

export default function AuthCard({ view, setView }:
  { view: AuthView; setView: (v: AuthView) => void }) {
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
  const go = (v: AuthView) => { setView(v); setError(''); setNotice(''); };
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
      setNotice('We sent a code to your WhatsApp.'); go('signupVerify'); setBusy(false);
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
      setNotice('We sent a reset code to your WhatsApp.'); go('forgotVerify'); setBusy(false);
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

  const phoneRow = (onEnter: () => void) => (
    <div className="lp-phone">
      <CountrySelect dial={dial} setDial={(d) => { setDial(d); setError(''); }} />
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
    <div className="lp-card">
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
              <input className="lp-input" value={first} placeholder="Alex"
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
  );
}
