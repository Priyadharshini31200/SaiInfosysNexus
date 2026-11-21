import { useEffect, useState } from 'react';
import DashboardVisibilityGear from '../../components/DashboardVisibilityGear';
import { useNavigate, useOutletContext, useParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import { formatDateTime } from '../../utils/date';
import styles from '../tally/Tally.module.css';
import compact from '../ProjectDirectory.module.css';
import ProjectActions from './ProjectActions';

/** UNIVERSE project dashboard: the same GSTR-style pattern for every registered project (one dynamic page). Counts come from real, project-scoped activity data;
 *  where the project has sent no telemetry the category shows "Data Unavailable" with the reason instead of a misleading 0. */
export default function UniverseDashboardPage() {
  const { code } = useParams();
  const navigate = useNavigate();
  const grants = useOutletContext();
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const [showTransfers, setShowTransfers] = useState(false);
  useEffect(() => {
    let current = true;
    setState({ loading: true, error: '', data: null });
    integrationsApi.universeDashboard(code).then((data) => current && setState({ loading: false, error: '', data }), (e) => current && setState({ loading: false, error: e.message, data: null }));
    return () => { current = false; };
  }, [code, revision]);
  const { loading, error, data } = state;
  if (loading) return <p className={styles.muted}>Loading…</p>;
  if (error) return <p role="alert" className={styles.emptyError}>{error}</p>;
  const s = data.stats;
  const go = (usage) => navigate(`/universe/projects/${code}/customers?usage=${usage}`);
  return (
    <div className={`${styles.page} ${compact.dashboard}`}>
      <header className={compact.dashboardHeader}>
        <h2>{data.project.name}</h2><span>{data.project.code}</span>
        <strong>{{ ACTIVE: 'Active', DISABLED: 'Disabled', ARCHIVED: 'Archived', SETUP_PENDING: 'Setup Pending' }[data.project.status]}</strong>
        <ProjectActions project={data.project} grants={{ edit: grants?.edit, configure: grants?.configure, archive: grants?.archive }}
          label="Manage Project" state={{ from: `/universe/projects/${code}/dashboard` }} onChange={() => setRevision((v) => v + 1)} />
        <DashboardVisibilityGear code={data.project.code} />
      </header>
      <div className={styles.cards} role="list" aria-label="Customers by usage category">
        {data.cards.map((c) => (
          <button key={c.key} type="button" role="listitem" className={styles.card} onClick={() => go(c.key)} title={c.available ? undefined : data.telemetry.reason}>
            <span className={styles.cardLabel}>{c.label}</span>
            <span className={styles.cardCount} style={c.available ? undefined : { fontSize: 15 }}>{c.available ? c.count : 'Data Unavailable'}</span>
            <span className={styles.cardLink}>View customers</span>
          </button>
        ))}
      </div>
      {!data.telemetry.available && <p className={styles.muted} role="status">Data Unavailable: {data.telemetry.reason} New User uses the registration date and is always available.</p>}
      <section className={styles.summary} aria-label="Registration Summary">
        <div className={styles.metrics}>
          <span className={styles.metric} title={data.definitions.total_customers}><span>Total Customers</span><strong>{s.total_customers}</strong></span>
          <span className={styles.metric}><span>Used</span><strong>{s.used ?? '—'}</strong></span>
          <span className={styles.metric}><span>Not Used</span><strong>{s.not_used ?? '—'}</strong></span>
          <span className={styles.metric}><span>Expired Licences</span><strong>{s.expired}</strong></span>
          <span className={styles.metric}><span>Last Used</span><strong>{s.last_used ? formatDateTime(s.last_used) : '—'}</strong></span>
          <span className={styles.metric} title={data.definitions.last_7_days}><span>Last 7 Days</span><strong>{s.last_7_days}</strong></span>
          <button type="button" className={`${styles.metric} ${styles.metricBtn}`} title={data.definitions.pending_activation} onClick={() => navigate(`/universe/projects/${code}/customers?status=Pending+Review`)}><span>Pending Activation</span><strong>{s.pending_activation}</strong></button>
          <button type="button" className={`${styles.metric} ${styles.metricBtn}`} aria-expanded={showTransfers} title={data.definitions.recent_device_transfers} onClick={() => setShowTransfers((v) => !v)}><span>Recent Device Transfers</span><strong>{s.recent_device_transfers}</strong></button>
        </div>
      </section>
      {showTransfers && (
        <section className={styles.summary} aria-label="Recent device transfers">
          {data.recent_transfers.length === 0 ? <p className={styles.muted}>No device transfers in the last 7 days.</p> : (
            <table style={{ width: '100%', fontSize: 13 }}><thead><tr><th align="left">When</th><th align="left">Customer</th><th align="left">From</th><th align="left">To</th></tr></thead>
              <tbody>{data.recent_transfers.map((t, i) => <tr key={i}><td>{formatDateTime(t.at)}</td><td>{t.customer}</td><td>{t.from_device || '—'}</td><td>{t.to_device || '—'}</td></tr>)}</tbody></table>
          )}
        </section>
      )}
      <details className={styles.muted}>
        <summary>How usage categories are counted</summary>
        <p>New customers registered within {data.rules.new_days} days. Speed users were active on at least {data.rules.speed_min_days} days in the last 30 days. One-time users were active on a single day. Normal users are other customers with activity.</p>
      </details>
    </div>
  );
}
