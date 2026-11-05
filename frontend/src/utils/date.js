// dd/mm/yyyy in India Standard Time, matching how the server draws day boundaries.
const format = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Asia/Kolkata' });

export const formatDate = (iso) => (iso ? format.format(new Date(iso)) : '—');

const stamp = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });

export const formatDateTime = (iso) => (iso ? stamp.format(new Date(iso)).replace(',', '') : '—');

const monthParts = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', timeZone: 'Asia/Kolkata' });

/** "YYYY-MM" of a moment, in India Standard Time (default: now). */
export const monthKey = (iso) => {
  const parts = Object.fromEntries(monthParts.formatToParts(iso ? new Date(iso) : new Date()).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}`;
};

const monthName = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });

/** "October 2026" for "2026-10". */
export const formatMonth = (key) => monthName.format(new Date(`${key}-01T00:00:00Z`));
