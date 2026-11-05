import { request } from './client';

// Super Admin dashboard (the server enforces the role).
export const dashboardApi = {
  // { projects: [{ code, label, available, used, not_used, expiring, total } | { code, label, available: false, message }] }
  sync: () => request('/dashboard/sync/'),
  projectUsage: () => request('/dashboard/project-usage/'),
  // { page, page_size, total, results: [{ sno, customer, device, last_used, status }] }
  projectUsageList: (project, category, { page, pageSize, search }) => {
    const query = new URLSearchParams({ page, page_size: pageSize });
    if (search) query.set('search', search);
    return request(`/dashboard/project-usage/${project}/${category}/?${query}`);
  },
};
