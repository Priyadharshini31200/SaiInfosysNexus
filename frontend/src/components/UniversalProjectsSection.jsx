import { useEffect, useState } from 'react';
import { integrationsApi } from '../api/integrations';
import { useAuth } from '../context/AuthContext';
import styles from '../pages/integrations/Integrations.module.css';

const OVERALL = [
  ['total_unique_customers', 'Total Unique Customers'], ['total_licenses', 'Total Licenses'], ['used', 'Used'], ['not_used', 'Not Used'],
  ['expired', 'Expired'], ['expiring_this_month', 'This Month Expiring'], ['active_devices', 'Active Devices'],
];
const COLS = [['total_customers', 'Total Customers'], ['used', 'Used'], ['not_used', 'Not Used'], ['expired', 'Expire'], ['speed_users', 'Speed Users'], ['normal_users', 'Normal Users'], ['new_users', 'New Users'], ['one_time_users', 'One-Time Users']];

/** Projects connected through the Universal Licensing API, read from the Project Registry (nothing is hardcoded per project).
 *  Rendered below the existing PRN / GSTR dashboard, which it does not touch. Super Admin only; hidden when none are connected. */
export default function UniversalProjectsSection() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  useEffect(() => {
    if (user?.role !== 'SUPERADMIN') return undefined;
    let live = true;
    const load = () => integrationsApi.dashboard().then((d) => live && setData(d), () => {});
    load();
    const t = setInterval(load, 60000);
    return () => { live = false; clearInterval(t); };
  }, [user?.role]);
  if (!data || !data.projects.length) return null;
  return (
    <div className={styles.panel} aria-label="Connected projects">
      <h3 className={styles.title}>Connected Projects (Universal API)</h3>
      <div className={styles.tiles}>{OVERALL.map(([k, l]) => <div key={k} className={styles.tile}><span>{l}</span><span>{data.overall[k].toLocaleString('en-IN')}</span></div>)}</div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead><tr><th>Project Name</th>{COLS.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
          <tbody>{data.projects.map((p) => <tr key={p.code}><td>{p.name}</td>{COLS.map(([k]) => <td key={k}>{p[k]}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}
