import { useState } from 'react';
import { registrationsApi } from '../api/registrations';
import Alert from './Alert';
import Modal from './Modal';
import styles from './CustomerList.module.css';

/**
 * Super Admin only: replaces the customer's Activation Key (the old one stops working at once) and emails the new one.
 * The password, serial number, licence dates and an already activated computer are untouched. The new key is shown here
 * only when the email could not be sent, and only once.
 */
export default function ReissueActivationKey({ project, customer: c, onChanged, onNotify }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fallback, setFallback] = useState('');

  const close = () => {
    setOpen(false);
    setFallback('');
    setError('');
  };

  async function confirm() {
    setBusy(true);
    setError('');
    try {
      const { email_sent, activation_key, ...updated } = await registrationsApi.reissueKey(project, c.id);
      onChanged(updated);
      if (email_sent) {
        onNotify('success', `A new activation key was sent to the mail server for ${c.name}. Delivery is not confirmed by the mail system.`);
        close();
      } else {
        setFallback(activation_key || '');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className={styles.danger} onClick={() => setOpen(true)}>
        Reissue Key
      </button>
      {open && (
        <Modal title="Reissue Activation Key" onClose={close}>
          {fallback ? (
            <>
              <Alert type="error">The email could not be sent. Give the customer this key yourself; it is shown only once.</Alert>
              <p className={styles.message}>
                <strong>{fallback}</strong>
              </p>
              <div className={styles.dialogActions}>
                <button type="button" onClick={close}>Done</button>
              </div>
            </>
          ) : (
            <>
              <p className={styles.message}>
                Issue {c.name} ({c.user_id}) a new Activation Key and email it to them? The current key stops working immediately.
                Their password, licence dates and an already activated computer are not affected.
              </p>
              {error && <Alert type="error">{error}</Alert>}
              <div className={styles.dialogActions}>
                <button type="button" className={styles.dangerSolid} disabled={busy} onClick={confirm}>
                  {busy ? 'Issuing…' : 'Yes, reissue key'}
                </button>
                <button type="button" onClick={close} disabled={busy}>Keep</button>
              </div>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
