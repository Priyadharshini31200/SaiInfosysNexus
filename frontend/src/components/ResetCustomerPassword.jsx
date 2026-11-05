import { useState } from 'react';
import { registrationsApi } from '../api/registrations';
import Alert from './Alert';
import { TextField } from './Field';
import Modal from './Modal';
import styles from './CustomerList.module.css';

/**
 * Super Admin only: issues the customer a new temporary password by email and forces a change at their next sign-in.
 * The optional legacy reference records which old local PRN account this Nexus account is being linked to.
 * The new password is never shown unless the email could not be sent (then once, so it can be passed on another way).
 */
export default function ResetCustomerPassword({ project, customer: c, onChanged, onNotify }) {
  const [open, setOpen] = useState(false);
  const [legacy, setLegacy] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fallback, setFallback] = useState('');

  const close = () => {
    setOpen(false);
    setFallback('');
    setLegacy('');
    setError('');
  };

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const { email_sent, temporary_password, ...updated } = await registrationsApi.resetPassword(project, c.id, legacy.trim());
      onChanged(updated);
      if (email_sent) {
        onNotify('success', `A new temporary password was emailed to ${c.name}.`);
        close();
      } else {
        setFallback(temporary_password || '');
      }
    } catch (err) {
      setError(err.errors?.email?.[0] || err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className={styles.danger} onClick={() => setOpen(true)}>
        Reset Password
      </button>
      {open && (
        <Modal title="Reset Password" onClose={close}>
          {fallback ? (
            <>
              <Alert type="error">The email could not be sent. Give the customer this temporary password yourself; it is shown only once.</Alert>
              <p className={styles.message}>
                <strong>{fallback}</strong>
              </p>
              <div className={styles.dialogActions}>
                <button type="button" onClick={close}>Done</button>
              </div>
            </>
          ) : (
            <form onSubmit={submit} noValidate>
              <p className={styles.message}>
                Send {c.name} ({c.user_id}) a new temporary password by email? Their current password and sign-ins stop working
                immediately, and they must choose a new password at next sign-in.
              </p>
              {error && <Alert type="error">{error}</Alert>}
              <TextField
                compact
                label="Legacy account reference (optional)"
                value={legacy}
                maxLength={200}
                onChange={(e) => setLegacy(e.target.value)}
                autoFocus
              />
              <div className={styles.dialogActions}>
                <button type="submit" className={styles.dangerSolid} disabled={busy}>
                  {busy ? 'Sending…' : 'Yes, reset password'}
                </button>
                <button type="button" onClick={close} disabled={busy}>Keep</button>
              </div>
            </form>
          )}
        </Modal>
      )}
    </>
  );
}
