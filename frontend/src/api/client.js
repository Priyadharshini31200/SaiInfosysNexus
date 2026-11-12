// Follow the app mount path: /api locally, /nexus/api behind the live proxy.
export const API_BASE = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api`;

const CSRF_COOKIE = 'nexus_csrftoken';
export const SESSION_EXPIRED_EVENT = 'nexus:session-expired';

export class ApiError extends Error {
  constructor(message, { status, errors = {} } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.errors = errors;
  }
}

function readCookie(name) {
  const match = document.cookie.split('; ').find((row) => row.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split('=').slice(1).join('=')) : null;
}

const UNAVAILABLE_MESSAGE = 'The Nexus server is temporarily unavailable. Please try again in a minute; if it persists, contact support.';

async function getCsrfToken() {
  const existing = readCookie(CSRF_COOKIE);
  if (existing) return existing;
  let res;
  try {
    res = await fetch(`${API_BASE}/auth/csrf/`, { credentials: 'same-origin' });
  } catch {
    throw new ApiError('Unable to reach the server. Check your connection and try again.', { status: 0 });
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* a proxy error page (502/503/504) is HTML, not JSON */
  }
  if (!res.ok || !body?.csrfToken) {
    throw new ApiError(body?.message || ([502, 503, 504].includes(res.status) ? UNAVAILABLE_MESSAGE : 'Could not start a secure session. Please try again.'), {
      status: res.status,
    });
  }
  return body.csrfToken;
}

/**
 * request(path, { method, body, skipExpiry })
 * - sends the session cookie and CSRF header
 * - throws ApiError with the server's {message, errors}
 * - a 401 on an authenticated call broadcasts a session-expired event
 */
export async function request(path, { method = 'GET', body, skipExpiry = false, timeoutMs, timeoutMessage } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRFToken'] = await getCsrfToken();

  // Optional upper bound, so a slow mail server can never leave a "Sending…" button spinning indefinitely.
  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller?.signal,
    });
  } catch {
    if (controller?.signal.aborted) {
      throw new ApiError(timeoutMessage || 'The server is taking too long to respond. Please try again.', { status: 0 });
    }
    throw new ApiError('Unable to reach the server. Check your connection and try again.', { status: 0 });
  } finally {
    if (timer) clearTimeout(timer);
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }

  if (!res.ok) {
    if (res.status === 401 && !skipExpiry) {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    // A reverse proxy answers 502/503/504 with its own HTML page when Nexus is down or restarting: say so plainly.
    const fallback =
      res.status === 429
        ? 'Too many attempts. Please wait a minute and try again.'
        : [502, 503, 504].includes(res.status)
          ? UNAVAILABLE_MESSAGE
          : res.status >= 500
            ? 'Something went wrong on the server. Please try again.'
            : 'Request failed.';
    throw new ApiError(data?.message || fallback, { status: res.status, errors: data?.errors || {} });
  }
  return data;
}

/** POST a JSON body and receive a file: { blob, filename }. Errors are raised like request() does. */
export async function download(path, body) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { Accept: '*/*', 'Content-Type': 'application/json', 'X-CSRFToken': await getCsrfToken() },
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Unable to reach the server. Check your connection and try again.', { status: 0 });
  }
  if (!res.ok) {
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* non-JSON body */
    }
    if (res.status === 401) window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    throw new ApiError(data?.message || (res.status >= 500 ? 'Something went wrong on the server. Please try again.' : 'Export failed.'), {
      status: res.status,
    });
  }
  return { blob: await res.blob(), filename: res.headers.get('X-Filename') || 'export' };
}
