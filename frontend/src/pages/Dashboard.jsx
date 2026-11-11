import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { dashboardApi } from '../api/dashboard';
import Alert from '../components/Alert';
import ProjectUsageCard from '../components/ProjectUsageCard';
import { useAuth } from '../context/AuthContext';
import { monthKey } from '../utils/date';
import useSyncChanges from '../utils/useSyncChanges';
import styles from './Dashboard.module.css';

/**
 * Super Admin dashboard: usage counts per project. Clicking a count opens its report with the project and count already
 * applied: Used / Not Used → Usage Report, Expiring This Month → License Expiry Report (current month).
 */
export default function Dashboard() {
  const { user, can } = useAuth();
  const navigate = useNavigate();
  const isSuperAdmin = can('view', 'gstr2tally:dashboard') || can('view', 'prn-auto-print:dashboard');
  const [state, setState] = useState({ loading: true, error: '', projects: [] });
  const synced = useSyncChanges(dashboardApi.sync, isSuperAdmin);

  useEffect(() => {
    if (!isSuperAdmin) return undefined;
    let current = true;
    dashboardApi
      .projectUsage()
      .then(({ projects }) => current && setState({ loading: false, error: '', projects }))
      .catch((err) => current && setState((s) => (s.projects.length ? s : { loading: false, error: err.message, projects: [] })));
    return () => {
      current = false;
    };
  }, [isSuperAdmin, synced, can]);

  if (!isSuperAdmin) return <section className={styles.canvas} aria-label="Dashboard"><p className={styles.note}>No Dashboard access is assigned. Choose an authorized menu or contact your Superadmin.</p></section>;

  const openReport = (project, category) => {
    const query = ['expiring', 'expired'].includes(category) ? { product: project, ...(category === 'expiring' ? { month: monthKey() } : { state: 'expired' }) } : { project, status: category };
    navigate(`${['expiring', 'expired'].includes(category) ? '/reports/license-expiry' : '/reports/usage'}?${new URLSearchParams(query)}`);
  };

  return (
    <section className={styles.canvas} aria-label="Dashboard">
      {state.error && <Alert type="error">{state.error}</Alert>}
      {state.loading && <p className={styles.note}>Loading…</p>}

      {!state.loading && !state.error && (
        <div className={styles.cards}>
          {state.projects.filter((project) => can('view', `${project.code}:dashboard`)).map((project) => (
            <ProjectUsageCard key={project.code} project={project} selected={null} onSelect={openReport} canOpen={(category) => can('reports', `${project.code}:${['expiring', 'expired'].includes(category) ? 'expiry' : 'usage'}`)} />
          ))}
        </div>
      )}
    </section>
  );
}
