import { NavLink } from 'react-router-dom';
import { ChevronsLeftIcon } from '../components/Icons';
import { NAV_ITEMS, NON_TALLY_ITEMS } from './navigation';
import TallySidebarMenu from './TallySidebarMenu';
import ProjectSidebarMenu from './ProjectSidebarMenu';
import ReportsSidebarMenu from './ReportsSidebarMenu';
import { useAuth } from '../context/AuthContext';
import { useTallyAccess } from '../context/TallyAccessContext';
import styles from './Sidebar.module.css';

export default function Sidebar({ collapsed, mobileOpen, onToggleCollapse, onCloseMobile }) {
  const { can } = useAuth();
  const { anyCan } = useTallyAccess();
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
            {NAV_ITEMS.filter(() => can('view', 'gstr2tally:dashboard') || can('view', 'prn-auto-print:dashboard')).map(({ to, label, icon: Icon }) => (
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

          <p className={`${styles.sectionLabel} ${styles.projectsLabel}`}>Non-Tally SL Products</p>
          <ProjectSidebarMenu collapsed={collapsed} onNavigate={onCloseMobile} items={NON_TALLY_ITEMS} />

          <TallySidebarMenu collapsed={collapsed} onNavigate={onCloseMobile} />

          {(can('reports', null) || anyCan('reports')) && (
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
