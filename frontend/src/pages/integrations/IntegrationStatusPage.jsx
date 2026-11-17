import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import { formatDateTime } from '../../utils/date';
import { statusBadge } from './ProjectListPage';
import styles from './Integrations.module.css';

/** Project Management → API Integration Status: verification progress, last sync and the Failed Sync Report. */
export default function IntegrationStatusPage() {
  const [projects, setProjects] = useState([]);
  const [failed, setFailed] = useState({ failed_syncs: [], failed_emails: [] });
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const load = useCallback(async () => {
    try {
      const [p, f] = await Promise.all([integrationsApi.projects(), integrationsApi.failedSync()]);
      setProjects(p.projects); setFailed(f);
    } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const retry = async (id) => {
    setError(''); setInfo('');
    try { await integrationsApi.retrySync(id); setInfo('Sync retried successfully.'); } catch (e) { setError(e.message); }
    load();
  };
  return (
    <section className={styles.page} aria-label="API Integration Status">
      {error && <Alert type="error">{error}</Alert>}
      {info && <Alert type="success">{info}</Alert>}
      <div className={styles.panel}>
        <h2 className={styles.title}>API Integration Status</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr><th>Project</th><th>Status</th><th>Verified steps</th><th>Last Sync</th><th /></tr></thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.code}>
                  <td>{p.name}</td><td>{statusBadge(p)}</td>
                  <td><div className={styles.steps}>{p.required_steps.map((s) => <span key={s} className={`${styles.badge} ${p.verified_steps.includes(s) ? styles.ok : styles.pending}`}>{s.replace('_', ' ')}</span>)}</div></td>
                  <td>{p.last_sync_status ? `${p.last_sync_status} · ${formatDateTime(p.last_sync_at)}` : '—'}</td>
                  <td><Link to={`/project-management/${p.code}/integration`}>Open</Link></td>
                </tr>
              ))}
              {!projects.length && <tr><td colSpan={5}>No projects connected yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <div className={styles.panel}>
        <h3 className={styles.title}>Failed Sync Report</h3>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr><th>When</th><th>Project</th><th>External ID</th><th>Event</th><th>Error</th><th>Retries</th><th /></tr></thead>
            <tbody>
              {failed.failed_syncs.map((f) => (
                <tr key={f.id}><td>{formatDateTime(f.at)}</td><td>{f.project}</td><td>{f.external_customer_id || '—'}</td><td>{f.event}</td><td>{f.error_code}: {f.error}</td><td>{f.retries}</td>
                  <td><button className={`${styles.btn} ${styles.ghost}`} onClick={() => retry(f.id)}>Retry</button></td></tr>
              ))}
              {!failed.failed_syncs.length && <tr><td colSpan={7}>No failed syncs.</td></tr>}
            </tbody>
          </table>
        </div>
        {failed.failed_emails.length > 0 && <p className={styles.note}>{failed.failed_emails.length} welcome email(s) failed – open the project and use “Resend credentials”: {failed.failed_emails.map((e) => `${e.project}/${e.user_id}`).join(', ')}</p>}
      </div>
    </section>
  );
}
