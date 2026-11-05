import { useState } from 'react';
import { Link } from 'react-router-dom';
import { PROJECT_PAGES } from '../layouts/navigation';
import { formatDate } from '../utils/date';
import styles from './RegistrationRequests.module.css';

/**
 * Sign-ups Pending Approval, sent from the connected project. Nothing is activated until someone opens one in
 * Add Customer (prefilled) and saves it (= approve), or rejects it. Renders nothing when there are none.
 */
export default function RegistrationRequests({ project, requests, onReject }) {
  const [open, setOpen] = useState(false);
  if (!requests?.length) return null;
  const addPath = (id) => `${PROJECT_PAGES.add.path(project)}?request=${id}`;

  return (
    <section className={styles.box} aria-label="Pending registration requests">
      <button type="button" className={styles.bar} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={styles.count}>{requests.length}</span>
        <span>
          Pending Approval: {requests.length === 1 ? 'registration request' : 'registration requests'}
        </span>
        <span className={styles.toggle}>{open ? 'Hide' : 'Review'}</span>
      </button>
      {open && (
        <ul className={styles.list}>
          {requests.map((r) => (
            <li key={r.id} className={styles.item}>
              <span className={styles.who}>
                <strong>{r.name}</strong>
                <span>
                  {r.contact_number} · {r.email} · {formatDate(r.created_at)}
                </span>
              </span>
              <span className={styles.actions}>
                <Link to={addPath(r.id)} className={styles.add}>
                  Approve
                </Link>
                {onReject && (
                  <button type="button" className={styles.reject} onClick={() => onReject(r)}>
                    Reject
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
