import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { integrationsApi } from '../api/integrations';
import { useAuth } from '../context/AuthContext';
import styles from './Sidebar.module.css';

export default function UniverseSidebarMenu({ collapsed, onNavigate }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const isSuper = user?.role === 'SUPERADMIN';
  const [hasProjects, setHasProjects] = useState(isSuper);
  const [unread, setUnread] = useState(0);
  const [toggled, setToggled] = useState(null);
  const eligible = isSuper || user?.role === 'ADMIN';
  useEffect(() => {
    if (!eligible) return undefined;
    let current = true;
    const load = () => {
      if (!isSuper) integrationsApi.universeProjects().then((d) => current && setHasProjects(d.projects.some((p) => p.can.integration)), () => {});
      if (isSuper) integrationsApi.notifications().then((d) => current && setUnread(d.results.filter((n) => !n.read && n.kind === 'REGISTRATION').length), () => {});
    };
    load();
    const t = setInterval(load, 30000);
    return () => { current = false; clearInterval(t); };
  }, [eligible, isSuper]);
  return null; // API Integration is opened per project from the Tally SL Projects directory (API icon); /universe/api and its deep links stay
  // eslint-disable-next-line no-unreachable
  if (!eligible || !hasProjects) return null;
  const inside = pathname.startsWith('/universe');
  const open = toggled ?? true;
  const items = hasProjects ? [{ to: '/universe/api', label: 'API Integration', badge: unread }] : [];
  if (!items.length) return null;
  return (
    <>
      {collapsed && <p className={`${styles.sectionLabel} ${styles.projectsLabel}`}>UNIVERSE</p>}
      <ul className={`${styles.list} ${styles.projectsLabel}`}>
        {collapsed ? items.map((i) => (
          <li key={i.to}><NavLink to={i.to} onClick={onNavigate} title={i.label} className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}><span className={styles.linkLabel}>{i.label}</span></NavLink></li>
        )) : (
          <li>
            <button type="button" className={`${styles.link} ${styles.parent} ${inside ? styles.parentActive : ''}`} aria-expanded={open} aria-controls="submenu-universe" onClick={() => setToggled(!open)}>
              <span className={styles.linkLabel}>UNIVERSE</span>
            </button>
            {open && (
              <ul id="submenu-universe" className={styles.sub}>
                {items.map((i) => (
                  <li key={i.to}>
                    <NavLink to={i.to} onClick={onNavigate} className={({ isActive }) => `${styles.subLink} ${isActive ? styles.subActive : ''}`}>{i.label}{i.badge ? ` (${i.badge})` : ''}</NavLink>
                  </li>
                ))}
              </ul>
            )}
          </li>
        )}
      </ul>
    </>
  );
}
