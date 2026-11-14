import { useAuth } from '../context/AuthContext';
import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronDownIcon } from '../components/Icons';
import { PROJECT_ITEMS, PROJECT_MENU, PROJECT_PAGES } from './navigation';
import styles from './Sidebar.module.css';

/**
 * PROJECTS section of the sidebar: every configured project is an expandable item with the same two
 * submenus (Add Customer, Customer List). With the sidebar collapsed to icons, a project is a single
 * link to its Customer List.
 */
export default function ProjectSidebarMenu({ collapsed, onNavigate, items = PROJECT_ITEMS }) {
  const { can } = useAuth();
  const { pathname } = useLocation();
  const [toggled, setToggled] = useState({}); // slug -> user's explicit open/closed choice

  return (
    <ul className={styles.list}>
      {items.map(({ slug, label, icon: Icon }) => {
        if (!can('entry', slug) && !can('view', slug) && !can('approve', slug) && !can('device_transfer', slug)) return null;
        const inProject = pathname.startsWith(`/projects/${slug}/`);
        const open = toggled[slug] ?? inProject;

        if (collapsed) {
          return (
            <li key={slug}>
              <NavLink
                to={((can('view', slug) || can('approve', slug) || can('device_transfer', slug)) ? PROJECT_PAGES.list : PROJECT_PAGES.add).path(slug)}
                onClick={onNavigate}
                title={label}
                className={() => `${styles.link} ${inProject ? styles.active : ''}`}
              >
                <Icon size={20} />
                <span className={styles.linkLabel}>{label}</span>
              </NavLink>
            </li>
          );
        }

        return (
          <li key={slug}>
            <button
              type="button"
              className={`${styles.link} ${styles.parent} ${inProject ? styles.parentActive : ''}`}
              onClick={() => setToggled((t) => ({ ...t, [slug]: !open }))}
              aria-expanded={open}
              aria-controls={`submenu-${slug}`}
            >
              <Icon size={20} />
              <span className={styles.linkLabel}>{label}</span>
              <ChevronDownIcon size={16} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} />
            </button>
            {open && (
              <ul id={`submenu-${slug}`} className={styles.sub}>
                {PROJECT_MENU.filter((page) => can(page === PROJECT_PAGES.add ? 'entry' : 'view', slug) || (page === PROJECT_PAGES.list && (can('approve', slug) || can('device_transfer', slug)))).map((page) => (
                  <li key={page.label}>
                    <NavLink
                      to={page.path(slug)}
                      onClick={onNavigate}
                      className={({ isActive }) => `${styles.subLink} ${isActive ? styles.subActive : ''}`}
                    >
                      {page.label}
                    </NavLink>
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
