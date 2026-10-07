import { useState } from 'react';
import { LogIn, Loader2 } from 'lucide-react';
import { api2 } from '../api/client';
import { BrandLockup } from '../components/Brand';
import './login-page.css';

// A minimal in-app sign-in form. It POSTs to /auth/login (proxied to the backend
// in dev) and, on success, does a full navigation to the app so every panel
// re-fetches with the fresh session cookie. In production the server serves its
// own login page for unauthenticated routes; this is mainly for local dev.
export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (busy || !password) return;
    setBusy(true); setError('');
    try {
      await api2.login(username.trim(), password);
      // Full load so the whole app picks up the new session.
      window.location.href = '/';
    } catch (e) {
      setError((e as Error)?.message || 'Sign in failed. Check your password.');
      setBusy(false);
    }
  };

  return (
    <div className="lp-wrap">
      <div className="lp-card">
        <div className="lp-brand"><BrandLockup /></div>
        <h1 className="lp-title">Sign in</h1>
        <p className="lp-sub">Enter your credentials to access Trdgo.us.</p>

        <label className="lp-label">Username</label>
        <input
          className="lp-input"
          value={username}
          autoFocus
          placeholder="admin"
          autoComplete="username"
          onChange={(e) => { setUsername(e.target.value); setError(''); }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
        />

        <label className="lp-label">Password</label>
        <input
          className="lp-input"
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(e) => { setPassword(e.target.value); setError(''); }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
        />

        {error && <div className="lp-error">{error}</div>}

        <button className="lp-btn" disabled={busy || !password} onClick={submit}>
          {busy ? <Loader2 size={16} className="lp-spin" /> : <LogIn size={16} />}
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </div>
    </div>
  );
}
