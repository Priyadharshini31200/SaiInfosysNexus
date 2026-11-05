import { NavLink } from 'react-router-dom';
import { ChevronsLeftIcon } from '../components/Icons';
import { NAV_ITEMS } from './navigation';
import ProjectSidebarMenu from './ProjectSidebarMenu';
import ReportsSidebarMenu from './ReportsSidebarMenu';
import { useAuth } from '../context/AuthContext';
import styles from './Sidebar.module.css';

export default function Sidebar({ collapsed, mobileOpen, onToggleCollapse, onCloseMobile }) {
  const { can } = useAuth();
  return (
    <>
      <div className={`${styles.backdrop} ${mobileOpen ? styles.backdropVisible : ''}`} onClick={onCloseMobile} aria-hidden="true" />
      <aside
        id="app-sidebar"
        className={`${styles.sidebar} ${collapsed ? styles.collapsed : ''} ${mobileOpen ? styles.open : ''}`}
        aria-label="Primary"
      >
        <nav className={styles.nav}>
          <ul className={styles.list}>
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  onClick={onCloseMobile}
                  title={collapsed ? label : undefined}
                  className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}
                >
                  <Icon size={20} />
                  <span className={styles.linkLabel}>{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>

          <p className={`${styles.sectionLabel} ${styles.projectsLabel}`}>Projects</p>
          <ProjectSidebarMenu collapsed={collapsed} onNavigate={onCloseMobile} />

          {can('reports') && (
            <>
              <p className={`${styles.sectionLabel} ${styles.projectsLabel}`}>Reports</p>
              <ReportsSidebarMenu collapsed={collapsed} onNavigate={onCloseMobile} />
            </>
          )}
        </nav>

        <button type="button" className={styles.collapseBtn} onClick={onToggleCollapse} aria-expanded={!collapsed} aria-controls="app-sidebar">
          <ChevronsLeftIcon size={18} className={styles.collapseIcon} />
          <span className={styles.linkLabel}>Collapse</span>
        </button>
      </aside>
    </>
  );
}
