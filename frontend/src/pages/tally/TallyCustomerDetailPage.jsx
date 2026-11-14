import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { tallyApi } from '../../api/tallysl';
import Button from '../../components/Button';
import Toast from '../../components/Toast';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDate, formatDateTime } from '../../utils/date';
import { NA, PLAN_LABELS, STATE_LABELS, useToast } from './TallyShared';
import styles from './Tally.module.css';

const FIELD_LABELS = {
  edition: 'Edition', tss_details: 'Tally Software Services (TSS)', virtual_user_limit: 'Virtual User Limit',
  license_administrator: 'License Administrator', gateway_server: 'Tally Gateway Server', registered_email: 'Tally Registered Email ID',
  tss_expiry_date: 'Tally / TSS Expiry Date',
};

function Row({ label, children }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Everything about one Tally SL customer: info, Tally licence data, Nexus licence, devices, history, activity, classification. */
export default function TallyCustomerDetailPage() {
  const { product, id } = useParams();
  const navigate = useNavigate();
  const { can, loaded } = useTallyAccess();
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const [busy, setBusy] = useState('');
  const [licenseType, setLicenseType] = useState('TRIAL');
  const [days, setDays] = useState(3);
  const [issuedKey, setIssuedKey] = useState('');
  const { toast, clear, notify } = useToast();
  const allowed = can('view', product);

  const load = useCallback(() => tallyApi.customer(product, id).then((data) => setState({ loading: false, error: '', data }))
    .catch((err) => setState({ loading: false, error: err.message, data: null })), [product, id]);
  useEffect(() => { if (allowed) load(); }, [allowed, load]);

  async function run(label, fn, success) {
    setBusy(label);
    try {
      const result = await fn();
      if (result?.activation_key) setIssuedKey(result.activation_key);
      notify('success', success);
      await load();
    } catch (err) {
      notify('error', err.message);
    } finally {
      setBusy('');
    }
  }

  if (loaded && !allowed) return <p>You do not have permission to view this product.</p>;
  const { loading, error, data } = state;
  if (loading) return <p>Loading…</p>;
  if (error) return <p role="alert">{error}</p>;
  const { customer: c, tally, license, devices, transfers, activation_history: history, last_activity: last, classification } = data;
  const act = (a, body) => () => tallyApi.licenseAction(product, id, a, body);
  const isTrial = license.plan === 'TRIAL';

  return (
    <div className={styles.page}>
      <div className={styles.actions}>
        <Link to={`/tally/${product}/customers`} className={styles.link}>← Customers</Link>
        {can('delete', product) && (
          <Button variant="secondary" onClick={async () => {
            if (!window.confirm(`Delete ${c.name} (${c.tally_serial})? This cannot be undone.`)) return;
            try { await tallyApi.deleteCustomer(product, id); navigate(`/tally/${product}/customers`); } catch (err) { notify('error', err.message); }
          }}>Delete Customer</Button>
        )}
      </div>

      <section className={styles.section} aria-label="Customer information">
        <h3>Customer Information</h3>
        <dl className={styles.grid}>
          <Row label="Customer Name">{c.name}</Row>
          <Row label="Product">{c.product_name}</Row>
          <Row label="User ID / Tally Serial Number">{c.tally_serial}</Row>
          <Row label="Email">{c.email}</Row>
          <Row label="Contact Number">{c.contact_number}</Row>
          <Row label="Registered On">{formatDateTime(c.registered_at)}</Row>
        </dl>
      </section>

      <section className={styles.section} aria-label="Tally information">
        <h3>Tally Information</h3>
        <p className={styles.muted}>Reported by the local GSTR client. Details it could not verify are shown as not available. Tally / TSS expiry is not the Nexus license expiry.</p>
        <dl className={styles.grid}>
          <Row label="Tally Serial Number">{tally.tally_serial}</Row>
          {Object.entries(FIELD_LABELS).map(([k, label]) => {
            const value = k === 'tss_expiry_date' ? (tally[k] ? formatDate(tally[k]) : '') : tally[k];
            return <Row key={k} label={label}>{value !== '' && value !== null && value !== undefined ? String(value) : NA}</Row>;
          })}
          <Row label="Last Reported">{tally.reported_at ? formatDateTime(tally.reported_at) : NA}</Row>
        </dl>
      </section>

      <section className={styles.section} aria-label="Nexus license">
        <h3>Nexus License</h3>
        <dl className={styles.grid}>
          <Row label="License Plan">{PLAN_LABELS[license.plan] || license.plan}</Row>
          <Row label="Status">{STATE_LABELS[license.state] || license.state}</Row>
          <Row label="Nexus Activation Date">{license.activated_at ? formatDate(license.activated_at) : NA}</Row>
          <Row label="Nexus Expiry Date">{license.plan === 'LIFETIME' ? 'Never (Lifetime)' : license.expires_at ? formatDate(license.expires_at) : NA}</Row>
          {license.trial_days ? <Row label="Trial Days">{license.trial_days}</Row> : null}
          <Row label="Activation Key">{license.activation_key_suffix ? `••••${license.activation_key_suffix}` : NA}</Row>
        </dl>
        {issuedKey && <p className={styles.key} role="status">New activation key (shown once, also emailed): <strong>{issuedKey}</strong></p>}
        <div className={styles.actions}>
          {can('license_activation', product) && (
            <>
              <label className={styles.field}>License Plan
                <select value={licenseType} onChange={(e) => setLicenseType(e.target.value)}>
                  <option value="TRIAL">Trial</option><option value="ANNUAL">Annual (1 Year)</option><option value="LIFETIME">Lifetime</option>
                </select>
              </label>
              {licenseType === 'TRIAL' && (
                <label className={styles.field}>Trial Days
                  <input type="number" min="1" max="365" value={days} onChange={(e) => setDays(e.target.value)} />
                </label>
              )}
              <Button loading={busy === 'activate'} onClick={() => run('activate', act('activate', { license_plan: licenseType, trial_days: licenseType === 'TRIAL' ? Number(days) : undefined }), 'License activated and the activation key was emailed.')}>
                Activate License
              </Button>
            </>
          )}
          {can('license_management', product) && (
            <>
              {isTrial && <Button variant="secondary" loading={busy === 'extend'} onClick={() => run('extend', act('extend-trial', { days: Number(days) }), `Trial extended by ${days} day(s).`)}>Extend Trial</Button>}
              {isTrial && <Button variant="secondary" loading={busy === 'cancel'} onClick={() => run('cancel', act('cancel-trial'), 'Trial cancelled.')}>Cancel Trial</Button>}
              {license.plan === 'ANNUAL' && <Button variant="secondary" loading={busy === 'renew'} onClick={() => run('renew', act('renew'), 'License renewed for one year.')}>Renew License</Button>}
              <Button variant="secondary" loading={busy === 'inactivate'} onClick={() => run('inactivate', act('inactivate'), 'License inactivated.')}>Inactivate License</Button>
              <Button variant="secondary" loading={busy === 'key'} onClick={() => run('key', act('reissue-key'), 'New activation key generated and emailed. The previous key no longer works.')}>Resend Activation Details</Button>
            </>
          )}
        </div>
      </section>

      <section className={styles.section} aria-label="Device details">
        <h3>Device Details</h3>
        {devices.length === 0 ? <p className={styles.muted}>No device has been enrolled yet.</p> : (
          <div className={styles.tableWrap}>
            <table className={styles.matrix}>
              <thead><tr><th>Device</th><th>Status</th><th>Enrolled</th><th>Last Seen</th><th>Version</th><th>Note</th></tr></thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.id}>
                    <td>{d.name || '—'}</td><td>{d.active ? 'Current device' : 'Revoked'}</td><td>{formatDateTime(d.enrolled_at)}</td>
                    <td>{formatDateTime(d.last_seen_at)}</td><td>{d.app_version || '—'}</td><td>{d.revoked_reason || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {can('device_management', product) && devices.some((d) => d.active) && (
          <div className={styles.actions} style={{ marginTop: 10 }}>
            <Button variant="secondary" loading={busy === 'revoke'} onClick={() => window.confirm('Sign out and revoke the current device? The customer must use Activate User again.') && run('revoke', () => tallyApi.revokeDevice(product, id), 'Device revoked.')}>Revoke Current Device</Button>
          </div>
        )}
      </section>

      <section className={styles.section} aria-label="Activation history">
        <h3>Activation History</h3>
        {transfers.length > 0 && (
          <ul>{transfers.map((t, i) => <li key={i}>{formatDateTime(t.at)} — {t.kind === 'REPLACED' ? `Device replaced: ${t.from || 'previous'} → ${t.to || 'new'}` : t.kind === 'ENROLLED' ? `Device enrolled: ${t.to}` : `Device revoked: ${t.from}`} ({t.by})</li>)}</ul>
        )}
        <div className={styles.tableWrap}>
          <table className={styles.matrix}>
            <thead><tr><th>When</th><th>Action</th><th>Detail</th><th>By</th></tr></thead>
            <tbody>
              {history.map((h) => <tr key={h.id}><td>{formatDateTime(h.at)}</td><td>{h.action}</td><td>{h.detail}</td><td>{h.actor}</td></tr>)}
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.section} aria-label="Usage">
        <h3>Last Application Activity &amp; Usage Classification</h3>
        <dl className={styles.grid}>
          <Row label="Last Opened">{last.last_opened ? formatDateTime(last.last_opened) : NA}</Row>
          <Row label="Opening Count">{last.opens}</Row>
          <Row label="Active Usage Days (rolling window)">{last.active_days}</Row>
          <Row label="Classification">{classification.label}</Row>
        </dl>
      </section>
      <Toast toast={toast} onDone={clear} />
    </div>
  );
}
