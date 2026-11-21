import { useState } from 'react';
import styles from './Integrations.module.css';

const STRATEGIES = [
  ['STATIC_HEADER', 'Static Header'],
  ['LOGIN_TOKEN', 'Login API Token (e.g. JWT)'],
  ['OAUTH2_CLIENT_CREDENTIALS', 'OAuth2 Client Credentials'],
];

/** "Other (Custom Authentication)": only the fields of the chosen strategy are shown. Secrets are never typed here - the backend reads
 *  them from server environment variables (the credential reference), exactly like Bearer / API Key. Values are kept in cfg.auth_config. */
export default function CustomAuthFields({ cfg, up, err, mode = 'CUSTOM' }) {
  const ac = cfg.auth_config || {};
  const set = (patch) => up({ auth_config: { ...ac, ...patch } });
  const [bodyText, setBodyText] = useState(() => JSON.stringify(ac.body || {}, null, 1));
  const [extraText, setExtraText] = useState(() => JSON.stringify(ac.extra_headers || {}, null, 1));
  const [jsonErr, setJsonErr] = useState({});
  const json = (key, text, setText) => {
    setText(text);
    try { const v = JSON.parse(text || '{}'); if (typeof v !== 'object' || Array.isArray(v) || v === null) throw new Error(); set({ [key]: v }); setJsonErr((e) => ({ ...e, [key]: '' })); }
    catch { setJsonErr((e) => ({ ...e, [key]: 'Enter a valid JSON object.' })); }
  };
  const strategy = mode === 'OAUTH2' ? 'OAUTH2_CLIENT_CREDENTIALS' : ac.strategy || '';
  const tokenBased = strategy === 'LOGIN_TOKEN' || strategy === 'OAUTH2_CLIENT_CREDENTIALS';
  const f = (label, input, key, required) => (
    <label className={styles.field}><span className={styles.fieldLabel}>{label}{required && <b className={styles.req} aria-hidden="true">*</b>}</span>{input}{err(key)}</label>
  );
  const box = { border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', margin: 0 };
  if (mode === 'BASIC') {
    return (
      <fieldset className={`${styles.field} ${styles.wide}`} style={box}>
        <legend className={styles.fieldLabel}>Basic Authentication</legend>
        <div className={styles.setupTwo}>
          {f('User Name', <input autoComplete="off" value={ac.username || ''} onChange={(e) => set({ username: e.target.value })} />, 'auth_username', true)}
          {f('Password – server env variable', <input autoComplete="off" value={cfg.credential_ref} onChange={(e) => up({ credential_ref: e.target.value })} placeholder="SERVER_ENV_VARIABLE_NAME" />, 'credential_ref', true)}
        </div>
      </fieldset>
    );
  }
  return (
    <fieldset className={`${styles.field} ${styles.wide}`} style={box}>
      <legend className={styles.fieldLabel}>{mode === 'OAUTH2' ? 'OAuth 2.0 (Client Credentials)' : 'Custom Authentication'}</legend>
      <div className={styles.setupTwo}>
        {mode !== 'OAUTH2' && f('Authentication Type / Description', <input value={ac.description || ''} onChange={(e) => set({ description: e.target.value })} placeholder="e.g. Partner portal JWT login" />, 'auth_description')}
        {mode !== 'OAUTH2' && f('Authentication Strategy', <select value={strategy} onChange={(e) => set({ strategy: e.target.value })}><option value="">Select…</option>{STRATEGIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>, 'auth_strategy', true)}
        {strategy && f('Header Name', <input value={ac.header_name ?? ''} onChange={(e) => set({ header_name: e.target.value })} placeholder={tokenBased ? 'Authorization' : 'X-Auth-Token'} />, 'auth_header_name', true)}
        {strategy && f('Header Value Prefix (optional)', <input value={ac.header_prefix ?? ''} onChange={(e) => set({ header_prefix: e.target.value })} placeholder={tokenBased ? 'Bearer ' : 'none'} />, 'auth_header_prefix')}
        {strategy && f(strategy === 'OAUTH2_CLIENT_CREDENTIALS' ? 'Client Secret – server env variable' : strategy === 'LOGIN_TOKEN' ? 'Secret for ${SECRET} – server env variable' : 'Token / Secret – server env variable',
          <input type="text" autoComplete="off" value={cfg.credential_ref} onChange={(e) => up({ credential_ref: e.target.value })} placeholder="SERVER_ENV_VARIABLE_NAME" />, 'credential_ref', true)}
        {strategy === 'LOGIN_TOKEN' && f('Second value for ${SECRET2} – server env variable (optional)', <input autoComplete="off" value={ac.secondary_ref || ''} onChange={(e) => set({ secondary_ref: e.target.value })} placeholder="e.g. PARTNER_API_USERNAME" />, 'auth_secondary_ref')}
        {tokenBased && f('Authentication Endpoint URL', <input value={ac.token_url || ''} onChange={(e) => set({ token_url: e.target.value })} placeholder="/auth/login or https://auth.example.com/oauth/token" />, 'auth_token_url', true)}
        {strategy === 'LOGIN_TOKEN' && f('HTTP Method', <select value={ac.token_method || 'POST'} onChange={(e) => set({ token_method: e.target.value })}><option>POST</option><option>GET</option></select>, 'auth_token_method')}
        {strategy === 'OAUTH2_CLIENT_CREDENTIALS' && f('Client ID', <input value={ac.client_id || ''} onChange={(e) => set({ client_id: e.target.value })} />, 'auth_client_id', true)}
        {strategy === 'OAUTH2_CLIENT_CREDENTIALS' && f('Scope (optional)', <input value={ac.scope || ''} onChange={(e) => set({ scope: e.target.value })} />, 'auth_scope')}
        {tokenBased && f('Token Response JSON Path', <input value={ac.token_path || ''} onChange={(e) => set({ token_path: e.target.value })} placeholder={strategy === 'LOGIN_TOKEN' ? 'token or data.access_token' : 'access_token'} />, 'auth_token_path')}
        {tokenBased && f('Expires-in JSON Path (optional)', <input value={ac.expires_in_path || ''} onChange={(e) => set({ expires_in_path: e.target.value })} placeholder={strategy === 'LOGIN_TOKEN' ? 'expires_in' : 'expires_in'} />, 'auth_expires_in_path')}
        {tokenBased && f('Token Lifetime if not returned (seconds)', <input type="number" min="60" max="86400" value={ac.token_ttl_seconds ?? 3600} onChange={(e) => set({ token_ttl_seconds: e.target.value })} />, 'auth_token_ttl')}
        {strategy === 'LOGIN_TOKEN' && (
          <label className={`${styles.field} ${styles.wide}`}><span className={styles.fieldLabel}>Login Request Body (JSON) – use ${'{'}SECRET{'}'} / ${'{'}SECRET2{'}'} for secrets, never real values</span>
            <textarea rows={3} value={bodyText} placeholder={'{"username": "${SECRET2}", "password": "${SECRET}"}'} onChange={(e) => json('body', e.target.value, setBodyText)} spellCheck={false} />
            {jsonErr.body ? <span className={styles.err}>{jsonErr.body}</span> : err('auth_body')}</label>
        )}
        {strategy && (
          <label className={`${styles.field} ${styles.wide}`}><span className={styles.fieldLabel}>Additional Headers (JSON, optional, no secrets)</span>
            <textarea rows={2} value={extraText} onChange={(e) => json('extra_headers', e.target.value, setExtraText)} spellCheck={false} />
            {jsonErr.extra_headers ? <span className={styles.err}>{jsonErr.extra_headers}</span> : err('auth_extra_headers')}</label>
        )}
      </div>
      {tokenBased && <p className={styles.note}>Nexus requests the token from its own server, keeps it only until it expires and renews it automatically (also once after an HTTP 401). The token is never shown, logged or sent to the browser.</p>}
    </fieldset>
  );
}
