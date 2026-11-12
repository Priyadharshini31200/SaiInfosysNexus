import { useCallback, useEffect, useState } from 'react';
import { registrationsApi } from '../api/registrations';
import { formatDate, formatDateTime } from '../utils/date';
import Alert from './Alert';
import DeviceApprovalModal, { RequestCard } from './DeviceApprovalModal';
import Modal from './Modal';
import styles from './CustomerList.module.css';

const ACTIONS = {
  revoke: {
    title: 'Revoke Device',
    confirm: 'Yes, revoke device',
    message: (c) =>
      `Revoke the registered computer of ${c.name}? PRN stops working on it at its next check, and no other computer can be registered until you transfer the licence.`,
    run: (project, id) => registrationsApi.revokeDevice(project, id, { block: true, confirm: true }),
    done: (c) => `Device revoked for ${c.name}.`,
  },
  transfer: {
    title: 'Transfer Device',
    confirm: 'Yes, allow transfer',
    message: (c) =>
      `Allow ${c.name} to move the licence to a new computer? The current computer (if any) is revoked and the customer can register one new computer. Their expiry and activation date do not change.`,
    run: (project, id) => registrationsApi.transferDevice(project, id, {}),
    done: (c) => `Transfer authorised for ${c.name}.`,
  },
};

/**
 * Compact Device Management section of the Customer drawer (Super Admin only: the API refuses everyone else).
 * Shows the registered device, activation / last-used dates, licence type and expiry, and the device status, with
 * confirmed Revoke and Transfer actions. A project without device licensing answers 404 and the section stays hidden.
 */
export default function DeviceManagement({ project, customer: c, onChanged, onNotify }) {
  const [panel, setPanel] = useState(null);
  const [dialog, setDialog] = useState(null); // 'revoke' | 'transfer'
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [review, setReview] = useState(false);
  const closeReview = useCallback(() => setReview(false), []);
  const closeDialog = useCallback(() => setDialog(null), []);

  const load = useCallback(async () => {
    try {
      setPanel(await registrationsApi.device(project, c.id));
    } catch {
      setPanel(null); // not a device-licensed project, or no permission
    }
  }, [project, c.id]);

  useEffect(() => {
    load();
  }, [load, c.updated_at]);

  if (!panel) return null;

  const device = panel.device;
  const lifetime = panel.license_type === 'LIFETIME';
  const rows = [
    ['Registered Device', device ? `${device.device_name || 'Unnamed'} · ${device.device_id.slice(0, 8)}` : '—'],
    ['Activation Date', panel.first_activated_at ? formatDateTime(panel.first_activated_at) : 'Not activated'],
    ['Last Used Date', panel.last_used_at ? formatDateTime(panel.last_used_at) : 'Never used'],
    ['License Type', panel.license_type_label],
    ['Expiry Date', lifetime ? 'Lifetime (no expiry)' : panel.effective_expires_at ? formatDate(panel.effective_expires_at) : 'Starts at activation'],
    ['Device Status', panel.device_status_label],
  ];
  const action = dialog ? ACTIONS[dialog] : null;
  const requests = panel.transfer_requests || [];

  // Called by the approval dialog after the Super Admin confirmed; errors are shown inside that dialog.
  async function decide(request, decision) {
    await registrationsApi.decideDeviceTransfer(project, request.id, decision);
    onNotify('success', request.kind === 'first_activation'
      ? decision === 'approve' ? `Activation approved: ${request.device_name || 'the computer'} is now registered to ${request.to_user_id}.` : `Activation request rejected for ${request.to_user_id}.`
      : decision === 'approve'
      ? `Transfer approved: ${request.device_name || 'the computer'} is released from ${request.from_user_id}; ${request.to_user_id} can now sign in on it.`
      : `Transfer request rejected for ${request.to_user_id}.`);
    await load();
    onChanged({});
  }

  async function confirm() {
    setBusy(true);
    setError('');
    try {
      const { device: _device, history: _history, device_status: _s, device_status_label: _l, can_revoke: _r, can_transfer: _t, transfer_requests: _q, ...updated } =
        await action.run(project, c.id);
      onChanged(updated);
      onNotify('success', action.done(c));
      setDialog(null);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.group}>
      <h3 className={styles.groupTitle}>Device Management</h3>
      <dl className={styles.details}>
        {rows.map(([label, value]) => (
          <div key={label} className={styles.detail}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {requests.length > 0 && (
        <div role="group" aria-label="Device requests" className={styles.dialogActions} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{requests.length} pending device request{requests.length > 1 ? 's' : ''}</span>
          <button type="button" className={styles.primary} onClick={() => setReview(true)}>
            Review requests
          </button>
        </div>
      )}
      {review && <DeviceApprovalModal requests={requests} onDecide={decide} onClose={closeReview} />}
      <div className={styles.dialogActions}>
        {panel.can_revoke && (
          <button type="button" className={styles.danger} onClick={() => { setError(''); setDialog('revoke'); }}>
            Revoke Device
          </button>
        )}
        {panel.can_transfer && (
          <button type="button" onClick={() => { setError(''); setDialog('transfer'); }}>
            Transfer Device
          </button>
        )}
      </div>
      {action && (
        <Modal title={action.title} onClose={closeDialog} tone={dialog === 'revoke' ? 'revoke' : 'transfer'} wide>
          <RequestCard
            kind={dialog}
            customer={c.name}
            device={device ? `${device.device_name || 'Unnamed'} · ${device.device_id.slice(0, 8)}` : 'No registered device'}
            requestedAt={new Date().toISOString()}
            status={panel.device_status_label}
          />
          <p className={styles.message} style={{ marginTop: 12 }}>{action.message(c)}</p>
          {error && <Alert type="error">{error}</Alert>}
          <div className={styles.dialogActions}>
            <button type="button" className={styles.dangerSolid} disabled={busy} onClick={confirm}>
              {busy ? 'Saving…' : action.confirm}
            </button>
            <button type="button" onClick={closeDialog} disabled={busy}>
              Keep
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
