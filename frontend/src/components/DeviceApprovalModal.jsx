import { useCallback, useState } from 'react';
import { formatDateTime } from '../utils/date';
import Alert from './Alert';
import Modal from './Modal';
import styles from './DeviceApprovalModal.module.css';

const KIND = {
  transfer: { label: 'Device Transfer', badge: styles.badgeTransfer, card: styles.cardTransfer },
  revoke: { label: 'Revoke Device', badge: styles.badgeRevoke, card: styles.cardRevoke },
};

/** One request card: type badge, customer, device, request date and status. Green border = transfer, orange/red = revoke. */
export function RequestCard({ kind, customer, device, requestedAt, status, children }) {
  const k = KIND[kind];
  return (
    <article className={`${styles.card} ${k.card}`} aria-label={`${k.label}: ${customer}`}>
      <span className={`${styles.badge} ${k.badge}`}>{k.label}</span>
      <dl className={styles.facts}>
        <div><dt>Customer</dt><dd>{customer}</dd></div>
        <div><dt>Device</dt><dd>{device}</dd></div>
        <div><dt>Request date</dt><dd>{requestedAt ? formatDateTime(requestedAt) : '—'}</dd></div>
        <div><dt>Status</dt><dd>{status}</dd></div>
      </dl>
      {children}
    </article>
  );
}

/**
 * Centred "Device Transfer Requests" dialog (Super Admin). Lists every pending transfer; nothing is approved or rejected until the
 * Super Admin confirms in a second dialog. The server still decides: this only asks (onDecide -> existing Nexus API).
 */
export default function DeviceApprovalModal({ requests, onDecide, onClose }) {
  const [pending, setPending] = useState(null); // { request, decision }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const closeConfirm = useCallback(() => setPending(null), []);
  const ignore = useCallback(() => {}, []);

  async function confirm() {
    setBusy(true);
    setError('');
    try {
      await onDecide(pending.request, pending.decision);
      setPending(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const r = pending?.request;
  const approve = pending?.decision === 'approve';
  const ask = (request, decision) => {
    setError('');
    setPending({ request, decision });
  };
  return (
    <>
      <Modal title="Device Transfer Requests" onClose={onClose} tone="transfer" wide>
        {requests.length === 0 && <p className={styles.empty}>No pending requests.</p>}
        <div className={styles.list}>
          {requests.map((q) => (
            <RequestCard
              key={q.id}
              kind="transfer"
              customer={`${q.to_name} (${q.to_user_id})`}
              device={`${q.device_name || 'Computer'} · registered to ${q.from_name} (${q.from_user_id})`}
              requestedAt={q.requested_at}
              status="Pending approval"
            >
              <div className={styles.actions}>
                <button type="button" className={styles.approve} disabled={busy} onClick={() => ask(q, 'approve')}>
                  Approve
                </button>
                <button type="button" className={styles.reject} disabled={busy} onClick={() => ask(q, 'reject')}>
                  Reject
                </button>
              </div>
            </RequestCard>
          ))}
        </div>
      </Modal>
      {pending && (
        <Modal title={approve ? 'Confirm device transfer' : 'Reject transfer request'} onClose={busy ? ignore : closeConfirm} tone="transfer">
          <p className={styles.message}>
            {approve
              ? `Approve: release ${r.device_name || 'this computer'} from ${r.from_user_id} (${r.from_name}) and let ${r.to_user_id} (${r.to_name}) register it? ${r.from_user_id} will no longer be able to use it.`
              : `Reject the transfer request from ${r.to_user_id} (${r.to_name})? Nothing is changed.`}
          </p>
          {error && <Alert type="error">{error}</Alert>}
          <div className={styles.actions}>
            <button type="button" className={approve ? styles.approve : styles.reject} disabled={busy} onClick={confirm}>
              {busy ? 'Saving…' : approve ? 'Yes, approve transfer' : 'Yes, reject'}
            </button>
            <button type="button" onClick={closeConfirm} disabled={busy}>Cancel</button>
          </div>
        </Modal>
      )}
    </>
  );
}
