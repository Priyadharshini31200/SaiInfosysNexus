import { useEffect, useState } from 'react';
import { API_BASE } from '../api/client';
import { companyApi } from '../api/company';

/** Company branding shared by the header and My Profile. One module-level copy: saving in the profile drawer updates the header at once, no refresh. */
let state = { loaded: false, name: '', gstin: '', has_logo: false, logo_version: '', can_edit: false };
let inflight = null;
const listeners = new Set();
const publish = (next) => { state = { ...state, ...next, loaded: true }; listeners.forEach((l) => l(state)); };

export function loadCompany(force = false) {
  if (!force && (state.loaded || inflight)) return inflight || Promise.resolve(state);
  inflight = companyApi.get().then((d) => publish(d), () => publish({})).finally(() => { inflight = null; });
  return inflight;
}
export const setCompany = publish;
export const resetCompany = () => { state = { loaded: false, name: '', gstin: '', has_logo: false, logo_version: '', can_edit: false }; listeners.forEach((l) => l(state)); };
export const logoUrl = (c) => (c.has_logo ? `${API_BASE}/company/logo/?v=${encodeURIComponent(c.logo_version)}` : '');

export default function useCompany(enabled = true) {
  const [c, setC] = useState(state);
  useEffect(() => {
    listeners.add(setC);
    if (enabled) loadCompany();
    return () => listeners.delete(setC);
  }, [enabled]);
  return c;
}
