import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { tallyApi } from '../api/tallysl';
import { useAuth } from './AuthContext';

const TallyAccessContext = createContext({ products: [], can: () => false, anyCan: () => false, loaded: false });

/** Per-product Tally SL permissions of the signed-in staff account (navigation hints; the API enforces them). */
export function TallyAccessProvider({ children }) {
  const { status, user } = useAuth();
  const [products, setProducts] = useState([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (status !== 'authenticated') {
      setProducts([]);
      setLoaded(false);
      return undefined;
    }
    let current = true;
    tallyApi
      .access()
      .then((d) => current && (setProducts(d.products), setLoaded(true)))
      .catch(() => current && (setProducts([]), setLoaded(true)));
    return () => {
      current = false;
    };
  }, [status, user?.id]);

  const can = useCallback((action, code) => Boolean(products.find((p) => p.code === code)?.actions?.[action]), [products]);
  const anyCan = useCallback((action) => products.some((p) => p.actions?.[action]), [products]);
  const value = useMemo(() => ({ products, can, anyCan, loaded }), [products, can, anyCan, loaded]);
  return <TallyAccessContext.Provider value={value}>{children}</TallyAccessContext.Provider>;
}

export const useTallyAccess = () => useContext(TallyAccessContext);
