import { useEffect, useState } from 'react';
import { configApi } from '../api/config';
import { useAuth } from '../context/AuthContext';
import Drawer from './Drawer';
import Alert from './Alert';
import Button from './Button';
import { PasswordField } from './Field';
import styles from './ConfigurationDrawer.module.css';

export default function ConfigurationDrawer({ open, onClose }) {
  const { refresh } = useAuth();
  const [data, setData] = useState(null);
  const [adminId, setAdminId] = useState('');
  const [grants, setGrants] = useState({});
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  useEffect(() => {
    if (!open) return;
    setError(''); setMessage(''); setPassword(''); setConfirmation('');
    configApi.projectPermissions().then((result) => {
      setData(result); setAdminId(String(result.admins[0]?.id || ''));
      setGrants(result.admins[0]?.grants || {});
    }).catch((e) => setError(e.message));
  }, [open]);
  function choose(id) {
    setAdminId(id); setGrants(data.admins.find((a) => String(a.id) === id)?.grants || {});
    setPassword(''); setConfirmation(''); setMessage('');
  }
  async function save() {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await configApi.saveProjectPermissions(Number(adminId), Object.entries(grants).map(([module, actions]) => ({
        module, actions: Object.entries(actions).filter(([, enabled]) => enabled).map(([action]) => action),
      })));
      setData(result); await refresh(); setMessage('Permissions saved. Admin sessions refresh within 10 seconds.');
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function resetPassword() {
    if (!password.trim() || password !== confirmation) { setError('Enter a new password and matching confirmation.'); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      await configApi.resetAdminPassword(adminId, password, confirmation);
      setPassword(''); setConfirmation(''); setMessage('Admin password changed. The Admin must sign in again.');
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return <Drawer open={open} onClose={onClose} title="Admin Permissions" titleId="config-drawer-title" closeLabel="Close configuration">
    <div className={styles.block}>
      {error && <Alert type="error">{error}</Alert>}
      {message && <p role="status">{message}</p>}
      {!data && !error && <p>Loading?</p>}
      {data && <>
        <label>Admin account <select value={adminId} onChange={(e) => choose(e.target.value)} disabled={busy}>
          {data.admins.map((a) => <option key={a.id} value={a.id}>{a.username}</option>)}
        </select></label>
        {!data.admins.length && <p>No active Admin accounts.</p>}
        {adminId && data.projects.map((project) => <details key={project.code}>
          <summary>+ {project.label}</summary>
          {project.menus.map((menu) => <fieldset key={menu.module} disabled={busy}>
            <legend>{menu.label}</legend>
            {menu.actions.map((action) => <label key={action} style={{ display: 'block', margin: '8px 0' }}>
              <input type="checkbox" checked={Boolean(grants[menu.module]?.[action])} onChange={(e) => {
                const checked = e.target.checked;
                setGrants((g) => ({ ...g, [menu.module]: { ...g[menu.module], [action]: checked } }));
              }} /> {data.actions[action]}
            </label>)}
          </fieldset>)}
        </details>)}
        <Button onClick={save} loading={busy} disabled={!adminId}>Save Permissions</Button>
        {adminId && <details><summary>Reset Admin Password</summary>
          <PasswordField label="New Password" value={password} maxLength={128} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          <PasswordField label="Confirm New Password" value={confirmation} maxLength={128} onChange={(e) => setConfirmation(e.target.value)} autoComplete="new-password" />
          <Button onClick={resetPassword} disabled={busy}>Reset Password</Button>
        </details>}
      </>}
    </div>
  </Drawer>;
}
