import { useCallback, useState } from 'react';
import { formatDateTime } from '../utils/date';
import Alert from './Alert';
import Modal from './Modal';
import styles from './DeviceApprovalModal.module.css';

const KIND = {
  activation: { label: 'First Activation', badge: styles.badgeActivation, card: styles.cardActivation },
  transfer: { label: 'Device Transfer', badge: styles.badgeTransfer, card: styles.cardTransfer },
  revoke: { label: 'Revoke Device', badge: styles.badgeRevoke, card: styles.cardRevoke },
};

/** One request card: type badge, customer, device, request date and status. Green border = transfer, orange/red = revoke. */
export function RequestCard({ kind, customer, device, requestedAt, status, existing, requested, reason, children }) {
  const k = KIND[kind];
  return (
    <article className={`${styles.card} ${k.card}`} aria-label={`${k.label}: ${customer}`}>
      <span className={`${styles.badge} ${k.badge}`}>{k.label}</span>
      <dl className={styles.facts}>
        {existing && <div><dt>Existing account / device</dt><dd>{existing}</dd></div>}
        {requested && <div><dt>Requested account / device</dt><dd>{requested}</dd></div>}
        {!existing && <div><dt>Customer</dt><dd>{customer}</dd></div>}
        {!existing && <div><dt>Device</dt><dd>{device}</dd></div>}
        {reason && <div><dt>Reason</dt><dd>{reason}</dd></div>}
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
const kindOf = (q) => (q.kind === 'first_activation' ? 'activation' : 'transfer');

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
  const act = r?.kind === 'first_activation';
  const ask = (request, decision) => {
    setError('');
    setPending({ request, decision });
  };
  return (
    <>
      <Modal title="Device Requests" onClose={onClose} tone="transfer" wide>
        {requests.length === 0 && <p className={styles.empty}>No pending requests.</p>}
        <div className={styles.list}>
          {requests.map((q) => (
            <RequestCard
              key={q.id}
              kind={kindOf(q)}
              customer={`${q.to_name} (${q.to_user_id})`}
              existing={kindOf(q) === 'transfer' ? `${q.from_name} (${q.from_user_id}) · ${q.device_name || 'Computer'}` : undefined}
              requested={kindOf(q) === 'transfer' ? `${q.to_name} (${q.to_user_id}) · ${q.requesting_device_name || 'this computer'}` : undefined}
              reason={q.reason}
              device={kindOf(q) === 'activation'
                ? `${q.device_name || 'New computer'} · first activation`
                : `${q.device_name || 'Computer'} · registered to ${q.from_name} (${q.from_user_id})`}
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
        <Modal title={act ? (approve ? 'Approve first activation' : 'Reject activation request') : approve ? 'Confirm device transfer' : 'Reject transfer request'} onClose={busy ? ignore : closeConfirm} tone="transfer">
          <p className={styles.message}>
            {act
              ? approve
                ? `Approve the first activation of ${r.device_name || 'this computer'} for ${r.to_user_id} (${r.to_name})? The computer is bound to the licence and the validity period starts now.`
                : `Reject the activation request from ${r.to_user_id} (${r.to_name})? Nothing is bound.`
              : approve
              ? `Approve: release ${r.device_name || 'this computer'} from ${r.from_user_id} (${r.from_name}) and let ${r.to_user_id} (${r.to_name}) register it? ${r.from_user_id} will no longer be able to use it.`
              : `Reject the transfer request from ${r.to_user_id} (${r.to_name})? Nothing is changed.`}
          </p>
          {error && <Alert type="error">{error}</Alert>}
          <div className={styles.actions}>
            <button type="button" className={approve ? styles.approve : styles.reject} disabled={busy} onClick={confirm}>
              {busy ? 'Saving…' : approve ? (act ? 'Yes, approve activation' : 'Yes, approve transfer') : 'Yes, reject'}
            </button>
            <button type="button" onClick={closeConfirm} disabled={busy}>Cancel</button>
          </div>
        </Modal>
      )}
    </>
  );
}
