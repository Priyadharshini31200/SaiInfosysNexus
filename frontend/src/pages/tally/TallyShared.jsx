import { useCallback, useEffect, useState } from 'react';
import { SearchIcon } from '../../components/Icons';
import styles from './Tally.module.css';

export const REPORT_CHUNK = 100;      // largest page size the API accepts
export const REPORT_MAX_ROWS = 5000;  // safety bound: beyond this the report says so and asks for narrower filters

/**
 * Loads EVERY matching row of a paginated report (no page controls). Pages are requested one after another with the existing
 * contract (`page`, `page_size`), rows appear as they arrive, and the loop stops at REPORT_MAX_ROWS (then `truncated` is true).
 * `fetchPage(page, pageSize)` returns { results, total }. A newer filter/search cancels the previous load.
 */
export function useAllRows(fetchPage, deps, enabled = true) {
  const [state, setState] = useState({ loading: true, error: '', rows: [], total: 0, truncated: false });
  const key = JSON.stringify(deps);
  useEffect(() => {
    if (!enabled) return undefined;
    let current = true;
    setState({ loading: true, error: '', rows: [], total: 0, truncated: false });
    (async () => {
      try {
        let rows = [];
        let total = 0;
        for (let page = 1; ; page += 1) {
          const data = await fetchPage(page, REPORT_CHUNK);
          if (!current) return;
          total = data.total;
          rows = rows.concat(data.results);
          const goal = Math.min(total, REPORT_MAX_ROWS);
          const done = rows.length >= goal || data.results.length === 0;
          setState({ loading: !done, error: '', rows, total, truncated: total > REPORT_MAX_ROWS });
          if (done) return;
        }
      } catch (err) {
        if (current) setState({ loading: false, error: err.message, rows: [], total: 0, truncated: false });
      }
    })();
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);
  return state;
}

/**
 * The one list/report layout of the GSTR pages: toolbar on top, then a white body that takes all remaining height, scrolls inside
 * (wheel, touch, keyboard) with its scrollbar hidden, keeps the navy header sticky, and shows the empty/loading/error message centred.
 * The page structure (a viewport-height content area) comes from AppLayout's contentFill class.
 */
export function ListPanel({ label, toolbar, note, columns, children, loading, error, empty }) {
  const hasRows = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return (
    <section className={styles.listPanel} aria-label={label}>
      <header className={styles.toolbar}>{toolbar}</header>
      {note && <div className={styles.reportNote} role="status">{note}</div>}
      <div className={styles.listBody} tabIndex={0} role="region" aria-label={`${label} results`}>
        <table className={styles.table}>
          <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{children}</tbody>
        </table>
        {loading && !hasRows && <div className={styles.listEmpty} role="status">Loading…</div>}
        {!loading && !error && !hasRows && <div className={styles.listEmpty}>{empty}</div>}
        {error && <div className={`${styles.listEmpty} ${styles.listEmptyError}`} role="alert">{error}</div>}
      </div>
    </section>
  );
}

/** Compact toolbar controls shared by every Tally SL list/report screen (one 34px-high family). */
export function SearchBox({ value, onChange, placeholder, label = 'Search' }) {
  return (
    <label className={styles.search}>
      <SearchIcon size={15} />
      <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} maxLength={100} aria-label={label} />
    </label>
  );
}

export function Filter({ label, value, onChange, options }) {
  return (
    <label className={styles.ctl}>
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function DateFilter({ label, value, onChange }) {
  return (
    <label className={styles.ctl}>
      <span>{label}</span>
      <input type="date" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} />
    </label>
  );
}

export const PLAN_LABELS = { TRIAL: 'Trial', ANNUAL: 'Annual (1 Year)', LIFETIME: 'Lifetime' };

export const NA = <span className={styles.na}>Not available</span>;

export const STATE_LABELS = {
  pending: 'Pending', active: 'Active', expired: 'Expired', cancelled: 'Trial Cancelled', inactive: 'Inactive',
};

export function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useToast() {
  const [toast, setToast] = useState(null);
  const clear = useCallback(() => setToast(null), []);
  const notify = useCallback((type, text) => setToast({ id: Date.now(), type, text }), []);
  return { toast, clear, notify };
}
