import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { request } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { REPORTS_MENU } from './navigation';
import styles from './Sidebar.module.css';

export default function ReportsSidebarMenu({ onNavigate }) {
  const { user } = useAuth();
  const [allowed, setAllowed] = useState(false);
  useEffect(() => {
    if (user?.role !== 'ADMIN') return undefined;
    let active = true;
    request('/reports/consolidated/?meta=1').then((data) => {
      if (active) setAllowed(data.reports.length > 0);
    }, () => { if (active) setAllowed(false); });
    return () => { active = false; };
  }, [user]);

  if (user?.role !== 'SUPERADMIN' && !allowed) return null;
  const { icon: Icon, label } = REPORTS_MENU;
  return <ul className={styles.list}><li>
    <NavLink to="/reports" onClick={onNavigate} title={label} className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}>
      <Icon size={20} /><span className={styles.linkLabel}>{label}</span>
    </NavLink>
  </li></ul>;
}
