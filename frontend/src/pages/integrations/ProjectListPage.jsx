import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import { formatDateTime } from '../../utils/date';
import styles from './Integrations.module.css';

export const statusBadge = (p) =>
  !p.enabled ? <span className={`${styles.badge} ${styles.off}`}>Disabled</span>
    : p.status === 'ACTIVE' ? <span className={`${styles.badge} ${styles.ok}`}>Verified</span>
      : <span className={`${styles.badge} ${styles.pending}`}>Setup Pending</span>;

/** Project Management → Project List: every project connected through the Universal Licensing API. */
export default function ProjectListPage() {
  const [state, setState] = useState({ loading: true, projects: [], error: '' });
  useEffect(() => {
    integrationsApi.projects().then((d) => setState({ loading: false, projects: d.projects, error: '' }), (e) => setState({ loading: false, projects: [], error: e.message }));
  }, []);
  return (
    <section className={styles.page} aria-label="Project List">
      <div className={styles.panel}>
        <div className={styles.head}>
          <div><h2 className={styles.title}>Connected Projects</h2><p className={styles.sub}>Non-Tally products licensed through the Nexus Universal API.</p></div>
          <Link to="/project-management/add" className={styles.btn}>+ Add / Connect Project</Link>
        </div>
        {state.error && <Alert type="error">{state.error}</Alert>}
        {state.loading ? <p className={styles.note} role="status">Loading…</p> : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead><tr><th>Project</th><th>Code</th><th>Framework</th><th>Customers</th><th>Status</th><th>Last Sync</th><th>Created</th><th /></tr></thead>
              <tbody>
                {state.projects.map((p) => (
                  <tr key={p.code}>
                    <td>{p.name}</td><td className={styles.mono}>{p.code}</td><td>{p.framework || '—'}</td><td>{p.customers}</td><td>{statusBadge(p)}</td>
                    <td>{p.last_sync_status ? `${p.last_sync_status} · ${formatDateTime(p.last_sync_at)}` : '—'}</td><td>{formatDateTime(p.created_at)}</td>
                    <td><Link to={`/project-management/${p.code}/integration`}>API Integration</Link></td>
                  </tr>
                ))}
                {!state.projects.length && <tr><td colSpan={8}>No projects connected yet.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
