import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronDownIcon, FolderIcon } from '../components/Icons';
import { useTallyAccess } from '../context/TallyAccessContext';
import styles from './Sidebar.module.css';

const pagesFor = (code, actions) => [actions.view && { label: 'Customers', path: `/tally/${code}/customers` }].filter(Boolean);

/**
 * TALLY SL PRODUCTS section: one expandable item per registered Tally SL product (GSTR2Tally today; future .NET / Python
 * apps appear automatically once registered). Entries only show what the account may do for THAT product; the API enforces the same.
 */
export default function TallySidebarMenu({ collapsed, onNavigate }) {
  const { products, loaded } = useTallyAccess();
  const { pathname } = useLocation();
  const [toggled, setToggled] = useState({});

  const visible = products
    .map((p) => ({ ...p, pages: pagesFor(p.code, p.actions || {}) }))
    .filter((p) => p.pages.length);
  if (!loaded || !visible.length) return null;

  return (
    <>
      <p className={`${styles.sectionLabel} ${styles.projectsLabel}`}>Tally SL Products</p>
      <ul className={styles.list}>
        {visible.map(({ code, name, pages }) => {
          const inProduct = pathname.startsWith(`/tally/${code}/`);
          const open = toggled[code] ?? inProduct;
          if (collapsed) {
            return (
              <li key={code}>
                <NavLink to={pages[0].path} onClick={onNavigate} title={name} className={() => `${styles.link} ${inProduct ? styles.active : ''}`}>
                  <FolderIcon size={20} />
                  <span className={styles.linkLabel}>{name}</span>
                </NavLink>
              </li>
            );
          }
          return (
            <li key={code}>
              <button
                type="button"
                className={`${styles.link} ${styles.parent} ${inProduct ? styles.parentActive : ''}`}
                onClick={() => setToggled((t) => ({ ...t, [code]: !open }))}
                aria-expanded={open}
                aria-controls={`submenu-tally-${code}`}
              >
                <FolderIcon size={20} />
                <span className={styles.linkLabel}>{name}</span>
                <ChevronDownIcon size={16} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} />
              </button>
              {open && (
                <ul id={`submenu-tally-${code}`} className={styles.sub}>
                  {pages.map((page) => (
                    <li key={page.path}>
                      <NavLink
                        to={page.path}
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
    </>
  );
}
