import { useEffect, useState } from 'react';
import DashboardVisibilityGear from '../../components/DashboardVisibilityGear';
import gearStyles from '../../components/DashboardVisibilityGear.module.css';
import { useNavigate, useParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import styles from '../tally/Tally.module.css';

const USAGE = [['speed_users', 'Speed Users', 'speed'], ['normal_users', 'Normal Users', 'normal'], ['new_users', 'New Users', 'new'], ['one_time_users', 'One-Time Users', 'one_time']];
const METRICS = [
  ['total_customers', 'Total Customers', null], ['used', 'Used', 'used'], ['not_used', 'Not Used', 'not_used'], ['expired', 'Expired', 'expired'],
  ['expiring_this_month', 'This Month Expiring', 'expiring'], ['trial_licenses', 'Trial Licenses', 'trial'], ['annual_licenses', '1 Year Licenses', 'annual'],
  ['lifetime_licenses', 'Lifetime Licenses', 'lifetime'], ['active_devices', 'Active Devices', 'active_devices'],
];

/** Mothys Dashboard: same structure as the GSTR2Tally dashboard. Every number is computed by the server from real records;
 *  clicking one opens the Customers list with exactly that filter. */
export default function ProductDashboardPage() {
  const { code } = useParams();
  const navigate = useNavigate();
  const [state, setState] = useState({ loading: true, error: '', data: null });
  useEffect(() => {
    let current = true;
    setState({ loading: true, error: '', data: null });
    integrationsApi.productDashboard(code).then((data) => current && setState({ loading: false, error: '', data }), (e) => current && setState({ loading: false, error: e.message, data: null }));
    return () => { current = false; };
  }, [code]);
  const { loading, error, data } = state;
  if (loading) return <p className={styles.muted}>Loading…</p>;
  if (error) return <p role="alert" className={styles.emptyError}>{error}</p>;
  const go = (qs) => navigate(`/product/${code}/customers?${qs}`);
  return (
    <div className={styles.page}>
      <div className={gearStyles.bar}><DashboardVisibilityGear code={code} /></div>
      <div className={styles.cards} role="list" aria-label="Customers by usage category">
        {USAGE.map(([key, label, usage]) => (
          <button key={key} type="button" role="listitem" className={styles.card} onClick={() => go(`usage=${usage}`)}>
            <span className={styles.cardLabel}>{label}</span><span className={styles.cardCount}>{data[key]}</span><span className={styles.cardLink}>View customers</span>
          </button>
        ))}
      </div>
      <section className={styles.summary} aria-label="License Summary">
        <div className={styles.metrics}>
          {METRICS.map(([key, label, filter]) => (filter ? (
            <button key={key} type="button" className={`${styles.metric} ${styles.metricBtn}`} onClick={() => go(`filter=${filter}`)} aria-label={`${label}, ${data[key]}. View list`}><span>{label}</span><strong>{data[key]}</strong></button>
          ) : (
            <button key={key} type="button" className={`${styles.metric} ${styles.metricBtn}`} onClick={() => go('')} aria-label={`${label}, ${data[key]}. View list`}><span>{label}</span><strong>{data[key]}</strong></button>
          )))}
        </div>
        {data.pending_approval > 0 && <p className={styles.muted}>{data.pending_approval} customer(s) are waiting for licence approval.</p>}
      </section>
    </div>
  );
}
