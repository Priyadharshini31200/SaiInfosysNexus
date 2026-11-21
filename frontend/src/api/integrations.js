import { API_BASE, ApiError, download, request } from './client';

let A = '/integrations/admin';
/** A project developer works against the project-scoped /integrations/dev API; everything else is Superadmin (/integrations/admin). */
export const setIntegrationsBase = (base) => { A = base; };

// Super Admin API for Project Management (the server enforces the role). API secrets are only ever returned once, by
// create / rotate, and are never stored by the UI.
export const integrationsApi = {
  projects: () => request(`${A}/projects/`),
  codePreview: (name) => request(`${A}/projects/code-preview/?name=${encodeURIComponent(name)}`),
  addManualCustomer: (code, body) => request(`${A}/projects/${code}/universe-customers/manual/`, { method: 'POST', body }),
  addManualProject: (body) => request(`${A}/universe/manual-project/`, { method: 'POST', body }),
  createProject: (body) => request(`${A}/projects/`, { method: 'POST', body }),
  project: (code) => request(`${A}/projects/${code}/`),
  deleteProject: (code) => request(`${A}/projects/${code}/`, { method: 'DELETE' }),
  updateProject: (code, body) => request(`${A}/projects/${code}/`, { method: 'PATCH', body }),
  rotate: (code) => request(`${A}/projects/${code}/rotate-credentials/`, { method: 'POST', body: {} }),
  testConnection: (code) => request(`${A}/projects/${code}/test-connection/`, { method: 'POST', body: {} }),
  tallyPending: (code) => request(`${A}/projects/${code}/tally-pending/`),
  retryTally: (code, id) => request(`${A}/projects/${code}/tally-pending/${id}/retry/`, { method: 'POST', body: {} }),
  usageReport: (code, q = {}) => request(`${A}/projects/${code}/usage-report/?${new URLSearchParams(q)}`),
  deviceTransfers: (code, q = {}) => request(`${A}/projects/${code}/device-transfers/?${new URLSearchParams(q)}`),
  renewals: (code) => request(`${A}/projects/${code}/renewals/`),
  universeHub: (params = {}) => request(`${A}/universe/hub/?${new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v)))}`),
  universeProject: (code) => request(`${A}/universe/projects/${code}/`),
  universeProjects: () => request(`${A}/universe/projects/`),
  universeCards: () => request(`${A}/universe/cards/`),
  universeDashboard: (code) => request(`${A}/projects/${code}/universe-dashboard/`),
  universeCustomers: (code, params = {}) => request(`${A}/projects/${code}/universe-customers/?${new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v && v !== 'all')))}`),
  universeCustomer: (code, kind, id, q = {}) => request(`${A}/projects/${code}/universe-customers/${kind}/${id}/?${new URLSearchParams(Object.fromEntries(Object.entries(q).filter(([, v]) => v)))}`),
  deleteUniverseCustomer: (code, kind, id) => request(`${A}/projects/${code}/universe-customers/${kind}/${id}/`, { method: 'DELETE' }),
  patchUniverseCustomer: (code, kind, id, body) => request(`${A}/projects/${code}/universe-customers/${kind}/${id}/`, { method: 'PATCH', body }),
  universeAction: (code, kind, id, action, body = {}) => request(`${A}/projects/${code}/universe-customers/${kind}/${id}/${action}/`, { method: 'POST', body }),
  registrations: (params = {}) => request(`${A}/registrations/?${new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v)))}`),
  registration: (id, q = {}) => request(`${A}/registrations/${id}/?${new URLSearchParams(Object.fromEntries(Object.entries(q).filter(([, v]) => v)))}`),
  registrationAction: (id, action, body = {}) => request(`${A}/registrations/${id}/${action}/`, { method: 'POST', body }),
  notifications: () => request(`${A}/notifications/`),
  readNotification: (id) => request(`${A}/notifications/${id}/read/`, { method: 'POST', body: {} }),
  validateApi: (code, kind, body = {}) => request(`${A}/projects/${code}/validate/${kind}/`, { method: 'POST', body }),
  detectFields: (code, sample) => request(`${A}/projects/${code}/detect-fields/`, { method: 'POST', body: sample ? { sample } : {} }),
  activateIntegration: (code) => request(`${A}/projects/${code}/activate/`, { method: 'POST', body: {} }),
  devProjects: () => request('/integrations/dev/projects/'),
  apiConfig: (code) => request(`${A}/projects/${code}/api-config/`),
  saveApiConfig: (code, body) => request(`${A}/projects/${code}/api-config/`, { method: 'PUT', body }),
  syncCustomers: (code) => request(`${A}/projects/${code}/sync/`, { method: 'POST', body: {} }),
  docEmails: (code) => request(`${A}/projects/${code}/send-docs/`),
  sendDocs: (code, to, opts = {}) => request(`${A}/projects/${code}/send-docs/`, { method: 'POST', body: { to, ...opts } }),
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
