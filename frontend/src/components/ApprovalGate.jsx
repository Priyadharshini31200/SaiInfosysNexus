import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { registrationsApi } from '../api/registrations';
import { PROJECT_ITEMS, PROJECT_PAGES } from '../layouts/navigation';
import { formatDate } from '../utils/date';
import useSyncChanges from '../utils/useSyncChanges';
import Button from './Button';
import styles from './ApprovalGate.module.css';

/**
 * Super Admin only. A sign-up sent from a connected project waits as "Pending Approval"; until it is approved or rejected
 * this centred dialog sits above every Nexus page and cannot be dismissed, so no other tab can be used meanwhile. It is
 * rebuilt from the server on every load, so a refresh or a new sign-in shows the same pending requests again, once each.
 * Approve opens Add Customer for that project, prefilled; the dialog stays away while that very request is being filled in.
 */
export default function ApprovalGate() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [pending, setPending] = useState([]); // [{ ...request, project, projectLabel }], oldest first
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmReject, setConfirmReject] = useState(false);
  const synced = useSyncChanges(
    async () => ({ version: (await Promise.all(PROJECT_ITEMS.map((p) => registrationsApi.sync(p.slug).then((r) => r.version).catch(() => '')))).join('|') }),
    true,
  );

  const load = useCallback(async () => {
    const lists = await Promise.all(
      PROJECT_ITEMS.map((p) =>
        registrationsApi
          .requests(p.slug)
          .then(({ results }) => results.map((r) => ({ ...r, project: p.slug, projectLabel: p.label })))
          .catch(() => []),
      ),
    );
    setPending(lists.flat().sort((a, b) => new Date(a.created_at) - new Date(b.created_at)));
  }, []);

  // Also reloads on every page change, so a request approved on the Add Customer page disappears as soon as it is saved.
  useEffect(() => {
    load();
  }, [load, synced, pathname, search]);

  // The request currently being filled in on the Add Customer page is "in progress": no dialog over the form.
  const inProgress = new URLSearchParams(search).get('request');
  const current = pending.find((r) => !(pathname === PROJECT_PAGES.add.path(r.project) && inProgress === String(r.id)));

  useEffect(() => {
    setError('');
    setConfirmReject(false);
  }, [current?.id]);

  if (!current) return null;

  const approve = () => navigate(`${PROJECT_PAGES.add.path(current.project)}?request=${current.id}`);
  const reject = async () => {
    if (!confirmReject) return setConfirmReject(true);
    setBusy(true);
    setError('');
    try {
      await registrationsApi.rejectRequest(current.project, current.id);
      setPending((list) => list.filter((r) => !(r.id === current.id && r.project === current.project)));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      setConfirmReject(false);
    }
  };

  const rows = [
    ['Customer Name', current.name],
    ['Contact Number', current.contact_number],
    ['Email', current.email],
    ['Project Name', current.projectLabel],
    ['Request Date', formatDate(current.created_at)],
    ['User ID', 'To Be Assigned'],
    ['Serial Number', 'To Be Assigned'],
    ['Activation Key', 'To Be Assigned'],
  ];

  return (
    <div className={styles.backdrop} data-modal>
      <div className={styles.dialog} role="alertdialog" aria-modal="true" aria-labelledby="approval-title">
        <h2 id="approval-title" className={styles.title}>New Customer Registration – Approval Required</h2>
        {pending.length > 1 && <p className={styles.more}>{pending.length} requests are waiting; this is the oldest.</p>}
        <dl className={styles.rows}>
          {rows.map(([label, value]) => (
            <div key={label} className={styles.row}>
              <dt>{label}</dt>
              <dd className={value === 'To Be Assigned' ? styles.tba : undefined}>{value}</dd>
            </div>
          ))}
        </dl>
        {error && <p className={styles.error} role="alert">{error}</p>}
        {confirmReject && <p className={styles.error}>Reject this request? No customer, license or credentials will be created. Click Reject again to confirm.</p>}
        <div className={styles.actions}>
          <Button onClick={approve} disabled={busy}>Approve</Button>
          <Button variant="secondary" onClick={reject} loading={busy} loadingLabel="Rejecting…">
            {confirmReject ? 'Confirm Reject' : 'Reject'}
          </Button>
        </div>
      </div>
    </div>
  );
}
