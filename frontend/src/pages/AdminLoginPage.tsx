import { useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { api2 } from '../api/client';
import logoImg from '../assets/logo-dark.png';
import './admin-login.css';

/**
 * Separate admin sign-in, reached at /admin when signed out. The owner logs in
 * with a username + password (the app ACCESS_PASSWORD account), distinct from
 * the phone + OTP flow that regular users use at /login.
 */
export default function AdminLoginPage() {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    if (busy || !username.trim() || !password) return;
    setBusy(true); setErr('');
    try {
      const r = await api2.login(username.trim(), password);
      if (r?.status === 'OK') { window.location.href = '/admin'; return; }
      setErr(r?.detail || 'Incorrect username or password.');
    } catch (e: any) {
      setErr(e?.message || 'Could not sign in.');
    } finally { setBusy(false); }
  };

  return (
    <div className="adl-shell">
      <div className="adl-card">
        <img src={logoImg} alt="Tradgo.us" className="adl-logo" />
        <div className="adl-badge"><ShieldCheck size={13} /> Admin sign-in</div>
        <h1>Admin access</h1>
        <p className="adl-sub">Sign in with your admin username and password.</p>

        <label className="adl-label">Username</label>
        <input className="adl-input" value={username} autoFocus
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()} />

        <label className="adl-label">Password</label>
        <input className="adl-input" type="password" value={password}
          placeholder="••••••••"
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()} />

        {err && <div className="adl-err">{err}</div>}

        <button className="adl-btn" onClick={submit}
          disabled={busy || !username.trim() || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <a className="adl-userlink" href="/login">User sign-in (phone) →</a>
      </div>
    </div>
  );
}
