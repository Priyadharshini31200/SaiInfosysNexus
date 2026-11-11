import { request } from './client';

// Per-project customer registration and license management. The server enforces Super Admin / Admin permissions.
const base = (project) => `/projects/${project}/customers/`;

// Calls that email the customer: bounded so the button always comes back. The server still finishes and records the
// outcome, so staff are pointed at the Credentials Email status rather than told it failed.
const MAIL = {
  timeoutMs: 60000,
  timeoutMessage: 'The mail server is taking too long. Check the customer’s Credentials Email status before trying again.',
};

export const registrationsApi = {
  list: (project) => request(base(project)),
  get: (project, id) => request(`${base(project)}${id}/`),
  requests: (project) => request(`/projects/${project}/registration-requests/`),
  rejectRequest: (project, id, reason) =>
    request(`/projects/${project}/registration-requests/${id}/reject/`, { method: 'POST', body: reason ? { reason } : {} }),
  sync: (project) => request(`/projects/${project}/sync/`),
  activity: (project, customerId) => request(`/projects/${project}/activity/${customerId ? `?customer=${customerId}` : ''}`),
  generateKey: (project) => request(`/projects/${project}/activation-key/`, { method: 'POST' }),
  generateSerial: (project) => request(`/projects/${project}/serial-number/`, { method: 'POST' }),
  register: (project, body) => request(base(project), { method: 'POST', body }),
  update: (project, id, body) => request(`${base(project)}${id}/`, { method: 'PATCH', body }),
  remove: (project, id, confirmSerial) => request(`${base(project)}${id}/`, { method: 'DELETE', body: { confirm_serial: confirmSerial } }),
  extendTrial: (project, id, days) => request(`${base(project)}${id}/extend-trial/`, { method: 'POST', body: { days } }),
  cancelTrial: (project, id) => request(`${base(project)}${id}/cancel-trial/`, { method: 'POST' }),
  inactivate: (project, id) => request(`${base(project)}${id}/inactivate/`, { method: 'POST' }),
  renew: (project, id, note) => request(`${base(project)}${id}/renew/`, { method: 'POST', body: note ? { note } : {} }),
  renewals: (project, id) => request(`${base(project)}${id}/renewals/`),
  device: (project, id) => request(`${base(project)}${id}/device/`),
  revokeDevice: (project, id, body) => request(`${base(project)}${id}/device/revoke/`, { method: 'POST', body }),
  deviceTransfers: (project, status = 'pending') => request(`/projects/${project}/device-transfer-requests/?status=${status}`),
  decideDeviceTransfer: (project, requestId, decision, body) =>
    request(`/projects/${project}/device-transfer-requests/${requestId}/${decision}/`, { method: 'POST', body: body || {} }),
  transferDevice: (project, id, body) => request(`${base(project)}${id}/device/transfer/`, { method: 'POST', body }),
  temporaryPassword: (project) => request(`/projects/${project}/temporary-password/`, { method: 'POST' }),
  reissueKey: (project, id) => request(`${base(project)}${id}/reissue-activation-key/`, { method: 'POST', ...MAIL }),
  retryEmail: (project, id) => request(`${base(project)}${id}/retry-credentials-email/`, { method: 'POST', ...MAIL }),
  resendCredentials: (project, id) => request(`${base(project)}${id}/resend-credentials/`, { method: 'POST', ...MAIL }),
  resetPassword: (project, id, legacyReference) =>
    request(`${base(project)}${id}/reset-password/`, {
      method: 'POST',
      body: legacyReference ? { legacy_reference: legacyReference } : {},
      ...MAIL,
    }),
};
