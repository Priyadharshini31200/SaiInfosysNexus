import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import Logo from '../components/Logo';
import { ArrowLeftIcon, ChevronDownIcon, LogoutIcon, MenuIcon, SettingsIcon, UserIcon } from '../components/Icons';
import styles from './Header.module.css';

export default function Header({ title, onBack, user, onOpenMobileNav, onOpenProfile, onOpenConfig, onLogout }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const triggerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const initial = user.username.charAt(0).toUpperCase();

  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <button type="button" className={styles.menuBtn} onClick={onOpenMobileNav} aria-label="Open navigation" aria-controls="app-sidebar">
          <MenuIcon size={22} />
        </button>
        <Link to="/dashboard" className={styles.brand}>
          <span className={styles.iconBox}>
            <Logo variant="mark-brand" className={styles.mark} />
          </span>
          <span className={styles.brandName}>
            <span className={styles.brandMain}>Sai Infosys</span> <span className={styles.brandAccent}>Nexus</span>
          </span>
        </Link>
        <span className={styles.titleSep} aria-hidden="true" />
        <button type="button" className={styles.backBtn} onClick={onBack} aria-label="Go back" title="Back">
          <ArrowLeftIcon size={20} />
        </button>
        <h1 className={styles.pageName}>{title}</h1>
      </div>

      <div className={styles.profile} ref={wrapRef}>
        <button
          ref={triggerRef}
          type="button"
          className={`${styles.trigger} ${open ? styles.triggerOpen : ''}`}
          onClick={() => setOpen((o) => !o)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label="Account menu"
        >
          <span className={styles.avatar} aria-hidden="true">
            {initial}
          </span>
          <ChevronDownIcon size={16} className={styles.chevron} />
        </button>

        {open && (
          <div className={styles.menu} role="menu" aria-label="Profile menu">
            <button
              role="menuitem"
              type="button"
              className={styles.item}
              onClick={() => {
                setOpen(false);
                onOpenProfile();
              }}
            >
              <UserIcon size={17} /> My Profile
            </button>
            {user.role === 'SUPERADMIN' && (
              <button
                role="menuitem"
                type="button"
                className={styles.item}
                onClick={() => {
                  setOpen(false);
                  onOpenConfig();
                }}
              >
                <SettingsIcon size={17} /> Configuration
              </button>
            )}
            <div className={styles.divider} />
            <button
              role="menuitem"
              type="button"
              className={`${styles.item} ${styles.danger}`}
              onClick={() => {
                setOpen(false);
                onLogout();
              }}
            >
              <LogoutIcon size={17} /> Logout
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
