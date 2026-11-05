import { useEffect, useState } from 'react';
import { registrationsApi } from '../api/registrations';
import { formatDateTime } from '../utils/date';
import styles from './ActivityLog.module.css';

const ACTIONS = {
  REGISTERED: 'Customer added',
  EDITED: 'Edited',
  TRIAL_EXTENDED: 'Trial extended',
  TRIAL_CANCELLED: 'Trial cancelled',
  LICENSE_INACTIVATED: 'License inactivated',
  KEY_GENERATED: 'Activation key generated',
  REQUEST_RECEIVED: 'Sign-up request received',
  REQUEST_PROCESSED: 'Sign-up request processed',
  CUSTOMER_PASSWORD_CHANGED: 'Customer changed password',
  CUSTOMER_PASSWORD_RESET: 'Customer reset password',
};

/**
 * Who did what and when (Super Admin only; the API enforces it). For one customer when `customerId` is given,
 * otherwise the project's recent activity. `refreshKey` reloads it after something changed.
 */
export default function ActivityLog({ project, customerId, refreshKey }) {
  const [state, setState] = useState({ loading: true, error: '', rows: [] });

  useEffect(() => {
    let current = true;
    registrationsApi
      .activity(project, customerId)
      .then(({ results }) => current && setState({ loading: false, error: '', rows: results }))
      .catch((e) => current && setState((s) => ({ loading: false, error: e.message, rows: s.rows })));
    return () => {
      current = false;
    };
  }, [project, customerId, refreshKey]);

  if (state.error) return <p className={styles.note}>{state.error}</p>;
  if (state.loading) return <p className={styles.note}>Loading…</p>;
  if (!state.rows.length) return <p className={styles.note}>No activity recorded yet.</p>;
  return (
    <ul className={styles.list}>
      {state.rows.map((row) => (
        <li key={row.id} className={styles.item}>
          <span className={styles.line}>
            <span className={styles.what}>
              {ACTIONS[row.action] || row.action}
              {!customerId && row.customer ? ` · ${row.customer}` : ''}
            </span>
            <span className={styles.meta}>
              {row.actor}
              {row.actor_role === 'ADMIN' ? ' (Admin)' : ''} · {formatDateTime(row.created_at)}
            </span>
          </span>
          {row.detail && <span className={styles.detail}>{row.detail}</span>}
        </li>
      ))}
    </ul>
  );
}
