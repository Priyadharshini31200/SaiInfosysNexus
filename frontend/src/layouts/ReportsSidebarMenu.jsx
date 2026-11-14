import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronDownIcon } from '../components/Icons';
import { useAuth } from '../context/AuthContext';
import { useTallyAccess } from '../context/TallyAccessContext';
import { PROJECT_ITEMS, REPORT_PAGES, REPORTS_MENU } from './navigation';
import styles from './Sidebar.module.css';

const GSTR_REPORTS = [
  { label: 'License Expiry Report', path: '/tally/reports/license-expiry' },
  { label: 'Tally Details Report', path: '/tally/reports/tally-details' },
  { label: 'User Performance Report', path: '/tally/reports/user-performance' },
];

/** REPORTS: "GSTR Reports" (Tally SL, per-product Reports permission) and "PRN Reports" (the existing reports, unchanged). */
export default function ReportsSidebarMenu({ collapsed, onNavigate }) {
  const { can } = useAuth();
  const { anyCan } = useTallyAccess();
  const { pathname } = useLocation();
  const [toggled, setToggled] = useState({});
  const { label, icon: Icon } = REPORTS_MENU;

  const prnPages = REPORT_PAGES.filter((p) => PROJECT_ITEMS.some(({ slug }) => can('reports', `${slug}:${p.path.endsWith('usage') ? 'usage' : 'expiry'}`)));
  const groups = [
    anyCan('reports') && { key: 'gstr', title: 'GSTR Reports', pages: GSTR_REPORTS, active: pathname.startsWith('/tally/reports/') },
    prnPages.length > 0 && { key: 'prn', title: 'PRN Reports', pages: prnPages, active: pathname.startsWith('/reports/') },
  ].filter(Boolean);
  if (!groups.length) return null;

  if (collapsed) {
    return (
      <ul className={styles.list}>
        <li>
          <NavLink to={groups[0].pages[0].path} onClick={onNavigate} title={label} className={() => `${styles.link} ${groups.some((g) => g.active) ? styles.active : ''}`}>
            <Icon size={20} />
            <span className={styles.linkLabel}>{label}</span>
          </NavLink>
        </li>
      </ul>
    );
  }
  return (
    <ul className={styles.list}>
      {groups.map((g) => {
        const open = toggled[g.key] ?? g.active;
        return (
          <li key={g.key}>
            <button type="button" className={`${styles.link} ${styles.parent} ${g.active ? styles.parentActive : ''}`}
              onClick={() => setToggled((t) => ({ ...t, [g.key]: !open }))} aria-expanded={open} aria-controls={`submenu-reports-${g.key}`}>
              <Icon size={20} />
              <span className={styles.linkLabel}>{g.title}</span>
              <ChevronDownIcon size={16} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} />
            </button>
            {open && (
              <ul id={`submenu-reports-${g.key}`} className={styles.sub}>
                {g.pages.map((page) => (
                  <li key={page.path}>
                    <NavLink to={page.path} onClick={onNavigate} className={({ isActive }) => `${styles.subLink} ${isActive ? styles.subActive : ''}`}>{page.label}</NavLink>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
