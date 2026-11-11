import { request } from './client';

// Global Admin Permissions (Super Admin only; the server enforces it). Every call returns
// { permissions: [{ id, name, enabled, mapped }] }.
export const configApi = {
  projectPermissions: () => request('/config/project-permissions/'),
  saveProjectPermissions: (user_id, grants) => request('/config/project-permissions/', { method: 'PUT', body: { user_id, grants } }),
  resetAdminPassword: (id, new_password, confirm_password) => request(`/config/admins/${id}/reset-password/`, { method: 'POST', body: { new_password, confirm_password } }),
  adminPermissions: () => request('/config/admin-permissions/'),
  addAdminPermission: (name) => request('/config/admin-permissions/', { method: 'POST', body: { name } }),
  setAdminPermission: (id, enabled) => request(`/config/admin-permissions/${id}/`, { method: 'PATCH', body: { enabled } }),
}; 


