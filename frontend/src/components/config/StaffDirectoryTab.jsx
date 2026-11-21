import { useEffect, useMemo, useRef, useState } from 'react';
import styles from './Config.module.css';

function RowMenu({ user, onEdit, onToggle, onLink }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (e.type === 'keydown' ? e.key === 'Escape' : !ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  const run = (fn) => () => { setOpen(false); fn(user); };
  return (
    <span className={styles.rowMenu} ref={ref}>
      <button type="button" className={styles.rowBtn} aria-haspopup="menu" aria-expanded={open} aria-label={`Actions for ${user.full_name}`} onClick={() => setOpen((o) => !o)}>Actions ▾</button>
      {open && (
        <div className={styles.menu} role="menu">
          <button type="button" role="menuitem" className={styles.menuItem} onClick={run(onEdit)}>View / Edit</button>
          <button type="button" role="menuitem" className={styles.menuItem} onClick={run(onToggle)}>{user.is_active ? 'Deactivate' : 'Activate'}</button>
          <button type="button" role="menuitem" className={styles.menuItem} onClick={run(onLink)}>Send Setup Link</button>
        </div>
      )}
    </span>
  );
}

/** Compact staff table with search and Role / Status filters and one row-action menu (no clipped or stacked buttons). */
export default function StaffDirectoryTab({ staff, projectNames, onEdit, onToggle, onLink, onAdd }) {
  const [q, setQ] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (staff || []).filter((u) => (!role || u.role === role) && (!status || (status === 'active') === u.is_active)
      && (!s || [u.full_name, u.username, u.email].some((v) => (v || '').toLowerCase().includes(s))));
  }, [staff, q, role, status]);

  return (
    <>
      <div className={styles.toolbar}>
        <input className={styles.search} type="search" placeholder="Search name, username or email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search staff" />
        <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Filter by role"><option value="">All roles</option><option value="ADMIN">Admin</option><option value="USER">User</option></select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status"><option value="">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option></select>
        <button type="button" className={styles.btn} onClick={onAdd}>+ Add Staff</button>
      </div>
      {!staff && <p className={styles.muted}>Loading…</p>}
      {staff && rows.length === 0 && <p className={styles.empty}>{staff.length ? 'No staff match the filters.' : 'No Admin or User accounts yet.'}</p>}
      {rows.length > 0 && (
        <table className={styles.table}>
          <thead><tr><th className={styles.cName}>Staff Name</th><th className={styles.cUser}>Username</th><th className={styles.cRole}>Role</th><th className={styles.cProj}>Assigned Projects</th><th className={styles.cStat}>Status</th><th className={styles.cLast}>Last Login</th><th className={styles.cAct}>Actions</th></tr></thead>
          <tbody>
            {rows.map((u) => {
              const names = u.projects.map((c) => projectNames[c] || c).join(', ') || '—';
              return (
                <tr key={u.id}>
                  <td className={styles.cName} data-label="Name" title={u.full_name}>{u.full_name}</td>
                  <td className={styles.cUser} data-label="Username">{u.username}</td>
                  <td className={styles.cRole} data-label="Role">{u.role === 'USER' ? 'User' : 'Admin'}</td>
                  <td className={styles.cProj} data-label="Projects" title={names}>{names}</td>
                  <td className={styles.cStat} data-label="Status"><span className={`${styles.status} ${u.is_active ? styles.sOn : styles.sOff}`}>{u.is_active ? 'Active' : 'Inactive'}</span></td>
                  <td className={styles.cLast} data-label="Last login">{u.last_login ? new Date(u.last_login).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }) : 'Never'}</td>
                  <td className={styles.cAct}><RowMenu user={u} onEdit={onEdit} onToggle={onToggle} onLink={onLink} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
