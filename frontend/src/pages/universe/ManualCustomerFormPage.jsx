import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import Button from '../../components/Button';
import CopyValue from '../../components/CopyValue';
import { PasswordField, SelectField, TextField } from '../../components/Field';
import styles from '../../components/CustomerForm.module.css';

const EMPTY = { name: '', company_name: '', contact_number: '', email: '', gstin: '', address: '', temporary_password: '', license_type: 'TRIAL', trial_days: '3', years: '1' };
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
const randomPassword = () => { const a = new Uint32Array(10); crypto.getRandomValues(a); return Array.from(a, (n) => CHARS[n % CHARS.length]).join(''); };

function Section({ title, children }) {
  return <fieldset className={styles.section}><legend className={styles.legend}>{title}</legend><div className={styles.grid}>{children}</div></fieldset>;
}

/** Non-Tally -> + Add Project (no :code) opens this Barcode-style form and ONE Save creates the Manual project + its first customer.
 *  With :code it is that manual project's + Add Customer. Same layout and components as the Barcode Add Customer form (CustomerForm). */
export default function ManualCustomerFormPage() {
  const { code: routeCode } = useParams();
  const { state: nav } = useLocation();
  const navigate = useNavigate();
  const isNew = !routeCode;
  const [newName, setNewName] = useState('');
  const [created, setCreated] = useState(null);
  const code = created?.code || routeCode;
  const [pname, setPname] = useState(nav?.project || routeCode || '');
  const [f, setF] = useState(() => ({ ...EMPTY, temporary_password: randomPassword() }));
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  useEffect(() => { if (!isNew && !nav?.project) integrationsApi.universeCustomers(code, { page_size: 1 }).then((d) => d.project && setPname(d.project.name), () => {}); }, [code, nav, isNew]);
  const toList = () => navigate(`/universe/projects/${code}/customers`, { state: { crumb: 'Customers', project: created?.name || pname, page: 'Customer List' } });
  const cancel = () => (isNew ? navigate('/directory/non-tally') : toList());
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const err = (k) => (errors[k] ? [].concat(errors[k])[0] : undefined);
  const text = (k, label, extra = {}) => <TextField compact label={label} value={f[k]} onChange={set(k)} error={err(k)} autoComplete="off" {...extra} />;
 
  async function submit(e) {
    e.preventDefault();
    const x = {};
    if (isNew && newName.trim().length < 2) x.project_name = 'Project name is required.';
    if (f.name.trim().length < 2) x.name = 'This field is required.';
    if (!/^[0-9]{10}$/.test(f.contact_number)) x.contact_number = 'Enter a valid 10-digit mobile number';
    if (!EMAIL.test(f.email.trim())) x.email = 'Enter a valid email address';
    if (f.gstin.trim() && !GSTIN.test(f.gstin.trim().toUpperCase())) x.gstin = 'Enter a valid 15-character GSTIN';
    if (f.temporary_password.length < 8) x.temporary_password = 'Click Generate, or enter at least 8 characters.';
    setErrors(x);
    if (Object.keys(x).length) return;
    setBusy(true); setMessage('');
    try {
      const body = { ...f, name: f.name.trim(), email: f.email.trim(), gstin: f.gstin.trim().toUpperCase(), trial_days: f.license_type === 'TRIAL' ? f.trial_days : undefined, years: f.license_type === 'ONE_YEAR' ? f.years : undefined };
      const d = isNew ? await integrationsApi.addManualProject({ ...body, project_name: newName.trim() }) : await integrationsApi.addManualCustomer(code, body);
      if (isNew) setCreated(d.project);
      setDone(d);
    } catch (ex) { // entered values stay in the form
      const fe = ex.errors || ex.data?.fields || {};
      setErrors(Object.fromEntries(Object.entries(fe).map(([k, v]) => [k === 'customer_name' ? 'name' : k, v])));
      setMessage(ex.message);
    } finally { setBusy(false); }
  }

  if (done) {
    return (
      <form className={styles.form} onSubmit={(e) => { e.preventDefault(); toList(); }}>
        <Alert type="success">Customer saved{created ? ` under the new project ${created.name} (${created.code})` : ''}.</Alert>
        <Section title="Login Details">
          <div><b>User ID / Serial Number</b><CopyValue value={done.user_id} label="User ID" /></div>
          <div><b>Temporary Password (shown only now)</b><CopyValue value={f.temporary_password} label="Temporary password" /></div>
        </Section>
        <div className={styles.actions}><Button type="submit">Go to Customer List</Button></div>
      </form>
    );
  }
  const trial = f.license_type === 'TRIAL';
  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-label="Add customer">
      {message && <Alert type="error">{message}</Alert>}
      <Section title="Customer Details">
        {isNew
          ? <TextField compact required label="Project" value={newName} maxLength={120} placeholder="New project name" error={err('project_name')} autoComplete="off" onChange={(e) => setNewName(e.target.value.replace(/\s+/g, ' ').toUpperCase())} />
          : <TextField compact label="Project" value={pname} readOnly />}
        {text('name', 'Name', { maxLength: 100, required: true })}
        {text('company_name', 'Company Name', { maxLength: 150 })}
        {text('contact_number', 'Contact Number', { maxLength: 10, inputMode: 'numeric', required: true, onChange: (e) => setF((s) => ({ ...s, contact_number: e.target.value.replace(/\D/g, '').slice(0, 10) })) })}
        {text('email', 'Email ID', { maxLength: 254, type: 'email', required: true })}
        {text('gstin', 'GST Details / GSTIN', { maxLength: 15, style: { textTransform: 'uppercase' } })}
        <div className={styles.full}>{text('address', 'Address', { maxLength: 300 })}</div>
      </Section>
      <Section title="Login Details">
        <TextField compact label="User ID" value="" readOnly placeholder="Same as Serial Number (generated on Save)" />
        <div className={styles.passwordRow}>
          <PasswordField compact required label="Temporary Password" value={f.temporary_password} onChange={set('temporary_password')} error={err('temporary_password')} autoComplete="new-password" maxLength={128} />
          <button type="button" className={styles.passwordGenerate} onClick={() => setF((s) => ({ ...s, temporary_password: randomPassword() }))}>Generate</button>
        </div>
      </Section>
      <Section title="License Details">
        <SelectField compact label="License Type" required value={f.license_type} onChange={set('license_type')} error={err('license_type')}>
          <option value="TRIAL">Trial</option><option value="ONE_YEAR">Yearly</option><option value="LIFETIME">Lifetime</option>
        </SelectField>
        <TextField compact label="Serial Number" value="" readOnly placeholder="Generated on Save (9 digits, starts with 9)" />
        <TextField compact label="Activation Key" value="" readOnly placeholder="Generated internally on Save - never shown or emailed" />
        {trial && <TextField compact required label="Trial Days" type="number" min={1} max={3650} value={f.trial_days} onChange={set('trial_days')} error={err('trial_days')} />}
        {f.license_type === 'ONE_YEAR' && <TextField compact required label="Validity (Years)" type="number" min={1} max={10} value={f.years} onChange={set('years')} error={err('years')} />}
      </Section>
      <div className={styles.actions}>
        <Button type="submit" loading={busy} loadingLabel="Saving…">Save</Button>
        <Button variant="secondary" onClick={cancel} disabled={busy}>Cancel</Button>
      </div>
    </form>
  );
}
