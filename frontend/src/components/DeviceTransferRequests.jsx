import { useState } from 'react';
import { formatDateTime } from '../utils/date';
import styles from './RegistrationRequests.module.css';

/**
 * Pending Device Transfers (Super Admin): customers refused on a computer that is registered to ANOTHER customer's licence.
 * Approve releases the previous registration (kept as history, server side, in one transaction) so the requester can register by
 * signing in; Reject changes nothing. Renders nothing when there are none. The server decides; this only asks.
 */
export default function DeviceTransferRequests({ requests, onDecide }) {
  const [busyId, setBusyId] = useState(null);
  if (!requests?.length) return null;

  const decide = async (r, decision) => {
    if (busyId) return; // one decision at a time
    const text =
      decision === 'approve'
        ? `Approve: release ${r.device_name || 'this computer'} from ${r.from_user_id} (${r.from_name}) and let ${r.to_user_id} (${r.to_name}) register it? ${r.from_user_id} will no longer be able to use it.`
        : `Reject the transfer request from ${r.to_user_id} (${r.to_name})? Nothing is changed.`;
    if (!window.confirm(text)) return;
    setBusyId(r.id);
    try {
      await onDecide(r, decision);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className={styles.box} aria-label="Pending device transfers">
      <div className={styles.bar}>
        <span className={styles.count}>{requests.length}</span>
        <span>Pending Device Transfers</span>
      </div>
      <ul className={styles.list}>
        {requests.map((r) => (
          <li key={r.id} className={styles.item}>
            <span className={styles.who}>
              <strong>
                {r.to_user_id} ({r.to_name}) wants {r.device_name || 'a computer'}
              </strong>
              <span>
                Registered to {r.from_user_id} ({r.from_name}) · requested from {r.requesting_device_name || 'unknown computer'} ·{' '}
                {formatDateTime(r.requested_at)}
              </span>
            </span>
            <span className={styles.actions}>
              <button type="button" className={styles.add} disabled={busyId === r.id} onClick={() => decide(r, 'approve')}>
                Approve
              </button>
              <button type="button" className={styles.reject} disabled={busyId === r.id} onClick={() => decide(r, 'reject')}>
                Reject
              </button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
