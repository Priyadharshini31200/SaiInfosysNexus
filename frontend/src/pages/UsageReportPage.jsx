import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { reportsApi } from '../api/reports';
import Alert from '../components/Alert';
import ExportButtons from '../components/ExportButtons';
import FilterSelect from '../components/FilterSelect';
import { SearchIcon } from '../components/Icons';
import Toast from '../components/Toast';
import { useAuth } from '../context/AuthContext';
import { PROJECT_ITEMS, USAGE_STATUSES } from '../layouts/navigation';
import { formatDate } from '../utils/date';
import styles from '../components/CustomerList.module.css';

const ALL = 'all';
const STATUS_OPTIONS = [{ value: ALL, label: 'All' }, ...Object.entries(USAGE_STATUSES).map(([value, label]) => ({ value, label }))];
const PROJECT_OPTIONS = [{ value: ALL, label: 'All' }, ...PROJECT_ITEMS.map((p) => ({ value: p.slug, label: p.label }))];

/**
 * Usage Report: the Used / Not Used customers and devices of one project or all of them, read from the projects' own
 * databases. The Dashboard opens it with the project and count already applied; every filter lives in the URL, so a refresh
 * keeps it and Back returns to the previous page.
 */
export default function UsageReportPage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const projectParam = params.get('project');
  const statusParam = params.get('status');
  const project = PROJECT_OPTIONS.some((o) => o.value === projectParam) ? projectParam : ALL;
  const status = STATUS_OPTIONS.some((o) => o.value === statusParam) ? statusParam : ALL;
  const q = params.get('q') || '';
  const [search, setSearch] = useState(q);
  const [state, setState] = useState({ loading: true, error: '', rows: [], unavailable: [] });
  const [toast, setToast] = useState(null);
  const clearToast = useCallback(() => setToast(null), []);
  const canView = can('reports');

  const setFilter = useCallback(
    (key, value) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (!value || value === ALL) next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  // Typing applies to the URL (and so to the data) shortly after it stops; Back / Forward put the typed text back.
  useEffect(() => {
    const timer = setTimeout(() => search.trim() !== q && setFilter('q', search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search, q, setFilter]);
  useEffect(() => {
    setSearch((current) => (current.trim() === q ? current : q));
  }, [q]);

  useEffect(() => {
    if (!canView) return undefined;
    let current = true;
    setState((s) => ({ ...s, loading: true }));
    reportsApi
      .usage({ project: project === ALL ? '' : project, status: status === ALL ? '' : status, search: q })
      .then(({ results, unavailable }) => current && setState({ loading: false, error: '', rows: results, unavailable }))
      .catch((err) => current && setState({ loading: false, error: err.message, rows: [], unavailable: [] }));
    return () => {
      current = false;
    };
  }, [canView, project, status, q]);

  const filters = useMemo(() => ({ project, status, search: q }), [project, status, q]);
  const { loading, error, rows, unavailable } = state;
  let empty = '';
  if (!canView) empty = 'You do not have permission to view reports.';
  else if (error) empty = error;
  else if (loading && rows.length === 0) empty = 'Loading…';
  else if (rows.length === 0) empty = q || project !== ALL || status !== ALL ? 'No records match your filters.' : 'No records found.';

  return (
    <section className={styles.panel} aria-label="Usage Report" aria-busy={loading}>
      <header className={`${styles.head} ${styles.headRow}`}>
        <label className={styles.search}>
          <SearchIcon size={17} />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search customer, device or serial"
            aria-label="Search report"
            disabled={!canView}
            maxLength={100}
          />
        </label>
        <FilterSelect label="Project" value={project} onChange={(v) => setFilter('project', v)} options={PROJECT_OPTIONS} disabled={!canView} />
        <FilterSelect label="Status" value={status} onChange={(v) => setFilter('status', v)} options={STATUS_OPTIONS} disabled={!canView} />
        <span className={styles.count}>{canView && !error && !(loading && rows.length === 0) ? `Total: ${rows.length}` : ''}</span>
        {can('export_print') && canView && (
          <div className={styles.tools}>
            <ExportButtons
              menu
              disabled={loading || rows.length === 0}
              run={(fmt) => reportsApi.exportUsage(fmt, filters)}
              onError={(text) => setToast({ id: Date.now(), type: 'error', text })}
            />
          </div>
        )}
      </header>

      {unavailable.length > 0 && (
        <div className={styles.notice}>
          <Alert type="error">{`${unavailable.join(', ')} data is currently unavailable.`}</Alert>
        </div>
      )}

      {!empty && (
        <div className={styles.scroll}>
          <table className={`${styles.table} ${styles.usage}`}>
            <thead>
              <tr>
                <th scope="col">S.No</th>
                <th scope="col">Customer / Company Name</th>
                <th scope="col">Product</th>
                <th scope="col">Device / Serial Number</th>
                <th scope="col">Last Used Date</th>
                <th scope="col">Usage Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.product}-${index}`}>
                  <td data-label="S.No">{index + 1}</td>
                  <td data-label="Customer / Company Name" className={styles.name}>{row.customer || '—'}</td>
                  <td data-label="Product">{row.product}</td>
                  <td data-label="Device / Serial Number">{row.device || '—'}</td>
                  <td data-label="Last Used Date">{formatDate(row.last_used)}</td>
                  <td data-label="Usage Status">
                    <span className={`${styles.badge} ${row.status === 'Used' ? styles.statusUsed : styles.statusNotUsed}`}>{row.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {empty && <p className={`${styles.empty} ${error && canView ? styles.emptyError : ''}`}>{empty}</p>}
      <Toast toast={toast} onDone={clearToast} />
    </section>
  );
}
