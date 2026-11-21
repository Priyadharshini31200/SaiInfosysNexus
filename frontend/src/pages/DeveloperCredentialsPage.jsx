import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { API_BASE } from '../api/client';
import styles from './integrations/Integrations.module.css';

/** Public one-time page for an external developer. ONE short-lived, single-use link: clicking Show Credentials (a POST, so mail-scanner link previews
 *  cannot consume it) returns the project's Client ID and a NEW API secret, shown once. The secret lives only in this page's memory. */
export default function DeveloperCredentialsPage() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [creds, setCreds] = useState(null);
  const [copied, setCopied] = useState('');

  const redeem = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try {
      const res = await fetch(`${API_BASE}/integrations/developer-credentials/redeem/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'omit', cache: 'no-store', body: JSON.stringify({ token }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) throw new Error(body?.error?.message || 'The credentials could not be retrieved.');
      setCreds(body.data);
      window.history.replaceState(null, '', window.location.pathname); // drop the used token from the address bar / history
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const copy = async (label, value) => {
    try { await navigator.clipboard.writeText(value); setCopied(`${label} copied successfully.`); } catch { setCopied(`Clipboard unavailable - select the ${label} and press Ctrl+C.`); }
  };

  return (
    <main style={{ maxWidth: 560, margin: '48px auto', padding: '0 16px' }}>
      <div className={styles.panel}>
        <h1 className={styles.title} style={{ marginTop: 0 }}>Nexus API Credentials</h1>
        {!token && !creds && <p role="alert" className={styles.err}>This link is incomplete. Use the full link from the Nexus email.</p>}
        {token && !creds && (
          <form onSubmit={redeem}>
            <p className={styles.note}>Click <b>Show Credentials</b> to receive your project&apos;s Client ID and API Secret. This issues a <b>new API secret</b> for your Client ID; any earlier secret stops working. The link works once.</p>
            {error && <p role="alert" className={styles.err}>{error}</p>}
            <div className={styles.stepFooter}><span /><span /><button type="submit" className={styles.btn} disabled={busy}>{busy ? 'Issuing…' : 'Show Credentials'}</button></div>
          </form>
        )}
        {creds && (
          <>
            <p className={styles.logErr}>Shown <b>once</b>. Store both values in your backend&apos;s secret store now; Nexus cannot show the secret again.</p>
            <p className={styles.note}>Project: <b>{creds.project_name} ({creds.project_code})</b></p>
            {[['Client ID', creds.client_id], ['API Secret', creds.api_secret]].map(([l, v]) => (
              <div key={l} className={styles.field} style={{ marginBottom: 10 }}><span className={styles.fieldLabel}>{l}</span>
                <div className={styles.secretRow}><input readOnly value={v} className={styles.mono} aria-label={l} onFocus={(e) => e.target.select()} /><button type="button" className={styles.btn} onClick={() => copy(l, v)}>Copy</button></div>
              </div>
            ))}
            {copied && <p role="status" className={styles.note}>{copied}</p>}
          </>
        )}
      </div>
    </main>
  );
}
