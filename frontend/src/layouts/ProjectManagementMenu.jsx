import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronDownIcon, FolderIcon } from '../components/Icons';
import { useAuth } from '../context/AuthContext';
import styles from './Sidebar.module.css';

export const PM_PAGES = [
  { label: 'Project List', path: '/project-management', end: true },
  { label: 'Add / Connect Project', path: '/project-management/add' },
  { label: 'API Integration Status', path: '/project-management/status' },
];

/** Super Admin only: Project Management (Universal Licensing API projects). The API enforces the role too. */
export default function ProjectManagementMenu({ collapsed, onNavigate }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const [toggled, setToggled] = useState(null);
  if (user?.role !== 'SUPERADMIN') return null;
  const inside = pathname.startsWith('/project-management');
  const open = toggled ?? inside;
  return (
    <>
      <p className={`${styles.sectionLabel} ${styles.projectsLabel}`}>Project Management</p>
      <ul className={styles.list}>
        <li>
          {collapsed ? (
            <NavLink to="/project-management" onClick={onNavigate} title="Project Management" className={() => `${styles.link} ${inside ? styles.active : ''}`}>
              <FolderIcon size={20} /><span className={styles.linkLabel}>Project Management</span>
            </NavLink>
          ) : (
            <>
              <button type="button" className={`${styles.link} ${styles.parent} ${inside ? styles.parentActive : ''}`} onClick={() => setToggled(!open)} aria-expanded={open} aria-controls="submenu-pm">
                <FolderIcon size={20} /><span className={styles.linkLabel}>Project Management</span>
                <ChevronDownIcon size={16} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} />
              </button>
              {open && (
                <ul id="submenu-pm" className={styles.sub}>
                  {PM_PAGES.map((page) => (
                    <li key={page.path}>
                      <NavLink to={page.path} end={page.end} onClick={onNavigate} className={({ isActive }) => `${styles.subLink} ${isActive ? styles.subActive : ''}`}>{page.label}</NavLink>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </li>
      </ul>
    </>
  );
}
