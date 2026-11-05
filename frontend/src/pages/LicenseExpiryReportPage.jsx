import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { reportsApi } from '../api/reports';
import CustomerDetailsView from '../components/CustomerDetailsView';
import ExportButtons from '../components/ExportButtons';
import FilterSelect from '../components/FilterSelect';
import { SearchIcon } from '../components/Icons';
import Toast from '../components/Toast';
import { useAuth } from '../context/AuthContext';
import { PROJECT_ITEMS, PROJECT_PAGES } from '../layouts/navigation';
import { formatDate, formatMonth, monthKey } from '../utils/date';
import styles from '../components/CustomerList.module.css';

const ALL = 'all';
const TYPES = [
  { value: ALL, label: 'All' },
  { value: 'TRIAL', label: 'Trial' },
  { value: 'ONE_YEAR', label: '1 Year' },
];

/**
 * License Expiry Report: the expired and upcoming license expiries of every project in one table, with Search, Product,
 * License Type and Expiry Month filters (kept in the URL, so the Dashboard can link to a filtered view) and Excel / PDF / Print.
 */
export default function LicenseExpiryReportPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [state, setState] = useState({ loading: true, error: '', rows: [] });
  const [viewingId, setViewingId] = useState(null);
  const [reload, setReload] = useState(0);
  const [dirty, setDirty] = useState(false); // a license changed in the drawer: refetch once it closes
  const [toast, setToast] = useState(null);
  const clearToast = useCallback(() => setToast(null), []);
  const notify = useCallback((type, text) => setToast({ id: Date.now(), type, text }), []);

  const search = params.get('q') || '';
  const product = params.get('product') || ALL;
  const type = params.get('type') || ALL;
  const month = params.get('month') || ALL;
  const setFilter = (key, value) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (!value || value === ALL) next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true },
    );

  const canView = can('reports');
  useEffect(() => {
    if (!canView) return undefined;
    let current = true;
    reportsApi
      .licenseExpiry()
      .then(({ results }) => current && setState({ loading: false, error: '', rows: results }))
      .catch((err) => current && setState((s) => (s.rows.length ? s : { loading: false, error: err.message, rows: [] })));
    return () => {
      current = false;
    };
  }, [canView, reload]);

  const months = useMemo(() => {
    const keys = new Set(state.rows.map((r) => monthKey(r.expires_at)));
    if (month !== ALL) keys.add(month);
    return [...keys].sort().map((k) => ({ value: k, label: formatMonth(k) }));
  }, [state.rows, month]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return state.rows.filter(
      (r) =>
        (product === ALL || r.project === product) &&
        (type === ALL || r.license_type === type) &&
        (month === ALL || monthKey(r.expires_at) === month) &&
        (!term || [r.name, r.company_name, r.product, r.serial_number, r.user_id].some((v) => (v || '').toLowerCase().includes(term))),
    );
  }, [state.rows, search, product, type, month]);

  const filterText = [
    product !== ALL && `Product: ${PROJECT_ITEMS.find((p) => p.slug === product)?.label ?? product}`,
    type !== ALL && `License Type: ${TYPES.find((t) => t.value === type)?.label ?? type}`,
    month !== ALL && `Expiry Month: ${formatMonth(month)}`,
    search.trim() && `Search: ${search.trim()}`,
  ]
    .filter(Boolean)
    .join('; ');

  const viewing = state.rows.find((r) => r.id === viewingId) ?? null;
  const replace = (record) => {
    setState((s) => ({ ...s, rows: s.rows.map((r) => (r.id === record.id ? { ...r, ...record } : r)) }));
    setDirty(true);
  };
  const closeDrawer = () => {
    setViewingId(null);
    if (dirty) {
      setDirty(false);
      setReload((n) => n + 1);
    }
  };

  const { loading, error, rows } = state;
  let empty = '';
  if (!canView) empty = 'You do not have permission to view reports.';
  else if (loading) empty = 'Loading…';
  else if (error) empty = error;
  else if (rows.length === 0) empty = 'No licenses with an expiry date yet.';
  else if (visible.length === 0) empty = 'No licenses match your filters.';

  const productOptions = [{ value: ALL, label: 'All' }, ...PROJECT_ITEMS.map((p) => ({ value: p.slug, label: p.label }))];

  return (
    <section className={styles.panel} aria-label="License Expiry Report">
      <header className={`${styles.head} ${styles.headRow}`}>
        <label className={styles.search}>
          <SearchIcon size={17} />
          <input
            type="search"
            value={search}
            onChange={(e) => setFilter('q', e.target.value)}
            placeholder="Search name, company, product or serial"
            aria-label="Search report"
            disabled={!canView}
            maxLength={100}
          />
        </label>
        <FilterSelect label="Product" value={product} onChange={(v) => setFilter('product', v)} options={productOptions} disabled={!canView} />
        <FilterSelect label="License Type" value={type} onChange={(v) => setFilter('type', v)} options={TYPES} disabled={!canView} />
        <FilterSelect label="Expiry Month" value={month} onChange={(v) => setFilter('month', v)} options={[{ value: ALL, label: 'All' }, ...months]} disabled={!canView} />
        <span className={styles.count}>{canView && !loading && !error ? `Total: ${visible.length}` : ''}</span>
        {can('export_print') && canView && (
          <div className={styles.tools}>
            <ExportButtons
              menu
              disabled={loading || visible.length === 0}
              run={(fmt) => reportsApi.exportExpiry(fmt, visible.map((r) => r.id), filterText)}
              onError={(text) => notify('error', text)}
            />
          </div>
        )}
      </header>

      {!empty && (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">S.No</th>
                <th scope="col">Name</th>
                <th scope="col">Company Name</th>
                <th scope="col">Product</th>
                <th scope="col">Product Serial No.</th>
                <th scope="col">License Type</th>
                <th scope="col">Customer Serial No.</th>
                <th scope="col">Expiry Date</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row, index) => (
                <tr
                  key={row.id}
                  className={styles.row}
                  tabIndex={0}
                  onClick={() => setViewingId(row.id)}
                  onKeyDown={(e) => {
                    if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
                      e.preventDefault();
                      setViewingId(row.id);
                    }
                  }}
                  aria-label={`View details of ${row.name}`}
                >
                  <td data-label="S.No">{index + 1}</td>
                  <td data-label="Name" className={styles.name}>{row.name}</td>
                  <td data-label="Company Name">{row.company_name}</td>
                  <td data-label="Product">{row.product}</td>
                  <td data-label="Product Serial No.">{row.serial_number || '—'}</td>
                  <td data-label="License Type">{row.license_type_label}</td>
                  <td data-label="Customer Serial No.">{row.user_id}</td>
                  <td data-label="Expiry Date">{formatDate(row.expires_at)}</td>
                  <td data-label="Status">
                    <span className={`${styles.badge} ${row.expiry_status === 'Expired' ? styles.expired : styles.upcoming}`}>{row.expiry_status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {empty && <p className={`${styles.empty} ${error && canView ? styles.emptyError : ''}`}>{empty}</p>}

      {viewing && (
        <CustomerDetailsView
          project={viewing.project}
          customer={viewing}
          onEdit={(c) => navigate(PROJECT_PAGES.edit.path(viewing.project, c.id))}
          onChanged={replace}
          onNotify={notify}
          onClose={closeDrawer}
        />
      )}
      <Toast toast={toast} onDone={clearToast} />
    </section>
  );
}
