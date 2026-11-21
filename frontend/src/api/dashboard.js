import { request } from './client';

// Super Admin dashboard (the server enforces the role).
export const dashboardApi = {
  // { projects: [{ code, label, available, used, not_used, expiring, total } | { code, label, available: false, message }] }
  sync: () => request('/dashboard/sync/'),
  // Card visibility (server-authoritative): { server_time_utc, settings: { code: { mode, visible_from_utc, effective, version, can_edit } } }
  cardVisibility: () => request('/dashboard/card-visibility/'),
  cardVisibilityProjects: () => request('/dashboard/card-visibility/projects/'),
  cardVisibilityOne: (code) => request(`/dashboard/card-visibility/${code}/`),
  saveCardVisibility: (code, body) => request(`/dashboard/card-visibility/${code}/`, { method: 'PUT', body }),
  projectUsage: () => request('/dashboard/project-usage/'),
  // { page, page_size, total, results: [{ sno, customer, device, last_used, status }] }
  projectUsageList: (project, category, { page, pageSize, search }) => {
    const query = new URLSearchParams({ page, page_size: pageSize });
    if (search) query.set('search', search);
    return request(`/dashboard/project-usage/${project}/${category}/?${query}`);
  },
};
