import { useState } from 'react';
import { LogIn, Loader2, Phone, ArrowLeft } from 'lucide-react';
import { api2 } from '../api/client';
import { BrandLockup } from '../components/Brand';
import './login-page.css';

// Phone-OTP auth. New users: phone -> WhatsApp code -> name + password (created
// with full access, signed in). Returning users: phone + password. Posts go to
// /auth/* (proxied to the backend in dev). On success a full navigation reloads
// the app with the fresh session cookie.
type Mode = 'signin' | 'phone' | 'verify';

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

  // Returning user: phone + password.
  const signIn = async () => {
    if (busy || !phone.trim() || !password) return;
    setBusy(true); setError('');
    try {
      await api2.login(phone.trim(), password);
      window.location.href = '/';
    } catch (e) { fail(e); }
  };

  // New user step 1: request the WhatsApp code.
  const sendCode = async () => {
    if (busy || !phone.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api2.otpRequest(phone.trim());
      setNotice('We sent a code to your WhatsApp. Enter it below.');
      setMode('verify'); setBusy(false);
    } catch (e) { fail(e); }
  };

  // New user step 2: verify code + create the account (and sign in).
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
    <div className="lp-wrap">
      <div className="lp-card">
        <div className="lp-brand"><BrandLockup /></div>

        {mode === 'signin' && (
          <>
            <h1 className="lp-title">Sign in</h1>
            <p className="lp-sub">Enter your phone number and password.</p>

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
            <button className="lp-link" onClick={() => reset('phone')}>
              First time here? Create an account
            </button>
          </>
        )}

        {mode === 'phone' && (
          <>
            <h1 className="lp-title">Create account</h1>
            <p className="lp-sub">We’ll send a verification code to your WhatsApp.</p>

            <label className="lp-label">Phone number</label>
            <input className="lp-input" value={phone} autoFocus inputMode="tel"
              placeholder="e.g. 919876543210 (with country code)"
              onChange={(e) => { setPhone(e.target.value); setError(''); }}
              onKeyDown={(e) => { if (e.key === 'Enter') sendCode(); }} />

            {error && <div className="lp-error">{error}</div>}

            <button className="lp-btn" disabled={busy || !phone.trim()} onClick={sendCode}>
              {busy ? <Loader2 size={16} className="lp-spin" /> : <Phone size={16} />}
              {busy ? 'Sending…' : 'Send code'}
            </button>
            <button className="lp-link" onClick={() => reset('signin')}>
              <ArrowLeft size={13} /> Back to sign in
            </button>
          </>
        )}

        {mode === 'verify' && (
          <>
            <h1 className="lp-title">Verify & finish</h1>
            <p className="lp-sub">Enter the code, your name, and a password to keep.</p>
            {notice && <div className="lp-notice">{notice}</div>}

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
            <button className="lp-link" onClick={() => reset('phone')}>
              <ArrowLeft size={13} /> Use a different number
            </button>
          </>
        )}
      </div>
    </div>
  );
}
