import Modal from './Modal';
import styles from './DeleteCustomerDialog.module.css';

const TrashIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6" /><path d="M14 11v6" />
  </svg>
);

/** Presentational Delete Customer confirmation (UI only). All behaviour - the typed-serial check, the delete call, errors - stays with the caller. */
export default function DeleteCustomerDialog({ name, serial, typed, onTyped, error, blocked, busy, onCancel, onConfirm }) {
  const matches = typed.trim() === serial;
  return (
    <Modal title="Delete Customer" icon={<span className={styles.trash}><TrashIcon size={18} /></span>} dialogClass={styles.dialog} onClose={busy ? () => {} : onCancel}>
      <div className={styles.body}>
        <dl className={styles.who}>
          <dt>Customer</dt><dd>{name}</dd>
          <dt>Serial Number</dt><dd className={styles.mono}>{serial}</dd>
        </dl>
        <p className={styles.warn}>This permanently deletes the customer and cannot be undone. It is only possible for a customer with no licence, device or usage history.</p>
        {!blocked && (
          <label className={styles.confirm}>
            <span>Type the serial number <b>{serial}</b> to confirm</span>
            <input value={typed} onChange={(e) => onTyped(e.target.value)} autoFocus autoComplete="off" aria-label="Serial number confirmation" />
          </label>
        )}
        {error && <p role="alert" className={styles.error}>{error}</p>}
        <div className={styles.actions}>
          <button type="button" className={styles.cancel} onClick={onCancel} disabled={busy}>{blocked ? 'Close' : 'Cancel'}</button>
          {!blocked && (
            <button type="button" className={styles.delete} onClick={onConfirm} disabled={busy || !matches} aria-busy={busy}>
              {busy ? <span className={styles.spin} aria-hidden="true" /> : <TrashIcon size={15} />}
              {busy ? 'Deleting…' : 'Delete Customer'}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
