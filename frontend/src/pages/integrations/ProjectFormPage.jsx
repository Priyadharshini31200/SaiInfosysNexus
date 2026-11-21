import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import CopyValue from '../../components/CopyValue';
import { stageProjectSecret } from './projectSecretHandoff';
import { useSecretRequestGuard } from './useProjectSecret';
import styles from './Integrations.module.css';
import form from './ProjectForm.module.css';

const DEFAULTS = {
  name: '', tally_serial_required: false, developer_email: '', description: '', framework: '',
  frontend_url: '', backend_url: '', customer_path: '/customers', auth_method: 'JWT Bearer',
  default_license_type: 'TRIAL', trial_days: 3, validity_years: 1, transfer_policy: 'SELF', auto_sync: true, usage_tracking: true,
};
const clean = (v) => v.replace(/\s+/g, ' ').toUpperCase(); // keeps one trailing space so typing "PMP RICE" works; trimmed on submit
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** UNIVERSE -> Projects -> Add / Connect Project. Three sections; the Project Code is ALLOCATED BY THE SERVER (the preview here is only a hint). Saves as Setup Pending
 *  and continues to the API Integration workspace with the new project preselected. */
export default function ProjectFormPage() {
  const navigate = useNavigate();
  const revision = useSecretRequestGuard();
  const [qs] = useSearchParams();
  const category = qs.get('category'); // 'tally' | 'non-tally' from the originating directory; locked (read-only)
  const locked = category === 'tally' || category === 'non-tally';
  const [f, setF] = useState({ ...DEFAULTS, tally_serial_required: category === 'tally' });
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState('');
    const name = f.name.trim();
  const set = (k, transform) => (e) => setF((s) => ({ ...s, [k]: transform ? transform(e.target.value) : e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

  useEffect(() => {
    if (!name) { setPreview(''); return undefined; }
    let cur = true;
    const t = setTimeout(() => integrationsApi.codePreview(name).then((d) => cur && setPreview(d.code), () => cur && setPreview('')), 250);
    return () => { cur = false; clearTimeout(t); };
  }, [name]);

  const field = (k, label, props = {}, hint, req) => (
    <label className={form.field}><span>{label}{req && <b className={form.req}> *</b>}</span>
      <input value={f[k]} aria-invalid={!!errors[k]} {...props} onChange={props.onChange || set(k)} />
      {hint && <small>{hint}</small>}{errors[k] && <span className={styles.err}>{[].concat(errors[k])[0]}</span>}
    </label>
  );
  const select = (k, label, options, req) => (
    <label className={form.field}><span>{label}{req && <b className={form.req}> *</b>}</span>
      <select value={String(f[k])} onChange={(e) => setF((s) => ({ ...s, [k]: ['tally_serial_required'].includes(k) ? e.target.value === 'true' : e.target.value }))}>{options.map(([v, l]) => <option key={String(v)} value={String(v)}>{l}</option>)}</select>
      {errors[k] && <span className={styles.err}>{[].concat(errors[k])[0]}</span>}
    </label>
  );
  const ro = (label, value) => <div className={form.field}><span>{label}</span><input value={value} readOnly tabIndex={-1} className={form.ro} aria-label={label} /></div>;

  async function submit(e) {
    e.preventDefault();
    const err = {};
    if (!name) err.name = 'Project Name is required.';
    if ((!locked || category === 'tally' || f.developer_email.trim()) && !EMAIL.test(f.developer_email.trim())) err.developer_email = 'Developer Email is invalid.';
    setErrors(err);
    if (Object.keys(err).length) return;
    setBusy(true); setMessage('');
    const request = revision.current;
    try {
      const d = await integrationsApi.createProject({ name, tally_serial_required: locked ? (category === 'tally' ? f.tally_serial_required : false) : f.tally_serial_required, ...(category === 'tally' ? { category: 'tally' } : {}), developer_email: f.developer_email.trim() });
      if (revision.current !== request) return;
      stageProjectSecret(d.project.code, d.credentials.api_secret);
      if (d.project.manual) { navigate(`/universe/projects/${d.project.code}/customers`, { state: { created: d.project.code, crumb: 'Non-Tally SL Projects', project: d.project.name, page: 'Customer List' } }); return; } // manual Non-Tally: no API step
      navigate(`/universe/api?project=${d.project.code}`, { state: { created: d.project.code } });
    } catch (x) {
      const fe = x.errors || {};
      setErrors(Object.fromEntries(Object.entries(fe).map(([k, v]) => [k === 'code' ? 'name' : k, v])));
      setMessage(x.message);
    } finally { setBusy(false); }
  }

  return (
    <section className={styles.page} aria-label="Add or connect a project">
      <form className={`${styles.panel} ${form.form}`} onSubmit={submit} noValidate>
        <div className={styles.head}><div><h2 className={styles.title}>Add / Connect Project</h2></div></div>
        {message && <Alert type="error">{message}</Alert>}

        <div className={form.grid}>
          {field('name', 'Project Name', { placeholder: 'e.g. PMP RICE MILL', maxLength: 120, autoComplete: 'off', onChange: (e) => { const el = e.target; const pos = el.selectionStart; const v = clean(el.value); setF((s) => ({ ...s, name: v })); requestAnimationFrame(() => el.setSelectionRange(pos, pos)); } }, undefined, true)}
          <div className={form.field}><span>Project Code – Auto Generated</span><CopyValue value={preview} label="Project code" /></div>
          {category === 'non-tally'
            ? ro('Project Category', 'Non-Tally SL Project (manual customers)')
            : select('tally_serial_required', 'Tally Integration Required', [[true, 'Yes – verify Tally Serial, Edition, TSS and Administrator'], [false, 'No – API customers without Tally verification']], true)}
          {field('developer_email', 'Developer Email', { type: 'email', placeholder: 'developer@company.com' }, undefined, !locked || category === 'tally')}
        </div>

        <div className={form.bar}>
          <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={() => navigate(locked ? `/directory/${category}` : '/directory/tally')}>Cancel</button>
          <button className={styles.btn} disabled={busy}>{busy ? 'Saving…' : 'Save Project & Continue'}</button>
        </div>
      </form>
    </section>
  );
}
