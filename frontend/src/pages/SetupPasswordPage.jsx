import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { request } from '../api/client';

/** Public page opened from a Superadmin-issued one-time link. The token is verified by the server; nothing is stored here. */
export default function SetupPasswordPage({ customer = false }) {
  const [params] = useSearchParams();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (customer && password !== confirm) throw new Error('The two passwords do not match.');
      await request(customer ? '/integrations/public/customer-setup/' : '/auth/setup-password/', { method: 'POST', skipExpiry: true, body: customer ? { token: params.get('token'), password } : { uid: params.get('uid'), token: params.get('token'), password, confirm_password: confirm } });
      setDone(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main style={{ maxWidth: 380, margin: '10vh auto', padding: 20, display: 'grid', gap: 12 }}>
      <h1 style={{ fontSize: 20, color: '#12305a' }}>Set your Nexus password</h1>
      {done ? (
        <p role="status">Password set. <Link to="/login">Sign in</Link></p>
      ) : (
        <form onSubmit={submit} style={{ display: 'grid', gap: 10 }}>
          <label>New password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="new-password" style={{ width: '100%', padding: 8 }} /></label>
          <label>Confirm password<input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" style={{ width: '100%', padding: 8 }} /></label>
          {error && <p role="alert" style={{ color: '#b3261e', margin: 0 }}>{error}</p>}
          <button type="submit" disabled={busy} style={{ padding: 10, border: 0, borderRadius: 6, background: '#12305a', color: '#fff', fontWeight: 600 }}>{busy ? 'Saving…' : 'Set password'}</button>
        </form>
      )}
    </main>
  );
}
