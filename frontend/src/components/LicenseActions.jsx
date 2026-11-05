import { useState } from 'react';
import { registrationsApi } from '../api/registrations';
import { useAuth } from '../context/AuthContext';
import { formatDate } from '../utils/date';
import Alert from './Alert';
import { TextField } from './Field';
import Modal from './Modal';
import styles from './CustomerList.module.css';

/**
 * Trial Extend / 1-Year Renew / Trial Cancel / License Inactivate for one customer, with their dialogs.
 * Extend and Renew need the Edit permission; Cancel and Inactivate need Delete (the API enforces the same rules).
 * Only the actions that make sense for the license's current state are offered.
 */
export default function LicenseActions({ project, customer: c, onChanged, onNotify }) {
  const { can } = useAuth();
  const [dialog, setDialog] = useState(null); // 'extend' | 'renew' | 'cancel' | 'inactivate'
  const [days, setDays] = useState('3');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const open = (type) => {
    setDialog(type);
    setError('');
    setDays('3');
  };
  const close = () => setDialog(null);

  async function run(action, success) {
    setBusy(true);
    setError('');
    try {
      onChanged(await action());
      onNotify('success', success);
      close();
    } catch (e) {
      setError(e.errors?.days?.[0] || e.message);
    } finally {
      setBusy(false);
    }
  }

  const isTrial = c.license_type === 'TRIAL';
  const canExtend = can('edit') && isTrial && (c.state === 'active' || c.state === 'expired');
  const canRenew = can('edit') && c.license_type === 'ONE_YEAR' && Boolean(c.expires_at) && (c.state === 'active' || c.state === 'expired');
  const canCancel = can('delete') && isTrial && c.state === 'active';
  const canInactivate = can('delete') && c.state !== 'inactive';

  return (
    <>
      {canExtend && <button type="button" onClick={() => open('extend')}>Extend Trial</button>}
      {canRenew && <button type="button" onClick={() => open('renew')}>Renew License</button>}
      {canCancel && <button type="button" className={styles.danger} onClick={() => open('cancel')}>Cancel Trial</button>}
      {canInactivate && <button type="button" className={styles.danger} onClick={() => open('inactivate')}>Inactivate</button>}

      {dialog === 'extend' && (
        <Modal title="Extend Trial" onClose={close}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              run(() => registrationsApi.extendTrial(project, c.id, Number(days)), `Trial extended for ${c.name}.`);
            }}
            noValidate
          >
            <p className={styles.message}>
              {c.name} · valid until {formatDate(c.expires_at)}
            </p>
            {error && <Alert type="error">{error}</Alert>}
            <TextField compact required label="Additional days" type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} autoFocus />
            <div className={styles.dialogActions}>
              <button type="submit" className={styles.primary} disabled={busy}>
                {busy ? 'Saving…' : 'Extend'}
              </button>
              <button type="button" onClick={close} disabled={busy}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {dialog === 'renew' && (
        <Modal title="Renew License" onClose={close}>
          <p className={styles.message}>
            Renew the 1 Year licence of {c.name} ({c.company_name})? Currently valid until {formatDate(c.expires_at)}; one more year is added
            {c.state === 'expired' ? ' from today' : ' to that date'}. The renewal is recorded in the history.
          </p>
          {error && <Alert type="error">{error}</Alert>}
          <div className={styles.dialogActions}>
            <button
              type="button"
              className={styles.primary}
              disabled={busy}
              onClick={() => run(() => registrationsApi.renew(project, c.id), `License renewed for ${c.name}.`)}
            >
              {busy ? 'Saving…' : 'Yes, renew for 1 year'}
            </button>
            <button type="button" onClick={close} disabled={busy}>
              Keep
            </button>
          </div>
        </Modal>
      )}

      {(dialog === 'cancel' || dialog === 'inactivate') && (
        <Modal title={dialog === 'cancel' ? 'Cancel Trial' : 'Inactivate License'} onClose={close}>
          <p className={styles.message}>
            {dialog === 'cancel'
              ? `Cancel the active trial of ${c.name} (${c.company_name})? Their trial access ends immediately.`
              : `Inactivate the license of ${c.name} (${c.company_name})? Further authorized usage is blocked.`}
          </p>
          {error && <Alert type="error">{error}</Alert>}
          <div className={styles.dialogActions}>
            <button
              type="button"
              className={styles.dangerSolid}
              disabled={busy}
              onClick={() =>
                dialog === 'cancel'
                  ? run(() => registrationsApi.cancelTrial(project, c.id), `Trial cancelled for ${c.name}.`)
                  : run(() => registrationsApi.inactivate(project, c.id), `License inactivated for ${c.name}.`)
              }
            >
              {busy ? 'Saving…' : dialog === 'cancel' ? 'Yes, cancel trial' : 'Yes, inactivate'}
            </button>
            <button type="button" onClick={close} disabled={busy}>
              Keep
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
