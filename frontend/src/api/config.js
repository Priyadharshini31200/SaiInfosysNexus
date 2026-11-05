import { request } from './client';

// Global Admin Permissions (Super Admin only; the server enforces it). Every call returns
// { permissions: [{ id, name, enabled, mapped }] }.
export const configApi = {
  adminPermissions: () => request('/config/admin-permissions/'),
  addAdminPermission: (name) => request('/config/admin-permissions/', { method: 'POST', body: { name } }),
  setAdminPermission: (id, enabled) => request(`/config/admin-permissions/${id}/`, { method: 'PATCH', body: { enabled } }),
}; 


