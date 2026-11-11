import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { authApi } from '../api/auth';
import { SESSION_EXPIRED_EVENT } from '../api/client';

const AuthContext = createContext(null);

// status: 'loading' | 'authenticated' | 'anonymous'
export function AuthProvider({ children }) {
  const location = useLocation();
  const activeProject = location.pathname.match(/^\/projects\/([^/]+)/)?.[1];
  const [status, setStatus] = useState('loading');
  const [user, setUser] = useState(null);
  const [notice, setNotice] = useState('');
  const wasAuthenticated = useRef(false);

  const clear = useCallback((message = '') => {
    wasAuthenticated.current = false;
    setUser(null);
    setStatus('anonymous');
    setNotice(message);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const { user: me } = await authApi.me();
      wasAuthenticated.current = true;
      setUser(me);
      setStatus('authenticated');
    } catch (err) {
      if (err.status === 401 && wasAuthenticated.current) {
        clear('Your session has expired. Please sign in again.');
      } else {
        clear();
      }
    }
  }, [clear]);

  useEffect(() => {
    refresh();
    const timer = setInterval(() => { if (document.visibilityState === 'visible' && wasAuthenticated.current) refresh(); }, 10000);
    return () => clearInterval(timer);
  }, [refresh]);

  // Session expiry on any API call, tab re-focus, or Back/Forward cache restore.
  useEffect(() => {
    const onExpired = () => clear('Your session has expired. Please sign in again.');
    const onVisible = () => {
      if (document.visibilityState === 'visible' && wasAuthenticated.current) refresh();
    };
    const onPageShow = (event) => {
      if (event.persisted) refresh();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pageshow', onPageShow);
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('pageshow', onPageShow);
    };
  }, [clear, refresh]);

  const login = useCallback(async (username, password) => {
    const { user: me } = await authApi.login(username, password);
    wasAuthenticated.current = true;
    setUser(me);
    setNotice('');
    setStatus('authenticated');
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      /* session already gone; still clear local state */
    }
    clear('You have been signed out.');
  }, [clear]);

  // UI-only convenience; the API enforces the same permissions. Super Admin may do everything.
  // Explicit modules scope shared components; project routes infer their own module.
  const can = useCallback(
    (action, module = activeProject) => user?.role === 'SUPERADMIN' || (module ? Boolean(user?.project_permissions?.[module]?.[action]) : Boolean(user?.permissions?.[action])),
    [user, activeProject],
  );

  const value = useMemo(
    () => ({ status, user, notice, login, logout, clear, setNotice, can, refresh }),
    [status, user, notice, login, logout, clear, can],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
