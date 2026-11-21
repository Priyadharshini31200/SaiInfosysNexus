import { request } from './client';

export const companyApi = {
  get: () => request('/company/profile/'),
  save: (form) => request('/company/profile/', { method: 'PUT', body: form }), // FormData: name, gstin, logo?, remove_logo?
};
