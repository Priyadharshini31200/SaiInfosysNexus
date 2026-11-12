import { useCallback, useState } from 'react';
import DeviceApprovalModal from './DeviceApprovalModal';
import styles from './RegistrationRequests.module.css';

/**
 * Pending Device Transfers (Super Admin): customers refused on a computer that is registered to ANOTHER customer's licence.
 * A compact banner shows the count; "Review requests" opens the centred approval dialog (green border). Approve releases the
 * previous registration (kept as history, server side, in one transaction); Reject changes nothing. Renders nothing when there
 * are none. The server decides; this only asks, and only after an explicit confirmation.
 */
export default function DeviceTransferRequests({ requests, onDecide }) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  if (!requests?.length && !open) return null;

  return (
    <section className={styles.box} aria-label="Pending device transfers">
      <div className={styles.bar}>
        <span className={styles.count}>{requests.length}</span>
        <span>Pending Device Transfers</span>
        <button type="button" className={styles.add} style={{ marginLeft: 'auto' }} onClick={() => setOpen(true)}>
          Review requests
        </button>
      </div>
      {open && <DeviceApprovalModal requests={requests} onDecide={onDecide} onClose={close} />}
    </section>
  );
}
