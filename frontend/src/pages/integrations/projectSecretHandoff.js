let pending;
let expiry;

function clearPending() {
  pending = undefined;
  clearTimeout(expiry);
  globalThis.removeEventListener?.('pagehide', clearPending);
}

// The create form and API workspace briefly share this in-memory slot.
export function stageProjectSecret(code, secret) {
  clearPending();
  pending = { code, secret };
  expiry = setTimeout(clearPending, 5000);
  globalThis.addEventListener?.('pagehide', clearPending, { once: true });
}

export function takeProjectSecret(code) {
  const secret = pending?.code === code ? pending.secret : '';
  clearPending();
  return secret;
}
