import { useEffect, useMemo, useState } from 'react';
import { configApi } from '../../api/config';
import PermissionEditor from './PermissionEditor';
import { fingerprint, fromStaff, toPayload } from './permissionModel';
import styles from './Config.module.css';

/** Access & Permissions: Select Staff -> pick a project on the left -> set its permissions on the right -> one Save Changes. */
export default function AccessTab({ staff, catalog, labels, selectedId, onSelect, onSaved, onDirty }) {
  const user = (staff || []).find((u) => u.id === selectedId) || null;
  const [perm, setPerm] = useState(fromStaff(null));
  const [details, setDetails] = useState({ full_name: '', email: '', phone: '', is_active: true });
  const [base, setBase] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ok, setOk] = useState('');
  const [pw, setPw] = useState({ a: '', b: '' });

  // (Re)load the editor from the saved record whenever a different account is chosen or the record is refreshed after saving.
  useEffect(() => {
    const p = fromStaff(user);
    setPerm(p);
    setDetails(user ? { full_name: user.full_name, email: user.email, phone: user.phone, is_active: user.is_active } : { full_name: '', email: '', phone: '', is_active: true });
    setBase(fingerprint(p) + JSON.stringify(user ? { n: user.full_name, e: user.email, p: user.phone, a: user.is_active } : {}));
    setError('');
  }, [user]);

  const now = fingerprint(perm) + JSON.stringify({ n: details.full_name, e: details.email, p: details.phone, a: details.is_active });
  const dirty = !!user && now !== base;
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const manageable = useMemo(() => staff || [], [staff]);
  const choose = (id) => {
    if (dirty && !window.confirm('You have unsaved changes. Discard them and switch account?')) return;
    onSelect(id ? Number(id) : null);
  };
  const discard = () => {
    const p = fromStaff(user);
    setPerm(p);
    setDetails({ full_name: user.full_name, email: user.email, phone: user.phone, is_active: user.is_active });
    setError(''); setOk('');
  };
  const save = async () => {
    setBusy(true); setError(''); setOk('');
    try {
      await configApi.updateStaff(user.id, { ...details, ...toPayload(perm, user.role) });
      setOk('Changes saved. They apply to this account\'s next request.');
      await onSaved();
    } catch (e) {
      setError(Object.values(e.errors || {}).flat().join(' ') || e.message);
    } finally { setBusy(false); }
  };
  const reset = async () => {
    if (!pw.a.trim() || pw.a !== pw.b) { setError('Enter a new password and a matching confirmation.'); return; }
    try { await configApi.resetAdminPassword(user.id, pw.a, pw.b); setPw({ a: '', b: '' }); setOk('Password changed. The Admin must sign in again.'); setError(''); } catch (e) { setError(e.message); }
  };

  return (
    <>
      <div className={styles.picker}>
        <label className={styles.field}>Select Staff Account
          <select value={selectedId || ''} onChange={(e) => choose(e.target.value)} disabled={busy}>
            <option value="">{staff ? 'Choose a staff account' : 'Loading…'}</option>
            {manageable.map((u) => <option key={u.id} value={u.id}>{u.full_name} ({u.username})</option>)}
          </select>
        </label>
        {user && <div className={styles.summary}><strong>{user.full_name}</strong> · {user.role === 'USER' ? 'User (Report Viewer)' : 'Admin'} · {user.is_active ? 'Active' : 'Inactive'} · {perm.assigned.length} project{perm.assigned.length === 1 ? '' : 's'} assigned</div>}
      </div>

      {!user && <p className={styles.note}>Select a staff account to review and change its project access.</p>}
      {user && (
        <>
          <details>
            <summary className={styles.link}>Edit details &amp; status</summary>
            <div className={`${styles.grid2} ${styles.section}`}>
              <label className={styles.field}>Full Name<input value={details.full_name} onChange={(e) => setDetails({ ...details, full_name: e.target.value })} /></label>
              <label className={styles.field}>Email<input type="email" value={details.email} onChange={(e) => setDetails({ ...details, email: e.target.value })} /></label>
              <label className={styles.field}>Contact Number<input value={details.phone} onChange={(e) => setDetails({ ...details, phone: e.target.value })} /></label>
              <label className={styles.check}><input type="checkbox" checked={details.is_active} onChange={(e) => setDetails({ ...details, is_active: e.target.checked })} /> Account active</label>
            </div>
            {user.role === 'ADMIN' && (
              <div className={`${styles.grid2} ${styles.section}`}>
                <label className={styles.field}>New password<input type="password" autoComplete="new-password" value={pw.a} onChange={(e) => setPw({ ...pw, a: e.target.value })} /></label>
                <label className={styles.field}>Confirm password<input type="password" autoComplete="new-password" value={pw.b} onChange={(e) => setPw({ ...pw, b: e.target.value })} /></label>
                <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={reset} disabled={!pw.a}>Reset Admin password</button>
              </div>
            )}
          </details>
          <PermissionEditor role={user.role} catalog={catalog} labels={labels} value={perm} onChange={setPerm} disabled={busy} />
        </>
      )}
      {error && <p role="alert" className={styles.err}>{error}</p>}
      {ok && <p role="status" className={styles.ok}>{ok}</p>}
      {user && (
        <div className={styles.footer} style={{ margin: '0 -16px -16px' }}>
          <span className={`${styles.grow} ${dirty ? styles.dirty : ''}`}>{dirty ? 'You have unsaved changes.' : 'Nothing changed.'}</span>
          <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={discard} disabled={!dirty || busy}>Discard Changes</button>
          <button type="button" className={styles.btn} onClick={save} disabled={!dirty || busy}>{busy ? 'Saving…' : 'Save Changes'}</button>
        </div>
      )}
    </>
  );
}
