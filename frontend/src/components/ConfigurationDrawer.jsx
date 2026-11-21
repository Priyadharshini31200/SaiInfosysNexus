import { useCallback, useEffect, useMemo, useState } from 'react';
import { configApi } from '../api/config';
import { useAuth } from '../context/AuthContext';
import Drawer from './Drawer';
import AccessTab from './config/AccessTab';
import StaffCreateForm from './config/StaffCreateForm';
import StaffDirectoryTab from './config/StaffDirectoryTab';
import VisibilityTab from './config/VisibilityTab';
import { SettingsIcon } from './Icons';
import styles from './config/Config.module.css';

const BASE = import.meta.env.BASE_URL.replace(/\/$/, '');
const TABS = [['directory', 'Staff Directory'], ['access', 'Access & Permissions'], ['visibility', 'Dashboard Visibility']];

/** Configuration workspace (Superadmin): a wide drawer with three separate tabs. `mode="create"` opens Add Staff directly.
 *  All permission writes go through the existing staff API, which stores the existing per-project grant tables. */
export default function ConfigurationDrawer({ open, mode, onClose }) {
  const { user } = useAuth();
  const isSuper = user?.role === 'SUPERADMIN';
  const allowed = isSuper || (user?.role === 'ADMIN' && user.visibility_projects?.length > 0); // an Admin sees only Dashboard Visibility, for granted projects
  const [view, setView] = useState('directory'); // directory | access | visibility | create
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    try { setData(await configApi.staff()); setError(''); } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => {
    if (!open || !allowed) return;
    setView(!isSuper ? 'visibility' : mode === 'create' ? 'create' : 'directory');
    setSelected(null); setDirty(false); setNotice(null);
    if (isSuper) load();
  }, [open, mode, allowed, isSuper, load]);

  const projectNames = useMemo(() => Object.fromEntries((data?.projects || []).map((p) => [p.code, p.name])), [data]);
  const confirmLeave = () => !dirty || window.confirm('You have unsaved permission changes. Discard them?');
  const go = (v) => { if (v !== view && !confirmLeave()) return; setDirty(false); setView(v); };
  const close = () => { if (confirmLeave()) onClose(); };

  const edit = (u) => { if (!confirmLeave()) return; setDirty(false); setSelected(u.id); setView('access'); };
  const toggle = async (u) => {
    try {
      await configApi.updateStaff(u.id, { full_name: u.full_name, email: u.email, phone: u.phone, is_active: !u.is_active, module_grants: u.module_grants, tally_grants: u.tally_grants, visibility_projects: u.visibility_projects, report_projects: u.report_projects });
      await load();
    } catch (e) { setError(e.message); }
  };
  const link = async (u) => {
    try {
      const r = await configApi.staffSetupLink(u.id);
      setNotice({ message: `One-time setup link for ${u.username}. Share it securely; it stops working once used.`, link: `${window.location.origin}${BASE}${r.setup_link}` });
    } catch (e) { setError(e.message); }
  };

  if (!allowed) return null;
  return (
    <Drawer open={open} onClose={close} wide flush titleId="config-drawer-title" closeLabel="Close configuration"
      title={<span style={{ display: 'flex', alignItems: 'center', gap: 10 }}><SettingsIcon size={21} /><span>{view === 'create' ? 'Add Staff' : 'Configuration'}<small style={{ display: 'block', fontSize: 11, fontWeight: 400, color: 'var(--muted)' }}>{view === 'create' ? 'Create an Admin or restricted User account' : 'Manage staff access, permissions and dashboard settings.'}</small></span></span>}>
      <div className={styles.workspace}>
        {view !== 'create' && (
          <div className={styles.tabs} role="tablist" aria-label="Configuration sections">
            {TABS.filter(([id]) => isSuper || id === 'visibility').map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={view === id} className={`${styles.tab} ${view === id ? styles.tabOn : ''}`} onClick={() => go(id)}>{label}</button>
            ))}
          </div>
        )}
        <div className={styles.body}>
          {error && <p role="alert" className={styles.err}>{error}</p>}
          {notice && (
            <div className={styles.notice} role="status">
              {notice.message}
              <input readOnly value={notice.link} onFocus={(e) => e.target.select()} aria-label="One-time setup link" />
            </div>
          )}
          {view === 'directory' && <StaffDirectoryTab staff={data?.staff} projectNames={projectNames} onEdit={edit} onToggle={toggle} onLink={link} onAdd={() => go('create')} />}
          {view === 'access' && data && <AccessTab staff={data.staff} catalog={data.catalog} labels={data.action_labels} selectedId={selected} onSelect={setSelected} onSaved={load} onDirty={setDirty} />}
          {view === 'visibility' && <VisibilityTab active />}
          {view === 'create' && data && <StaffCreateForm catalog={data.catalog} labels={data.action_labels} onCreated={load} onCancel={() => (mode === 'create' ? onClose() : setView('directory'))} />}
        </div>
      </div>
    </Drawer>
  );
}
