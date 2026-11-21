import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronsLeftIcon, FolderIcon } from '../components/Icons';
import { NAV_ITEMS, PROJECT_MANAGEMENT_ENABLED } from './navigation';
import ReportsSidebarMenu from './ReportsSidebarMenu';
import ProjectManagementMenu from './ProjectManagementMenu';
import UniverseSidebarMenu from './UniverseSidebarMenu';
import { integrationsApi } from '../api/integrations';
import { useAuth } from '../context/AuthContext';
import { useTallyAccess } from '../context/TallyAccessContext';
import styles from './Sidebar.module.css';

export default function Sidebar({ collapsed, mobileOpen, onToggleCollapse, onCloseMobile }) {
  const { can } = useAuth();
  const { anyCan, products } = useTallyAccess();
  const { user } = useAuth();
  const { pathname, state } = useLocation();
  const [scope, setScope] = useState(null);
  useEffect(() => {
    if (user?.role !== 'ADMIN') return undefined;
    let active = true;
    integrationsApi.universeProjects().then((data) => active && setScope(data), () => {});
    return () => { active = false; };
  }, [user]);
  const scopeList = user?.role === 'ADMIN' ? scope?.projects || [] : [];
  const barcode = can('entry', 'prn-auto-print') || can('view', 'prn-auto-print') || can('approve', 'prn-auto-print') || can('device_transfer', 'prn-auto-print');
  const entries = [
    (barcode || scopeList.some((project) => !project.tally_required) || user?.role === 'SUPERADMIN') && { to: '/directory/non-tally', label: 'Non-Tally SL Projects', key: 'Non-Tally SL Projects' },
    (products.length > 0 || scopeList.some((project) => project.tally_required) || (user?.role === 'ADMIN' && scope?.legacy?.length > 0) || user?.role === 'SUPERADMIN') && { to: '/directory/tally', label: 'Tally SL Projects', key: 'Tally SL Projects' },
  ].filter(Boolean);
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
            {NAV_ITEMS.filter(() => can('view', 'gstr2tally:dashboard') || can('view', 'prn-auto-print:dashboard') || anyCan('view')).map(({ to, label, icon: Icon }) => (
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

          {entries.length > 0 && (
            <>
              <p className={`${styles.sectionLabel} ${styles.projectsLabel}`}>Product Management</p>
              <ul className={styles.list}>
                {entries.map((e) => {
                  const active = pathname === e.to || state?.crumb === e.key;
                  return (
                    <li key={e.to}>
                      <NavLink to={e.to} onClick={onCloseMobile} title={collapsed ? e.label : undefined} className={`${styles.link} ${active ? styles.active : ''}`}>
                        <FolderIcon size={20} /><span className={styles.linkLabel}>{e.label}</span>
                      </NavLink>
                    </li>
                  );
                })}
              </ul>
            </>
          )}

          <UniverseSidebarMenu collapsed={collapsed} onNavigate={onCloseMobile} />

          {PROJECT_MANAGEMENT_ENABLED && <ProjectManagementMenu collapsed={collapsed} onNavigate={onCloseMobile} />}

          <ReportsSidebarMenu onNavigate={onCloseMobile} />
        </nav>

        <button type="button" className={styles.collapseBtn} onClick={onToggleCollapse} aria-expanded={!collapsed} aria-controls="app-sidebar">
          <ChevronsLeftIcon size={18} className={styles.collapseIcon} />
          <span className={styles.linkLabel}>Collapse</span>
        </button>
      </aside>
    </>
  );
}
