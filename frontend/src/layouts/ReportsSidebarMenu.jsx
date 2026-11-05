import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronDownIcon } from '../components/Icons';
import { REPORT_PAGES, REPORTS_MENU } from './navigation';
import styles from './Sidebar.module.css';

/** REPORTS section of the sidebar: an expandable "Reports" item with one entry per report (a single link when collapsed). */
export default function ReportsSidebarMenu({ collapsed, onNavigate }) {
  const { pathname } = useLocation();
  const [toggled, setToggled] = useState(null); // the user's explicit open/closed choice
  const { label, icon: Icon } = REPORTS_MENU;
  const inReports = pathname.startsWith('/reports/');
  const open = toggled ?? inReports;

  if (collapsed) {
    return (
      <ul className={styles.list}>
        <li>
          <NavLink to={REPORT_PAGES[0].path} onClick={onNavigate} title={label} className={() => `${styles.link} ${inReports ? styles.active : ''}`}>
            <Icon size={20} />
            <span className={styles.linkLabel}>{label}</span>
          </NavLink>
        </li>
      </ul>
    );
  }

  return (
    <ul className={styles.list}>
      <li>
        <button
          type="button"
          className={`${styles.link} ${styles.parent} ${inReports ? styles.parentActive : ''}`}
          onClick={() => setToggled(!open)}
          aria-expanded={open}
          aria-controls="submenu-reports"
        >
          <Icon size={20} />
          <span className={styles.linkLabel}>{label}</span>
          <ChevronDownIcon size={16} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} />
        </button>
        {open && (
          <ul id="submenu-reports" className={styles.sub}>
            {REPORT_PAGES.map((page) => (
              <li key={page.path}>
                <NavLink to={page.path} onClick={onNavigate} className={({ isActive }) => `${styles.subLink} ${isActive ? styles.subActive : ''}`}>
                  {page.label}
                </NavLink>
              </li>
            ))}
          </ul>
        )}
      </li>
    </ul>
  );
}
