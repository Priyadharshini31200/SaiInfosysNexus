import { useState } from 'react';
import { registrationsApi } from '../api/registrations';
import Alert from './Alert';
import Modal from './Modal';
import styles from './CustomerList.module.css';

/** Delete button + confirmation dialog. The API re-checks the Delete permission and the project; `busy` blocks duplicate requests. */
export default function DeleteCustomer({ project, customer: c, onDeleted, onNotify, onClose }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [blocked, setBlocked] = useState(false);
  const [typed, setTyped] = useState('');
  const serial = c.serial_number || c.user_id;

  async function confirm() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await registrationsApi.remove(project, c.id, typed.trim());
      onNotify('success', `Customer deleted: ${c.name}.`);
      onClose();
      onDeleted(c);
    } catch (e) {
      setError(e.message);
      setBlocked(e.status === 409);
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className={styles.danger} onClick={() => { setError(''); setBlocked(false); setTyped(''); setOpen(true); }}>
        Delete
      </button>
      {open && (
        <Modal title="Delete Customer" onClose={busy ? () => {} : () => setOpen(false)}>
          <p className={styles.message}>
            Permanently delete <strong>{c.name}</strong> (Serial Number {c.serial_number || c.user_id})? This is only possible for a new customer with no
            licence, device or usage history. This cannot be undone.
          </p>
          {!blocked && (
            <label className={styles.message}>
              Type the serial number <strong>{serial}</strong> to confirm:
              <input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus autoComplete="off" style={{ display: 'block', width: '100%', marginTop: 6 }} />
            </label>
          )}
          {error && <Alert type="error">{error}</Alert>}
          <div className={styles.dialogActions}>
            {!blocked && (
              <button type="button" className={styles.dangerSolid} disabled={busy || typed.trim() !== serial} onClick={confirm}>
                {busy ? 'Deleting…' : 'Yes, delete'}
              </button>
            )}
            <button type="button" onClick={() => setOpen(false)} disabled={busy}>
              {blocked ? 'Close' : 'Keep'}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
