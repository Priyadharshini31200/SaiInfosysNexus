import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { registrationsApi } from '../api/registrations';
import { projectBySlug } from '../layouts/navigation';
import Alert from './Alert';
import Button from './Button';
import { PasswordField, SelectField, TextField } from './Field';
import styles from './CustomerForm.module.css';

const EMPTY = {
  name: '',
  company_name: '',
  contact_number: '',
  email: '',
  address: '',
  gstin: '',
  user_id: '',
  password: '',
  license_type: 'TRIAL',
  activation_key: '',
  serial_number: '',
  trial_days: '3',
};
const PROFILE = ['name', 'company_name', 'contact_number', 'email', 'address', 'gstin', 'serial_number'];
// Company Name, Address and GSTIN are optional; every other profile field is mandatory.
const OPTIONAL = ['company_name', 'address', 'gstin'];
const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const GSTIN_MESSAGE = 'Enter a valid 15-character GSTIN';
const REQUIRED_MESSAGE = 'This field is required.';
const SERIAL = /^7[0-9]{8}$/;
const SERIAL_MESSAGE = 'Serial Number must contain exactly 9 digits and start with 7.';
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const EMAIL_MESSAGE = 'Enter a valid email address';
const CONTACT = /^[0-9]{10}$/;
const CONTACT_MESSAGE = 'Enter a valid 10-digit mobile number';

function Section({ title, children }) {
  return (
    <fieldset className={styles.section}>
      <legend className={styles.legend}>{title}</legend>
      <div className={styles.grid}>{children}</div>
    </fieldset>
  );
}

/**
 * The one Add / Edit Customer form, used by every project (`project` is only the API scope).
 * With `customer` it edits the profile fields; license type, key, User ID and password are fixed after registration.
 */
export default function CustomerForm({ project, customer, prefill, onSaved, onCancel }) {
  const { user } = useAuth();
  const editing = Boolean(customer);
  const approving = !editing && Boolean(prefill);
  const [values, setValues] = useState(() => (customer ? { ...Object.fromEntries(PROFILE.map((f) => [f, customer[f] ?? ''])), user_id: customer.user_id ?? '', password: '' } : { ...EMPTY, ...(prefill ? { name: prefill.name, contact_number: prefill.contact_number, email: prefill.email } : {}) }));
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  // Synchronous guards: state updates are not instant, so two fast clicks could both pass a `saving` check.
  const submittingRef = useRef(false);
  const issuedRef = useRef(false);

  const set = (field) => (e) => {
    setValues((v) => ({ ...v, [field]: e.target.value }));
    setErrors((prev) => ({ ...prev, [field]: undefined }));
  };
  // Digits only, at most 10: typing or pasting anything else is dropped.
  const setContact = (e) => set('contact_number')({ target: { value: e.target.value.replace(/\D/g, '').slice(0, 10) } });
  const setSerial = (e) => set('serial_number')({ target: { value: e.target.value.replace(/\D/g, '').slice(0, 9) } });
  const err = (field) => errors[field]?.[0];
  const isTrial = values.license_type === 'TRIAL';

  // A new customer's serial number is issued by the server when the form opens; the field is read-only.
  async function issueSerial() {
    try {
      const { serial_number } = await registrationsApi.generateSerial(project);
      setValues((v) => ({ ...v, serial_number, user_id: serial_number }));
      setErrors((prev) => ({ ...prev, serial_number: undefined }));
      return true;
    } catch (e) {
      setFormError(e.message);
      return false;
    }
  }

  // A new customer opens with a serial number, activation key and temporary password already issued by the server.
  useEffect(() => {
    if (editing || issuedRef.current) return; // once, even when React (StrictMode) runs effects twice
    issuedRef.current = true;
    issueSerial();
    generate();
    generatePassword();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function generatePassword() {
    setFormError('');
    try {
      const { temporary_password } = await registrationsApi.temporaryPassword(project);
      setValues((v) => ({ ...v, password: temporary_password }));
      setErrors((prev) => ({ ...prev, password: undefined }));
    } catch (e) {
      setFormError(e.message);
    }
  }

  async function generate() {
    setGenerating(true);
    setFormError('');
    try {
      const { activation_key } = await registrationsApi.generateKey(project);
      setValues((v) => ({ ...v, activation_key }));
      setErrors((prev) => ({ ...prev, activation_key: undefined }));
    } catch (e) {
      setFormError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  // Mandatory fields are checked here first so the user sees every gap at once; the server re-validates everything.
  function missing() {
    const required = PROFILE.filter((f) => !OPTIONAL.includes(f));
    const need = editing ? required : [...required, 'user_id', 'password', 'activation_key'];
    if (!editing && isTrial) need.push('trial_days');
    const gaps = Object.fromEntries(need.filter((f) => !String(values[f]).trim()).map((f) => [f, [f === 'activation_key' ? 'Click Generate to create an activation key.' : REQUIRED_MESSAGE]]));
    if (!gaps.contact_number && !CONTACT.test(values.contact_number)) gaps.contact_number = [CONTACT_MESSAGE];
    if (!gaps.serial_number && !SERIAL.test(values.serial_number.trim())) gaps.serial_number = [SERIAL_MESSAGE];
    if (!gaps.email && !EMAIL.test(values.email.trim())) gaps.email = [EMAIL_MESSAGE];
    if (values.gstin.trim() && !GSTIN.test(values.gstin.trim().toUpperCase())) gaps.gstin = [GSTIN_MESSAGE]; // optional, but must be valid when given
    return gaps;
  }

  async function submit(event) {
    event.preventDefault();
    if (submittingRef.current) return; // a second click while the first Save / Approve is still running
    const gaps = missing();
    if (Object.keys(gaps).length) return setErrors(gaps);
    submittingRef.current = true;
    setSaving(true);
    setFormError('');
    setErrors({});
    try {
      const body = editing ? { ...Object.fromEntries(PROFILE.map((f) => [f, values[f]])), ...(values.password ? { password: values.password } : {}) } : { ...values, trial_days: isTrial ? Number(values.trial_days) : undefined, request_id: prefill?.id };
      const saved = editing ? await registrationsApi.update(project, customer.id, body) : await registrationsApi.register(project, body);
      onSaved(saved, editing);
    } catch (e) {
      if (e.status === 400 && e.errors?.serial_number && !editing) {
        // Taken since it was issued: swap in a fresh one rather than leave a read-only field stuck.
        if (await issueSerial()) setFormError('That serial number was just taken. A new one has been generated; please click Save again.');
      } else if (e.status === 400 && Object.keys(e.errors || {}).length) setErrors(e.errors);
      else setFormError(e.message);
    } finally {
      submittingRef.current = false;
      setSaving(false);
    }
  }

  const text = (field, label, extra = {}) => (
    <TextField compact label={label} value={values[field]} onChange={set(field)} error={err(field)} autoComplete="off" {...extra} />
  );

  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-label={editing ? 'Edit customer' : 'Add customer'}>
      {formError && <Alert type="error">{formError}</Alert>}

      {approving && <Alert type="success">Registration request from the Barcode app. Complete the details and click Save &amp; Approve; nothing is activated until then.</Alert>}
      <Section title="Customer Details">
        <TextField compact label="Project" value={projectBySlug(project)?.label ?? project} readOnly />
        {text('name', 'Name', { maxLength: 100, required: true })}
        {text('company_name', 'Company Name', { maxLength: 150 })}
        {text('contact_number', 'Contact Number', { maxLength: 10, inputMode: 'numeric', autoComplete: 'tel-national', onChange: setContact, required: true })}
        {text('email', 'Email ID', { maxLength: 254, type: 'email', inputMode: 'email', required: true })}
        {text('gstin', 'GST Details / GSTIN', { maxLength: 15, style: { textTransform: 'uppercase' } })}
        {text('address', 'Address', { maxLength: 500 })}
      </Section>

      <Section title="Login Details">
        {text('user_id', 'User ID', { maxLength: 60, required: !editing, readOnly: true, placeholder: editing ? undefined : 'Same as Serial Number' })}
        {(!editing || user?.role === 'SUPERADMIN') && <div>
        <PasswordField
          compact
          required={!editing}
          label={editing ? 'New Password' : 'Temporary Password'}
          value={values.password}
          onChange={set('password')}
          error={err('password')}
          hint={editing ? 'Leave blank to keep the current password' : undefined}
          autoComplete="new-password"
          maxLength={128}
        />
        {!editing && (
          <button type="button" className={styles.generate} onClick={generatePassword} disabled={generating}>
            Generate temporary password
          </button>
        )}
        </div>}
      </Section>

      <Section title="License Details">
        {!editing && (
          <>
            <SelectField compact label="License Type" value={values.license_type} onChange={set('license_type')} error={err('license_type')}>
              <option value="TRIAL">Trial</option>
              <option value="ONE_YEAR">1 Year</option>
              <option value="LICENSED">Lifetime</option>
            </SelectField>
            {text('serial_number', 'Serial Number', { maxLength: 9, readOnly: true, placeholder: 'Generating…', required: true })}
            {/* Beside Trial Days when shown; otherwise spans the row so no column sits empty. */}
            <div className={isTrial ? undefined : styles.full}>
              <TextField
                compact
                required
                label="Activation Key"
                value={values.activation_key}
                readOnly
                placeholder="Click Generate"
                error={err('activation_key')}
                trailing={
                  <button type="button" className={styles.generate} onClick={generate} disabled={generating}>
                    {generating ? 'Generating…' : 'Generate'}
                  </button>
                }
              />
            </div>
            {isTrial && (
              <TextField compact required label="Trial Days" type="number" min={1} max={365} value={values.trial_days} onChange={set('trial_days')} error={err('trial_days')} />
            )}
          </>
        )}
        {editing && text('serial_number', 'Serial Number', { maxLength: 9, inputMode: 'numeric', onChange: setSerial, required: true })}
      </Section>

      {editing && <p className={styles.note}>License type, activation key and User ID cannot be changed here.</p>}
      <div className={styles.actions}>
        <Button type="submit" loading={saving} loadingLabel="Saving…">
          {approving ? 'Save & Approve' : 'Save'}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
