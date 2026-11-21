import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import ConsolidatedReportsPage from '../pages/ConsolidatedReportsPage';

/** The whole application for a restricted User: one report, nothing else. No dashboard, projects, configuration or other routes. */
export default function ReportViewerShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  if (pathname !== '/reports') return <Navigate to="/reports" replace />;
  return (
    <div style={{ minHeight: '100vh', background: '#f4f8fd' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 18px', background: '#12305a', color: '#fff' }}>
        <strong>Sai Infosys Nexus · Consolidated This Month Expiry Report</strong>
        <span style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 13 }}>
          {user.username}
          <button type="button" onClick={async () => { await logout(); navigate('/login', { replace: true }); }} style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid #9cc8ff', background: 'transparent', color: '#fff', cursor: 'pointer' }}>Logout</button>
        </span>
      </header>
      <main style={{ padding: 18 }}><ConsolidatedReportsPage restricted /></main>
    </div>
  );
}
