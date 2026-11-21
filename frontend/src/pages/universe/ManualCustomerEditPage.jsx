import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import Button from '../../components/Button';
import { TextField } from '../../components/Field';
import styles from '../../components/CustomerForm.module.css';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

/** Project-scoped Edit Customer for a manual UNIVERSE customer (same layout as the Barcode form). Updates the SAME customer record; User ID, serial,
 *  licence, password and activation key are shown read-only and are never changed here. */
export default function ManualCustomerEditPage() {
  const { code, id } = useParams();
  const navigate = useNavigate();
  const [rec, setRec] = useState(null);
  const [f, setF] = useState(null);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const toList = () => navigate(`/universe/projects/${code}/customers`, { state: { crumb: 'Customers', project: rec?.project?.name, page: 'Customer List' } });
  useEffect(() => {
    let live = true;
    integrationsApi.universeCustomer(code, 'customer', id).then((d) => {
      if (!live) return;
      const r = d.record;
      setRec(r);
      setF({ name: r.registration.name || '', company_name: r.registration.company_name || '', contact_number: r.registration.contact_number || '', email: r.registration.email || '', gstin: r.registration.gstin || '', address: r.registration.address || '' });
    }, (e) => live && setMessage(e.message));
    return () => { live = false; };
  }, [code, id]);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const err = (k) => (errors[k] ? [].concat(errors[k])[0] : undefined);
  const text = (k, label, extra = {}) => <TextField compact label={label} value={f[k]} onChange={set(k)} error={err(k)} autoComplete="off" {...extra} />;

  async function submit(e) {
    e.preventDefault();
    const x = {};
    if (f.name.trim().length < 2) x.name = 'This field is required.';
    if (!EMAIL.test(f.email.trim())) x.email = 'Enter a valid email address';
    if (f.gstin.trim() && !GSTIN.test(f.gstin.trim().toUpperCase())) x.gstin = 'Enter a valid 15-character GSTIN';
    setErrors(x);
    if (Object.keys(x).length) return;
    setBusy(true); setMessage('');
    try {
      await integrationsApi.patchUniverseCustomer(code, 'customer', id, { customer_name: f.name.trim(), company_name: f.company_name, contact_number: f.contact_number, email: f.email.trim(), gstin: f.gstin.trim().toUpperCase(), address: f.address });
      toList();
    } catch (ex) {
      setErrors(Object.fromEntries(Object.entries(ex.errors || ex.data?.fields || {}).map(([k, v]) => [k === 'customer_name' ? 'name' : k, v])));
      setMessage(ex.message);
    } finally { setBusy(false); }
  }

  if (message && !f) return <Alert type="error">{message}</Alert>;
  if (!f) return <p>Loading…</p>;
  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-label="Edit customer">
      {message && <Alert type="error">{message}</Alert>}
      <fieldset className={styles.section}><legend className={styles.legend}>Customer Details</legend><div className={styles.grid}>
        <TextField compact label="Project" value={rec.project.name} readOnly />
        {text('name', 'Name', { maxLength: 150, required: true })}
        {text('company_name', 'Company Name', { maxLength: 150 })}
        {text('contact_number', 'Contact Number', { maxLength: 20, required: true })}
        {text('email', 'Email ID', { maxLength: 254, type: 'email', required: true })}
        {text('gstin', 'GST Details / GSTIN', { maxLength: 15, style: { textTransform: 'uppercase' } })}
        <div className={styles.full}>{text('address', 'Address', { maxLength: 300 })}</div>
      </div></fieldset>
      <fieldset className={styles.section}><legend className={styles.legend}>Login &amp; License (read-only)</legend><div className={styles.grid}>
        <TextField compact label="User ID / Serial Number" value={rec.account?.user_id || ''} readOnly />
        <TextField compact label="License Type" value={rec.license?.type_label || ''} readOnly />
      </div></fieldset>
      <p className={styles.note}>License type, activation key, User ID and password cannot be changed here.</p>
      <div className={styles.actions}>
        <Button type="submit" loading={busy} loadingLabel="Saving…">Save</Button>
        <Button variant="secondary" onClick={toList} disabled={busy}>Cancel</Button>
      </div>
    </form>
  );
}
