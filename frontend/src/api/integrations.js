import { API_BASE, ApiError, download, request } from './client';

const A = '/integrations/admin';

// Super Admin API for Project Management (the server enforces the role). API secrets are only ever returned once, by
// create / rotate, and are never stored by the UI.
export const integrationsApi = {
  projects: () => request(`${A}/projects/`),
  createProject: (body) => request(`${A}/projects/`, { method: 'POST', body }),
  project: (code) => request(`${A}/projects/${code}/`),
  updateProject: (code, body) => request(`${A}/projects/${code}/`, { method: 'PATCH', body }),
  rotate: (code) => request(`${A}/projects/${code}/rotate-credentials/`, { method: 'POST', body: {} }),
  testConnection: (code) => request(`${A}/projects/${code}/test-connection/`, { method: 'POST', body: {} }),
  customers: (code) => request(`${A}/projects/${code}/customers/`),
  audit: (code) => request(`${A}/projects/${code}/audit/`),
  approve: (licenseId) => request(`${A}/licenses/${licenseId}/approve/`, { method: 'POST', body: {} }),
  resendWelcome: (customerId) => request(`${A}/customers/${customerId}/resend-welcome/`, { method: 'POST', body: {} }),
  // ---- API-connected product pages (Dashboard / Customers), e.g. Mothys
  productDashboard: (code) => request(`${A}/projects/${code}/dashboard/`),
  productCustomers: (code, params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && v !== '' && v !== 'all' && q.set(k, v));
    return request(`${A}/projects/${code}/product-customers/?${q}`);
  },
  productCustomer: (code, id) => request(`${A}/projects/${code}/product-customers/${id}/`),
  editProductCustomer: (code, id, body) => request(`${A}/projects/${code}/product-customers/${id}/`, { method: 'PATCH', body }),
  productAction: (code, id, action, body = {}) => request(`${A}/projects/${code}/product-customers/${id}/${action}/`, { method: 'POST', body }),
  exportProductCustomers: (code, fmt, filters = {}) => {
    const clean = Object.fromEntries(Object.entries(filters).filter(([, v]) => v && v !== 'all'));
    const path = `${A}/projects/${code}/product-customers/export/`;
    return fmt === 'print' ? request(path, { method: 'POST', body: { fmt, filters: clean } }) : download(path, { fmt, filters: clean });
  },
  productReport: (code, kind, params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && v !== '' && v !== 'all' && q.set(k, v));
    return request(`${A}/projects/${code}/reports/${kind}/?${q}`);
  },
  exportProductReport: (code, kind, fmt, filters = {}) => {
    const clean = Object.fromEntries(Object.entries(filters).filter(([, v]) => v && v !== 'all'));
    const path = `${A}/projects/${code}/reports/${kind}/export/`;
    return fmt === 'print' ? request(path, { method: 'POST', body: { fmt, filters: clean } }) : download(path, { fmt, filters: clean });
  },
  failedSync: () => request(`${A}/failed-sync/`),
  retrySync: (id) => request(`${A}/failed-sync/${id}/retry/`, { method: 'POST', body: {} }),
  dashboard: () => request(`${A}/dashboard/`),
  /** Downloads the generated documentation: kind = 'markdown' | 'openapi'. */
  async downloadDocs(code, kind) {
    const res = await fetch(`${API_BASE}${A}/projects/${code}/docs/${kind}/`, { credentials: 'same-origin' });
    if (!res.ok) throw new ApiError('Could not download the documentation.', { status: res.status });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: `nexus-${code.toLowerCase()}-${kind === 'openapi' ? 'openapi.json' : 'api.md'}` });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
