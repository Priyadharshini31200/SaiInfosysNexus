import { useCallback, useEffect, useRef, useState } from 'react';
import { configApi } from '../api/config';
import { tallyApi } from '../api/tallysl';
import { useAuth } from '../context/AuthContext';
import Drawer from './Drawer';
import Alert from './Alert';
import { PasswordField } from './Field';
import { SettingsIcon, FolderIcon, KeyIcon, UserIcon } from './Icons';
import styles from './ConfigurationDrawer.module.css';

export default function ConfigurationDrawer({ open, onClose }) {
  const { user, refresh } = useAuth();
  const [data, setData] = useState(null);
  const [adminId, setAdminId] = useState('');
  const [grants, setGrants] = useState({});
  const [tally, setTally] = useState(null); // Tally SL products + per-product grants (apps/tallysl); PRN grants above are untouched
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const locked = useRef(false);
  const requestId = useRef(0);
  const allowed = user?.role === 'SUPERADMIN';
  const close = useCallback(() => { if (!locked.current) onClose(); }, [onClose]);

  useEffect(() => {
    if (!open || !allowed) return;
    const token = ++requestId.current;
    setData(null); setGrants({}); setAdminId(''); setLoading(true);
    setError(''); setMessage(''); setPassword(''); setConfirmation('');
    tallyApi.permissions().then((t) => token === requestId.current && setTally(t)).catch(() => token === requestId.current && setTally(null));
    configApi.projectPermissions().then((result) => {
      if (token !== requestId.current) return;
      setData(result); setAdminId(String(result.admins[0]?.id || ''));
      setGrants(result.admins[0]?.grants || {});
    }).catch((e) => { if (token === requestId.current) setError(e.message); })
      .finally(() => { if (token === requestId.current) setLoading(false); });
    return () => { requestId.current++; };
  }, [open, allowed]);

  async function choose(id) {
    if (locked.current) return;
    const token = ++requestId.current;
    setAdminId(id); setGrants({}); setLoading(true);
    setPassword(''); setConfirmation(''); setMessage(''); setError('');
    try {
      const result = await configApi.projectPermissions();
      if (token !== requestId.current) return;
      const admin = result.admins.find((a) => String(a.id) === id);
      setData(result); setGrants(admin?.grants || {});
      if (!admin) { setAdminId(''); setError('This Admin account is no longer available.'); }
    } catch (e) {
      if (token === requestId.current) { setError(e.message); setAdminId(''); }
    } finally { if (token === requestId.current) setLoading(false); }
  }

  async function toggle(module, action, checked) {
    if (locked.current || loading || !adminId || !allowed) return;
    locked.current = true;
    const previous = grants;
    const next = { ...grants, [module]: { ...grants[module], [action]: checked } };
    setGrants(next); setBusy(true); setError(''); setMessage('');
    try {
      const result = await configApi.saveProjectPermissions(Number(adminId), Object.entries(next).map(([scope, actions]) => ({
        module: scope, actions: Object.entries(actions).filter(([, enabled]) => enabled).map(([name]) => name),
      })));
      const saved = result.admins.find((a) => String(a.id) === adminId);
      if (!saved) throw new Error('Admin account is no longer available. Reopen permissions to reload.');
      setData(result); setGrants(saved.grants || {});
      setMessage('Saved');
      await refresh();
    } catch (e) { setGrants(previous); setError(`Could not save permissions. ${e.message}`); }
    finally { locked.current = false; setBusy(false); }
  }

  const tallyAdmin = tally?.admins.find((a) => String(a.id) === adminId);
  async function toggleTally(code, action, checked) {
    if (locked.current || loading || !adminId || !allowed) return;
    locked.current = true;
    const current = tallyAdmin?.grants[code] || {};
    const actions = Object.keys(tally.actions).filter((k) => (k === action ? checked : current[k]));
    setBusy(true); setError(''); setMessage('');
    try {
      setTally(await tallyApi.savePermissions(Number(adminId), { [code]: actions }));
      setMessage('Saved');
    } catch (e) { setError(`Could not save permissions. ${e.message}`); }
    finally { locked.current = false; setBusy(false); }
  }

  async function resetPassword() {
    if (locked.current || loading || !adminId || !allowed) return;
    if (!password.trim() || password !== confirmation) { setError('Enter a new password and matching confirmation.'); return; }
    locked.current = true;
    setBusy(true); setError(''); setMessage('');
    try {
      await configApi.resetAdminPassword(adminId, password, confirmation);
      setPassword(''); setConfirmation(''); setMessage('Password changed. The Admin must sign in again.');
    } catch (e) { setError(e.message); } finally { locked.current = false; setBusy(false); }
  }

  if (!allowed) return null;
  return <Drawer open={open} onClose={close} title={<span className={styles.heading}><SettingsIcon size={21} /><span>Admin Permissions<small>Manage project-wise access</small></span></span>} titleId="config-drawer-title" closeLabel={busy ? 'Please wait for saving to finish' : 'Close configuration'} flush>
    <div className={styles.block}>
      {error && <Alert type="error">{error}</Alert>}
      <div className={styles.account}>
        <label htmlFor="permission-admin">Select Admin</label>
        <div className={styles.selectWrap}><UserIcon size={18} /><select id="permission-admin" value={adminId} onChange={(e) => choose(e.target.value)} disabled={busy || loading || !data?.admins.length}>
          {!adminId && <option value="">{loading ? 'Loading Admin accounts...' : 'Choose an Admin'}</option>}
          {data?.admins.map((a) => <option key={a.id} value={a.id}>{a.username}</option>)}
        </select></div>
        <p>Access is saved automatically for the selected Admin.</p>
      </div>
      {loading && <div className={styles.loading} role="status"><span className={styles.spinner} /> Loading saved permissions...</div>}
      {!loading && data && !data.admins.length && <p className={styles.empty}>No active Admin accounts.</p>}
      {!loading && adminId && data?.projects.filter((p) => !tally?.products.some((t) => t.code === p.code)).map((project) => {
        const enabled = project.menus.reduce((count, menu) => count + menu.actions.filter((action) => grants[menu.module]?.[action]).length, 0);
        const total = project.menus.reduce((count, menu) => count + menu.actions.length, 0);
        return <details className={styles.project} key={project.code}>
          <summary><span className={styles.projectIcon}><FolderIcon size={18} /></span><span className={styles.projectName}>{project.label}</span><span className={styles.count}>{enabled}/{total}</span><span className={styles.chevron} aria-hidden="true" /></summary>
          <div className={styles.matrix}>
            <div className={styles.columns}><span>Menu / page</span><span>Allowed actions</span></div>
            {project.menus.map((menu) => {
              const parts = menu.label.split(' / ');
              const nested = parts[0] === 'Reports';
              return <div className={styles.menu} key={menu.module}>
                <div className={styles.menuName}>{nested && <small>{parts[0]}</small>}<span>{nested ? `\u21b3 ${parts.slice(1).join(' / ')}` : menu.label}</span></div>
                <fieldset disabled={busy} className={styles.actions}>
                  <legend className={styles.srOnly}>{project.label}: {menu.label}</legend>
                  {menu.actions.map((action) => <label key={action} className={styles.permission}>
                    <input type="checkbox" checked={Boolean(grants[menu.module]?.[action])} onChange={(e) => toggle(menu.module, action, e.target.checked)} />
                    <span>{data.actions[action] || action}</span>
                  </label>)}
                </fieldset>
              </div>;
            })}
          </div>
        </details>;
      })}
      {!loading && adminId && tally?.products.map((product) => {
        const g = tallyAdmin?.grants[product.code] || {};
        const total = Object.keys(tally.actions).length;
        return <details className={styles.project} key={`tally-${product.code}`}>
          <summary><span className={styles.projectIcon}><FolderIcon size={18} /></span><span className={styles.projectName}>{product.name}</span><span className={styles.count}>{Object.values(g).filter(Boolean).length}/{total}</span><span className={styles.chevron} aria-hidden="true" /></summary>
          <div className={styles.matrix}>
            <div className={styles.columns}><span>Product</span><span>Allowed actions</span></div>
            <div className={styles.menu}>
              <div className={styles.menuName}><span>{product.name}</span></div>
              <fieldset disabled={busy} className={styles.actions}>
                <legend className={styles.srOnly}>{product.name} permissions</legend>
                {Object.entries(tally.actions).map(([action, label]) => <label key={action} className={styles.permission}>
                  <input type="checkbox" checked={Boolean(g[action])} onChange={(e) => toggleTally(product.code, action, e.target.checked)} />
                  <span>{label}</span>
                </label>)}
              </fieldset>
            </div>
          </div>
        </details>;
      })}
      {!loading && adminId && <details className={styles.password}>
        <summary><KeyIcon size={18} /><span>Reset Admin Password</span><span className={styles.chevron} aria-hidden="true" /></summary>
        <div className={styles.passwordBody}>
          <p>Changing the password signs this Admin out of existing sessions.</p>
          <PasswordField compact label="New Password" value={password} maxLength={128} disabled={busy} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          <PasswordField compact label="Confirm New Password" value={confirmation} maxLength={128} disabled={busy} onChange={(e) => setConfirmation(e.target.value)} autoComplete="new-password" />
          <button className={styles.reset} type="button" onClick={resetPassword} disabled={busy || !password || !confirmation}><KeyIcon size={15} /> Reset password</button>
        </div>
      </details>}
    </div>
    <div className={styles.footer} role="status" aria-live="polite">
      {busy ? <><span className={styles.spinner} /> Saving... Please keep this drawer open.</> : message ? <span className={styles.success}>{'\u2713'} {message}</span> : 'Only explicitly assigned actions are allowed.'}
    </div>
  </Drawer>;
}
