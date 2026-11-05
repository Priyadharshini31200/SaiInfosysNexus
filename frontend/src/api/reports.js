import { download, request } from './client';

export const reportsApi = {
  // { results: [customer record + { project, product, expiry_status }] } across every project
  licenseExpiry: () => request('/reports/license-expiry/'),
  // fmt: 'xlsx' | 'pdf' (a file) | 'print' (table data). ids are the rows on screen, in order.
  exportExpiry: (fmt, ids, filters) => send('/reports/license-expiry/export/', fmt, ids, filters),
  // { results: [{ customer, product, device, last_used, status }], unavailable: [project labels] }; filters: { project, status, search }
  usage: ({ project, status, search }) => request(`/reports/usage/?${usageQuery({ project, status, search })}`),
  exportUsage: (fmt, filters) => send('/reports/usage/export/', fmt, undefined, undefined, filters),
  exportCustomers: (project, fmt, ids, filters) => send(`/projects/${project}/customers/export/`, fmt, ids, filters),
};

function usageQuery({ project, status, search }) {
  const query = new URLSearchParams();
  if (project) query.set('project', project);
  if (status) query.set('status', status);
  if (search) query.set('search', search);
  return query;
}

function send(path, fmt, ids, filters, extra) {
  const body = extra ? { fmt, ...extra } : { fmt, ids, filters };
  return fmt === 'print' ? request(path, { method: 'POST', body }) : download(path, body);
}
