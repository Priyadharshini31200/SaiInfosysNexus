import { request } from './client';

export const authApi = {
  csrf: () => request('/auth/csrf/'),
  login: (username, password) =>
    request('/auth/login/', { method: 'POST', body: { username, password }, skipExpiry: true }),
  logout: () => request('/auth/logout/', { method: 'POST', skipExpiry: true }),
  me: () => request('/auth/me/', { skipExpiry: true }),
  changePassword: (current_password, new_password, confirm_password) =>
    request('/auth/change-password/', {
      method: 'POST',
      body: { current_password, new_password, confirm_password },
    }),
};
