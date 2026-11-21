import { useState } from 'react';
import { registrationsApi } from '../api/registrations';
import DeleteCustomerDialog from './DeleteCustomerDialog';
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
        <DeleteCustomerDialog name={c.name} serial={serial} typed={typed} onTyped={setTyped} error={error} blocked={blocked} busy={busy} onCancel={() => setOpen(false)} onConfirm={confirm} />
      )}
    </>
  );
}
