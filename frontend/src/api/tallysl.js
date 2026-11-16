import { download, request } from './client';

const A = '/v1/tally-sl-admin';
const qs = (params = {}) => {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && v !== '' && v !== 'all' && q.set(k, v));
  const s = q.toString();
  return s ? `?${s}` : '';
};

// Staff API for Tally SL products. The server enforces every permission; the UI only mirrors it.
export const tallyApi = {
  access: () => request(`${A}/access/`), // { products: [{ code, name, kind, actions: {view, entry, ...} }], actions }
  products: () => request(`${A}/products/`),
  permissions: () => request(`${A}/permissions/`),
  savePermissions: (user_id, grants) => request(`${A}/permissions/`, { method: 'PUT', body: { user_id, grants } }),
  notifications: () => request(`${A}/notifications/`),
  markNotificationRead: (id) => request(`${A}/notifications/${id}/read/`, { method: 'POST' }),
  transfers: (product, params) => request(`${A}/products/${product}/transfers/${qs(params)}`),
  dashboard: (product) => request(`${A}/dashboard/${qs({ product })}`),
  usageList: (category, params) => request(`${A}/usage/${category}/${qs(params)}`),
  customers: (product, params) => request(`${A}/products/${product}/customers/${qs(params)}`),
  addCustomer: (product, body) => request(`${A}/products/${product}/customers/`, { method: 'POST', body }),
  customer: (product, id) => request(`${A}/products/${product}/customers/${id}/`),
  editCustomer: (product, id, body) => request(`${A}/products/${product}/customers/${id}/`, { method: 'PATCH', body }),
  deleteCustomer: (product, id) => request(`${A}/products/${product}/customers/${id}/`, { method: 'DELETE' }),
  licenseAction: (product, id, action, body = {}) =>
    request(`${A}/products/${product}/customers/${id}/license/${action}/`, { method: 'POST', body }),
  credentials: (product, id, action) => request(`${A}/products/${product}/customers/${id}/credentials/${action}/`, { method: 'POST', body: {} }), // action: resend | reset-password
  exportCustomers: (product, fmt, filters = {}) => {
    const clean = Object.fromEntries(Object.entries(filters).filter(([, v]) => v && v !== 'all'));
    const path = `${A}/products/${product}/customers/export/`;
    return fmt === 'print' ? request(path, { method: 'POST', body: { fmt, filters: clean } }) : download(path, { fmt, filters: clean });
  },
  revokeDevice: (product, id, reason) => request(`${A}/products/${product}/customers/${id}/device/revoke/`, { method: 'POST', body: { reason } }),
  expiryReport: (params) => request(`${A}/reports/license-expiry/${qs(params)}`),
  tallyReport: (params) => request(`${A}/reports/tally-details/${qs(params)}`),
  exportExpiry: (fmt, filters) => exportOf('license-expiry', fmt, filters),
  performanceReport: (params) => request(`${A}/reports/user-performance/${qs(params)}`),
  exportPerformance: (fmt, filters) => exportOf('user-performance', fmt, filters),
  exportTally: (fmt, filters) => exportOf('tally-details', fmt, filters),
};

function exportOf(name, fmt, filters = {}) {
  const clean = Object.fromEntries(Object.entries(filters).filter(([, v]) => v && v !== 'all'));
  const path = `${A}/reports/${name}/export/`;
  const body = { fmt, filters: clean };
  return fmt === 'print' ? request(path, { method: 'POST', body }) : download(path, body);
}
