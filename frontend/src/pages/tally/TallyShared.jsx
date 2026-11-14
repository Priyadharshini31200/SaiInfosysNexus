import { useCallback, useEffect, useState } from 'react';
import { SearchIcon } from '../../components/Icons';
import styles from './Tally.module.css';

export const PAGE_SIZES = [10, 25, 50, 100];

/** Loads a paged list; `load(page, pageSize)` returns { results, total }. Reloads when `deps` change. */
export function usePaged(load, deps, enabled = true) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZES[0]);
  const [state, setState] = useState({ loading: true, error: '', results: [], total: 0 });
  const key = JSON.stringify(deps);
  useEffect(() => setPage(1), [key]);
  useEffect(() => {
    if (!enabled) return undefined;
    let current = true;
    setState((s) => ({ ...s, loading: true }));
    load(page, pageSize)
      .then((d) => current && setState({ loading: false, error: '', results: d.results, total: d.total }))
      .catch((err) => current && setState({ loading: false, error: err.message, results: [], total: 0 }));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, key, enabled]);
  return { ...state, page, pageSize, setPage, setPageSize };
}

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

export function Pager({ page, pageSize, total, setPage, setPageSize, showTotal = true }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className={styles.pager}>
      {showTotal && <span>Total: {total}</span>}
      <label>
        Rows{' '}
        <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
      <span>Page {page} of {pages}</span>
      <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
    </div>
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
