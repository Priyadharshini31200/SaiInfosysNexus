import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { tallyApi } from '../api/tallysl';
import { useAuth } from './AuthContext';

const TallyAccessContext = createContext({ products: [], can: () => false, anyCan: () => false, loaded: false });

const RETRY_BASE_MS = 2000;
const RETRY_MAX_MS = 30000;

/**
 * Per-product Tally SL permissions of the signed-in staff account (navigation hints; the API enforces them).
 * A failed load (server restarting, brief network or database error) must never wipe the menu: the last good answer is kept
 * and the request is retried with a growing delay, and again when the tab regains focus, until it succeeds.
 */
export function TallyAccessProvider({ children }) {
  const { status, user } = useAuth();
  const [products, setProducts] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [attempt, setAttempt] = useState(0); // bumped to force another load
  const failures = useRef(0);

  useEffect(() => {
    if (status !== 'authenticated') {
      setProducts([]);
      setLoaded(false);
      failures.current = 0;
      return undefined;
    }
    let current = true;
    let timer;
    tallyApi
      .access()
      .then((d) => {
        if (!current) return;
        failures.current = 0;
        setProducts(Array.isArray(d.products) ? d.products : []);
        setLoaded(true);
      })
      .catch(() => {
        if (!current) return;
        failures.current += 1;
        // Keep whatever was loaded before; retry soon.
        timer = setTimeout(() => setAttempt((n) => n + 1), Math.min(RETRY_BASE_MS * 2 ** (failures.current - 1), RETRY_MAX_MS));
      });
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [status, user?.id, attempt]);

  // Coming back to the tab after a failure: try again at once instead of waiting for the backoff timer.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && failures.current > 0) setAttempt((n) => n + 1);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);

  const can = useCallback((action, code) => Boolean(products.find((p) => p.code === code)?.actions?.[action]), [products]);
  const anyCan = useCallback((action) => products.some((p) => p.actions?.[action]), [products]);
  const value = useMemo(() => ({ products, can, anyCan, loaded }), [products, can, anyCan, loaded]);
  return <TallyAccessContext.Provider value={value}>{children}</TallyAccessContext.Provider>;
}

export const useTallyAccess = () => useContext(TallyAccessContext);
