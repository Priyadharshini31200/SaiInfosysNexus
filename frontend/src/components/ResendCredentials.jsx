import { useRef, useState } from 'react';
import { registrationsApi } from '../api/registrations';
import Alert from './Alert';
import Modal from './Modal';
import styles from './CustomerList.module.css';

/**
 * Staff with Add Customer (Entry) permission: emails the customer a fresh temporary password and Activation Key for the
 * SAME account (same User ID / Serial Number and licence; nothing new is created). The saved password is only a hash,
 * so it cannot be re-sent; the earlier password and key stop working. Neither is ever shown here.
 * Offered only while the customer has not started using the account; after that, Reset Password is the right action.
 */
export default function ResendCredentials({ project, customer: c, onChanged, onNotify }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const busyRef = useRef(false);

  const close = () => {
    setOpen(false);
    setError('');
  };

  async function confirm() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const { email_sent, ...updated } = await registrationsApi.resendCredentials(project, c.id);
      onChanged(updated);
      if (email_sent) {
        onNotify('success', `Credentials for ${c.name} were submitted to the mail server for ${c.email}.`);
        close();
      } else {
        setError(`The email could not be sent (${updated.credentials_email_status_label}). Check the mail settings and try again.`);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} disabled={!c.email}>
        Resend Credentials
      </button>
      {open && (
        <Modal title="Resend Credentials" onClose={busy ? () => {} : close}>
          <p className={styles.message}>
            Email {c.name} ({c.email}) their login details again? A new temporary password is issued for User ID {c.user_id}
            (the earlier one stops working). The Activation Key is kept if it was already emailed; if no email ever went out, a new key is
            included. No new customer, serial number or licence is created.
          </p>
          {error && <Alert type="error">{error}</Alert>}
          <div className={styles.dialogActions}>
            <button type="button" className={styles.dangerSolid} disabled={busy} onClick={confirm}>
              {busy ? 'Sending…' : 'Yes, resend'}
            </button>
            <button type="button" onClick={close} disabled={busy}>
              Cancel
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
