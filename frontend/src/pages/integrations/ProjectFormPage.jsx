import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import styles from './Integrations.module.css';

// Defaults for a non-Tally product (no Tally serial; Nexus generates a 9-digit serial that is also the User ID).
const DEFAULTS = {
  name: '', code: '', frontend_url: '', backend_url: '', auth_method: 'JWT Bearer', framework: 'FastAPI', registration_method: 'API',
  tally_serial_required: false, serial_prefix: '9', serial_length: 9, activation_key_length: 9, device_limit: 1, default_license_type: 'TRIAL',
  trial_days: 30, transfer_policy: 'SELF', auto_sync: true, usage_tracking: true,
};

/** Project Management → Add / Connect Project. On save the credentials are shown ONCE on the integration screen. */
export default function ProjectFormPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState(DEFAULTS);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

  const field = (k, label, props = {}, hint) => (
    <label className={styles.field}>{label}
      <input value={form[k]} onChange={set(k)} {...props} />
      {hint && <small>{hint}</small>}
      {errors[k] && <span className={styles.err}>{errors[k][0]}</span>}
    </label>
  );
  const select = (k, label, options, disabled) => (
    <label className={styles.field}>{label}
      <select value={form[k]} onChange={set(k)} disabled={disabled}>{options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      {errors[k] && <span className={styles.err}>{errors[k][0]}</span>}
    </label>
  );

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErrors({}); setMessage('');
    try {
      const d = await integrationsApi.createProject({ ...form, code: form.code.toUpperCase() });
      navigate(`/project-management/${d.project.code}/integration`, { state: { credentials: d.credentials, fresh: true } });
    } catch (err) {
      setErrors(err.errors || {}); setMessage(err.message);
    } finally { setBusy(false); }
  }

  return (
    <section className={styles.page} aria-label="Add or connect a project">
      <form className={styles.panel} onSubmit={submit} noValidate>
        <div className={styles.head}><div><h2 className={styles.title}>Add / Connect Project</h2><p className={styles.sub}>Saving creates the project in <b>Setup Pending</b>, generates scoped API credentials and shows the integration instructions.</p></div></div>
        {message && <Alert type="error">{message}</Alert>}
        <div className={styles.grid}>
          {field('name', 'Project Name', { required: true })}
          {field('code', 'Project Code', { required: true, placeholder: 'MOTHYS' }, 'Capital letters, digits, underscore')}
          {field('frontend_url', 'Frontend URL', { type: 'url', placeholder: 'https://…' })}
          {field('backend_url', 'Backend API Base URL', { type: 'url', placeholder: 'https://…/api' })}
          {select('auth_method', 'Authentication Method', [['JWT Bearer', 'JWT Bearer'], ['Session Cookie', 'Session Cookie'], ['API Key', 'API Key']])}
          {field('framework', 'Framework')}
          {select('registration_method', 'Customer Registration Method', [['API', 'Automatic via API'], ['BULK', 'Bulk import + API']])}
          {select('tally_serial_required', 'Tally Serial Required', [[false, 'No (Nexus generates serial)']], true)}
          {field('serial_prefix', 'Serial Prefix', { maxLength: 3 })}
          {field('serial_length', 'Serial Length', { type: 'number', min: 6, max: 18 })}
          {field('activation_key_length', 'Activation Key Length', { type: 'number', min: 6, max: 12 })}
          {field('device_limit', 'Device Limit', { type: 'number', min: 1, max: 20 })}
          {select('default_license_type', 'Default License Type', [['TRIAL', 'Trial'], ['ONE_YEAR', '1 Year (needs approval)'], ['LIFETIME', 'Lifetime (needs approval)']])}
          {field('trial_days', 'Trial Days', { type: 'number', min: 1, max: 365 })}
          {select('transfer_policy', 'Device Transfer Policy', [['SELF', 'Enabled – customer confirms on new device'], ['DISABLED', 'Disabled']])}
          <label className={`${styles.field} ${styles.check}`}><input type="checkbox" checked={form.auto_sync} onChange={set('auto_sync')} /> Customer Auto Sync</label>
          <label className={`${styles.field} ${styles.check}`}><input type="checkbox" checked={form.usage_tracking} onChange={set('usage_tracking')} /> Usage Tracking</label>
        </div>
        <p className={styles.note}>User ID = Serial Number · Activation key email: on request only · Paid licences are never auto-activated.</p>
        <div className={styles.actions}><button className={styles.btn} disabled={busy}>{busy ? 'Saving…' : 'Save & Generate Credentials'}</button></div>
      </form>
    </section>
  );
}
