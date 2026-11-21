import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { integrationsApi, setIntegrationsBase } from '../api/integrations';
import { useAuth } from '../context/AuthContext';
import CustomerSyncPanel from '../pages/integrations/CustomerSyncPanel';
import styles from '../pages/integrations/Integrations.module.css';

/** The whole application for a project developer: only the integration settings of the projects assigned to them. */
export default function DeveloperShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState(null);
  const [code, setCode] = useState('');
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => {
    setIntegrationsBase('/integrations/dev');
    integrationsApi.devProjects().then((d) => { setProjects(d.projects); setCode(d.projects[0]?.code || ''); }, (e) => setError(e.message));
    return () => setIntegrationsBase('/integrations/admin');
  }, []);
  useEffect(() => {
    if (!code) return;
    fetch(`${import.meta.env.BASE_URL.replace(/\/$/, '')}/api/integrations/dev/projects/${code}/logs/`, { credentials: 'same-origin' }).then((r) => r.json()).then((d) => setLogs(d.audit || []), () => {});
  }, [code]);
  return (
    <div style={{ minHeight: '100vh', background: '#f4f8fd' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 18px', background: '#12305a', color: '#fff' }}>
        <strong>Sai Infosys Nexus · Project Integration Workspace</strong>
        <span style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 13 }}>{user.username}
          <button type="button" onClick={async () => { await logout(); navigate('/login', { replace: true }); }} style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid #9cc8ff', background: 'transparent', color: '#fff', cursor: 'pointer' }}>Logout</button>
        </span>
      </header>
      <main style={{ padding: 18, display: 'grid', gap: 14, maxWidth: 1100, margin: '0 auto' }}>
        {error && <p role="alert" className={styles.err}>{error}</p>}
        {projects && projects.length === 0 && <p>No project is assigned to you yet.</p>}
        {projects && projects.length > 0 && (
          <>
            <label className={styles.field} style={{ maxWidth: 320 }}>Assigned project
              <select value={code} onChange={(e) => setCode(e.target.value)}>{projects.map((p) => <option key={p.code} value={p.code}>{p.name} ({p.status})</option>)}</select>
            </label>
            {code && <CustomerSyncPanel key={code} code={code} developer />}
            {logs.length > 0 && (
              <div className={styles.panel}><h3 className={styles.title}>Integration logs</h3>
                <table className={styles.table}><thead><tr><th>When</th><th>Action</th><th>Result</th><th>Detail</th></tr></thead>
                  <tbody>{logs.slice(0, 50).map((l, i) => <tr key={i}><td>{new Date(l.at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })}</td><td>{l.action}</td><td>{l.ok ? 'OK' : 'Failed'}</td><td>{l.detail}</td></tr>)}</tbody></table>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
