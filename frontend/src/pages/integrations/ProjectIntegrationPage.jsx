import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import CopyValue from '../../components/CopyValue';
import { formatDateTime } from '../../utils/date';
import { statusBadge } from './ProjectListPage';
import CustomerSyncPanel from './CustomerSyncPanel';
import { TallyPanel, UsagePanel } from './TallyUsagePanels';
import { useIntegrationPaths } from './paths';
import { useProjectSecret } from './useProjectSecret';
import styles from './Integrations.module.css';

const json = (v) => JSON.stringify(v, null, 2);

/** Project Management → <Project> → API Integration. The API secret is shown once (only right after create / rotate). */
export default function ProjectIntegrationPage() {
  const paths = useIntegrationPaths();
  const { code } = useParams();
  const location = useLocation();
  const [data, setData] = useState(null);
  const [customers, setCustomers] = useState([]);
  const { secret, clear: clearSecret, consume: consumeSecret, rotate: revealRotation } = useProjectSecret(code);
  const [info, setInfo] = useState('');
  const [error, setError] = useState('');
  const [test, setTest] = useState(null);

  const load = useCallback(async () => {
    try {
      const [d, c] = await Promise.all([integrationsApi.project(code), integrationsApi.customers(code)]);
      setData(d); setCustomers(c.customers);
    } catch (e) { setError(e.message); }
  }, [code]);
  useEffect(() => { load(); }, [load]);

  const run = (fn, ok) => async () => {
    setError(''); setInfo('');
    try { const r = await fn(); if (ok) setInfo(typeof ok === 'function' ? ok(r) : ok); await load(); return r; } catch (e) { setError(e.message); return null; }
  };
  const rotate = async () => {
    if (!window.confirm('Rotate credentials? The current secret stops working immediately and the project backend must be updated.')) return;
    await revealRotation(() => run(() => integrationsApi.rotate(code), 'New credentials generated. Copy the secret now – it is shown only once.')());
  };
  const toggle = run(() => integrationsApi.updateProject(code, { enabled: !data.project.enabled }), 'Integration updated.');
  const doTest = async () => { setTest(null); setError(''); try { setTest(await integrationsApi.testConnection(code)); } catch (e) { setError(e.message); } };

  if (!data) return <section className={styles.page}>{error ? <Alert type="error">{error}</Alert> : <p className={styles.note} role="status">Loading…</p>}</section>;
  const p = data.project;
  const base = data.api_base;

  return (
    <section className={styles.page} aria-label="API Integration">
      <div className={styles.panel}>
        <div className={styles.head}>
          <div><h2 className={styles.title}>{p.name} · API Integration</h2><p className={styles.sub}>{statusBadge(p)} &nbsp; API version {data.api_version}</p></div>
          <div className={styles.actions}>
            <button className={`${styles.btn} ${styles.ghost}`} onClick={doTest}>Test Connection</button>
            <button className={`${styles.btn} ${styles.ghost}`} onClick={run(() => integrationsApi.downloadDocs(code, 'markdown'))}>Download API Documentation</button>
            <button className={`${styles.btn} ${styles.ghost}`} onClick={run(() => integrationsApi.downloadDocs(code, 'openapi'))}>Download OpenAPI JSON</button>
            <button className={`${styles.btn} ${styles.ghost}`} onClick={rotate}>Regenerate / Rotate Credentials</button>
            <button className={`${styles.btn} ${p.enabled ? styles.danger : ''}`} onClick={toggle}>{p.enabled ? 'Disable Integration' : 'Enable Integration'}</button>
          </div>
        </div>
        {error && <Alert type="error">{error}</Alert>}
        {info && <Alert type="success">{info}</Alert>}
        {location.state?.fresh && <Alert type="success">Project created in Setup Pending. Give the credentials below to the project's backend team through a secure channel.</Alert>}
        {test && (
          <Alert type={test.credentials_active && test.project_backend_reachable ? 'success' : 'error'}>
            Credentials {test.credentials_active ? 'active' : 'MISSING'} · Project backend {test.project_backend_reachable ? 'reachable' : `not reachable (${test.detail})`} · Integration {test.integration_verified ? 'verified' : `NOT verified – missing: ${test.missing_steps.join(', ')}`}. {test.note}
          </Alert>
        )}
        <dl className={styles.kv}>
          <dt>Nexus API Base URL</dt><dd className={styles.mono}>{base}</dd>
          <dt>Project Code</dt><dd className={styles.mono}>{p.code}</dd>
          <dt>Client ID</dt><dd className={styles.mono}>{p.client_id}</dd>
          <dt>API Secret</dt>
          <dd>{secret ? (
            <div className={styles.secret}><strong>Copy now – this secret is never shown again</strong><CopyValue value={secret} label="API secret" onCopied={consumeSecret} />
              {' '}<button className={`${styles.btn} ${styles.ghost}`} onClick={clearSecret}>Hide</button></div>
          ) : <span className={styles.mono}>{p.secret_hint ? `••••••••${p.secret_hint}` : '—'} (hidden – rotate to get a new one)</span>}</dd>
          <dt>Integration Status</dt>
          <dd><div className={styles.steps}>{p.required_steps.map((s) => <span key={s} className={`${styles.badge} ${p.verified_steps.includes(s) ? styles.ok : styles.pending}`}>{s.replace('_', ' ')}</span>)}</div>
            <span className={styles.note}>Becomes Verified only after the project backend has really made each call; a health check is not proof.</span></dd>
        </dl>
      </div>

      <CustomerSyncPanel code={code} tallyRequired={!!p.tally_serial_required} />
      <TallyPanel code={code} project={p} onChanged={load} />
      <UsagePanel code={code} />

      <div className={styles.panel}>
        <h3 className={styles.title}>Authentication Instructions</h3>
        <p className={styles.note}>Server-to-server only (never from React/browser code). Every request carries the project credentials and <span className={styles.mono}>project_code</span>; calls are rate limited to 300/min.</p>
        <pre className={styles.code}>{`X-Client-Id: ${p.client_id}\nX-Client-Secret: <your API secret>\nContent-Type: application/json\n\nSuccess: {"ok": true, "data": {...}, "request_id": "..."}\nError:   {"ok": false, "error": {"code": "...", "message": "..."}, "request_id": "..."}`}</pre>
      </div>

      <div className={styles.panel}>
        <h3 className={styles.title}>API Endpoints</h3>
        {data.endpoints.map((e) => (
          <details key={e.id} className={styles.endpoint}>
            <summary><span className={styles.method}>{e.method}</span><span className={styles.mono}>{base.replace(/\/$/, '')}/integrations/v1/{e.path}</span> — {e.summary}</summary>
            {e.description && <p className={styles.note}>{e.description}</p>}
            {e.request && <><b>Request</b><pre className={styles.code}>{json(e.request)}</pre></>}
            <b>Response</b><pre className={styles.code}>{json({ ok: true, data: e.response })}</pre>
            {e.conflict && <><b>Transfer confirmation (HTTP 409)</b><pre className={styles.code}>{json({ ok: false, error: { code: 'TRANSFER_CONFIRMATION_REQUIRED', ...e.conflict } })}</pre></>}
          </details>
        ))}
        <h3 className={styles.title} style={{ marginTop: 16 }}>Error Codes</h3>
        <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Code</th><th>HTTP</th><th>Meaning</th></tr></thead>
          <tbody>{data.errors.map((e) => <tr key={e.code}><td className={styles.mono}>{e.code}</td><td>{e.http}</td><td>{e.meaning}</td></tr>)}</tbody></table></div>
      </div>

      <div className={styles.panel}>
        <h3 className={styles.title}>Registered Customers ({customers.length})</h3>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr><th>External ID</th><th>Name</th><th>Email</th><th>Serial / User ID</th><th>Licence</th><th>State</th><th>Expires</th><th>Devices</th><th>Welcome mail</th><th /></tr></thead>
            <tbody>
              {customers.map((c) => (
                <tr key={c.id}>
                  <td>{c.external_customer_id}</td><td>{c.name}</td><td>{c.email}</td><td className={styles.mono}>{c.serial_number}</td><td>{c.license_type}</td><td>{c.state}</td>
                  <td>{c.expires_at ? formatDateTime(c.expires_at) : '—'}</td><td>{c.active_devices}</td><td>{c.welcome_email_status || '—'}</td>
                  <td className={styles.actions}>
                    {c.state === 'PENDING_APPROVAL' && <button className={styles.btn} onClick={run(() => integrationsApi.approve(c.license_id), 'Licence approved.')}>Approve</button>}
                    <button className={`${styles.btn} ${styles.ghost}`} onClick={() => window.confirm('Issue a NEW temporary password and email it to the customer?') && run(() => integrationsApi.resendWelcome(c.id), 'New credentials emailed.')()}>Resend credentials</button>
                  </td>
                </tr>
              ))}
              {!customers.length && <tr><td colSpan={10}>No customers yet – they appear automatically when the project backend calls customers/register.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <p className={styles.note}><Link to={paths.status}>Integration Logs</Link> · <Link to={paths.list}>Project List</Link></p>
    </section>
  );
}
