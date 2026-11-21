import { useState } from 'react';
import { configApi } from '../../api/config';
import PermissionEditor from './PermissionEditor';
import { EMPTY_PERM, toPayload } from './permissionModel';
import styles from './Config.module.css';

const BASE = import.meta.env.BASE_URL.replace(/\/$/, '');

/** Add Staff: details, projects and per-project permissions on ONE screen; a single Create Staff Account saves everything atomically. */
export default function StaffCreateForm({ catalog, labels, onCreated, onCancel }) {
  const [f, setF] = useState({ role: 'ADMIN', full_name: '', username: '', email: '', phone: '', is_active: true });
  const [perm, setPerm] = useState({ ...EMPTY_PERM });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fe, setFe] = useState({});
  const [done, setDone] = useState(null);
  const up = (patch) => setF((x) => ({ ...x, ...patch }));
  const err = (k) => fe[k] && <span className={styles.err}>{[].concat(fe[k]).join(' ')}</span>;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError(''); setFe({});
    try {
      const r = await configApi.createStaff({ ...f, ...toPayload(perm, f.role) });
      setDone({ message: r.message, link: `${window.location.origin}${BASE}${r.setup_link}`, name: f.full_name });
      await onCreated();
    } catch (x) {
      setError(x.message); setFe(x.errors || {});
    } finally { setBusy(false); }
  };

  if (done) {
    return (
      <div className={styles.form}>
        <div className={styles.notice} role="status">
          <strong>{done.name}&apos;s account was created.</strong>
          <p className={styles.muted}>{done.message}</p>
          <input readOnly value={done.link} onFocus={(e) => e.target.select()} aria-label="One-time setup link" />
        </div>
        <div><button type="button" className={styles.btn} onClick={onCancel}>Done</button></div>
      </div>
    );
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <section className={styles.section}>
        <h3>A. Staff Information</h3>
        <div className={styles.grid2}>
          <label className={styles.field}>Full Name<input value={f.full_name} onChange={(e) => up({ full_name: e.target.value })} required />{err('full_name')}</label>
          <label className={styles.field}>Username<input value={f.username} onChange={(e) => up({ username: e.target.value })} required autoComplete="off" />{err('username')}</label>
          <label className={styles.field}>Email Address<input type="email" value={f.email} onChange={(e) => up({ email: e.target.value })} required />{err('email')}</label>
          <label className={styles.field}>Contact Number<input value={f.phone} onChange={(e) => up({ phone: e.target.value })} />{err('phone')}</label>
          <div className={styles.field}>Role
            <span className={styles.seg} role="group" aria-label="Role">
              {[['ADMIN', 'Admin'], ['USER', 'User'], ['DEVELOPER', 'Developer']].map(([v, l]) => <button key={v} type="button" aria-pressed={f.role === v} className={`${styles.segBtn} ${f.role === v ? styles.segOn : ''}`} onClick={() => { up({ role: v }); setPerm({ ...EMPTY_PERM }); }}>{l}</button>)}
            </span>
          </div>
          <div className={styles.field}>Account Status
            <span className={styles.seg} role="group" aria-label="Status">
              {[[true, 'Active'], [false, 'Inactive']].map(([v, l]) => <button key={l} type="button" aria-pressed={f.is_active === v} className={`${styles.segBtn} ${f.is_active === v ? styles.segOn : ''}`} onClick={() => up({ is_active: v })}>{l}</button>)}
            </span>
          </div>
        </div>
        <p className={styles.muted} style={{ marginTop: 8 }}>No password is set or emailed. After saving you receive a one-time setup link for the new staff member.</p>
      </section>

      <section className={styles.section}>
        <h3>B. Project Access{f.role === 'ADMIN' ? ' & C. Feature and Action Permissions' : ' (Consolidated This Month Expiry Report only)'}</h3>
        <PermissionEditor role={f.role} catalog={catalog} labels={labels} value={perm} onChange={setPerm} disabled={busy} />
        {err('report_projects')}{err('module_grants')}{err('tally_grants')}
      </section>

      {error && <p role="alert" className={styles.err}>{error}</p>}
      <div className={styles.footer} style={{ margin: '0 -16px -16px' }}>
        <span className={styles.grow}>{f.role === 'ADMIN' ? `${perm.assigned.length} project(s) with access` : `${perm.assigned.length} project(s) in the report`}</span>
        <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className={styles.btn} disabled={busy || (f.role !== 'ADMIN' && perm.assigned.length === 0)}>{busy ? 'Creating…' : 'Create Staff Account'}</button>
      </div>
    </form>
  );
}
