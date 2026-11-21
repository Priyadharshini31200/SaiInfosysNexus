// Asia/Kolkata has no DST (fixed UTC+05:30): the date/time shown to admins is IST, the persisted value is the UTC instant.
const IST_MS = 5.5 * 3600 * 1000;
export const istInputToUtc = (value) => (value ? new Date(`${value}:00+05:30`).toISOString() : null);
export const utcToIstInput = (iso) => (iso ? new Date(new Date(iso).getTime() + IST_MS).toISOString().slice(0, 16) : '');
export const formatIst = (iso) => (iso ? `${new Date(iso).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' })} IST` : '');
/** One browser-wide signal: any visibility change (gear, Configuration, card flip) refreshes every other view immediately. */
const EVT = 'nexus:dashboard-visibility-changed';
export const notifyVisibilityChanged = (code) => window.dispatchEvent(new CustomEvent(EVT, { detail: { code } }));
export const onVisibilityChanged = (fn) => { window.addEventListener(EVT, fn); return () => window.removeEventListener(EVT, fn); };
export const nowIstInput = () => utcToIstInput(new Date().toISOString());
