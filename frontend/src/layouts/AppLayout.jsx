import { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation, useNavigate, useNavigationType } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import Sidebar from './Sidebar';
import Header from './Header';
import ApprovalGate from '../components/ApprovalGate';
import ProfileDrawer from '../components/ProfileDrawer';
import ConfigurationDrawer from '../components/ConfigurationDrawer';
import { getPageTitle } from './navigation';
import styles from './AppLayout.module.css';

const DESKTOP_QUERY = '(min-width: 961px)';
const STORAGE_KEY = 'nexus.sidebarCollapsed';

function readCollapsed() {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function useIsDesktop() {
  const [matches, setMatches] = useState(() => window.matchMedia(DESKTOP_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    const onChange = (e) => setMatches(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return matches;
}

export default function AppLayout() {
  const { user, logout } = useAuth();
  const { pathname, search, key } = useLocation();
  const navType = useNavigationType();
  const navigate = useNavigate();
  const isDesktop = useIsDesktop();
  const [collapsedPref, setCollapsedPref] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [drawer, setDrawer] = useState(null); // null | 'profile' | 'config'
  const closeDrawer = useCallback(() => setDrawer(null), []);

  const collapsed = isDesktop && collapsedPref;
  const title = getPageTitle(pathname, search);

  // How many in-app pages sit behind this one (PUSH adds one, back/POP removes one). The Back arrow only
  // uses browser history when there is one, so it never leaves the app or lands on Login.
  const depth = useRef(0);
  const lastKey = useRef(key);
  useEffect(() => {
    if (lastKey.current === key) return;
    lastKey.current = key;
    if (navType === 'PUSH') depth.current += 1;
    else if (navType === 'POP') depth.current = Math.max(depth.current - 1, 0);
  }, [key, navType]);

  function handleBack() {
    if (depth.current > 0) navigate(-1);
    else navigate('/dashboard', { replace: true });
  }

  useEffect(() => {
    document.title = `${title} · Sai Infosys Nexus`;
  }, [title]);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname, isDesktop]);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const onKey = (e) => e.key === 'Escape' && setMobileOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mobileOpen]);

  function toggleCollapse() {
    setCollapsedPref((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, next ? '1' : '0');
      } catch {
        /* preference is optional */
      }
      return next;
    });
  }

  async function handleLogout() {
    await logout();
    navigate('/login', { replace: true });
  }

  return (
    <div className={styles.app}>
      <Sidebar collapsed={collapsed} mobileOpen={mobileOpen} onToggleCollapse={toggleCollapse} onCloseMobile={() => setMobileOpen(false)} />
      <div className={`${styles.main} ${collapsed ? styles.mainCollapsed : ''}`}>
        <Header title={title} onBack={handleBack} user={user} onOpenMobileNav={() => setMobileOpen(true)} onOpenProfile={() => setDrawer('profile')} onOpenConfig={() => setDrawer('config')} onLogout={handleLogout} />
        <main className={styles.content}>
          <Outlet />
        </main>
      </div>
      {user.role === 'SUPERADMIN' && <ApprovalGate />}
      <ProfileDrawer open={drawer === 'profile'} onClose={closeDrawer} />
      {user.role === 'SUPERADMIN' && <ConfigurationDrawer open={drawer === 'config'} onClose={closeDrawer} />}
    </div>
  );
}
