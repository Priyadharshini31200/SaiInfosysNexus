import { useEffect, useState } from 'react';
import { Link, Outlet, useLocation, useParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import styles from '../integrations/Integrations.module.css';

/** UNIVERSE / Projects / <Project>: the shared project workspace with exactly two tabs, Dashboard | Customers. The sidebar never changes with the project.
 *  Unknown project -> "not found", revoked access -> "forbidden"; no stale data is rendered. */
export default function ProjectWorkspacePage() {
  const { code } = useParams();
  const { state: navigation } = useLocation();
  const [state, setState] = useState({ loading: true, error: null, data: null });
  useEffect(() => {
    let current = true;
    setState({ loading: true, error: null, data: null });
    integrationsApi.universeProject(code).then((data) => current && setState({ loading: false, error: null, data }), (e) => current && setState({ loading: false, error: e, data: null }));
    return () => { current = false; };
  }, [code]);
  const { loading, error, data } = state;
  if (loading) return <p>Loading…</p>;
  if (error) {
    const forbidden = error.status === 403;
    return (
      <div className={styles.panel} role="alert">
        <h2 className={styles.title}>{forbidden ? 'Access denied' : 'Project not found'}</h2>
        <p className={styles.note}>{forbidden ? 'You do not have access to this project.' : 'This project does not exist or is no longer available.'}</p>
        <Link className={styles.btn} to={navigation?.from || '/universe/projects'}>Back to Projects</Link>
      </div>
    );
  }
  // Direct navigation: no intermediate shell, tabs or Configure API step - the Dashboard / Customers page renders immediately (Configure API lives in UNIVERSE -> API Integration).
  return <Outlet context={data.can} />;
}
