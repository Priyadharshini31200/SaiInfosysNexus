import { memo, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { tallyApi } from '../../api/tallysl';
import ExportButtons from '../../components/ExportButtons';
import Toast from '../../components/Toast';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDate, formatDateTime } from '../../utils/date';
import { DateFilter, Filter, NA, PLAN_LABELS, REPORT_MAX_ROWS, SearchBox, STATE_LABELS, useAllRows, useDebounced, useToast } from './TallyShared';
import styles from './Tally.module.css';

const CATEGORIES = [{ value: 'all', label: 'All' }, { value: 'speed', label: 'Speed User' }, { value: 'normal', label: 'Normal User' }, { value: 'new', label: 'New User' }, { value: 'one_time', label: 'One-Time User' }];
const STATUSES = ['all', 'pending', 'active', 'expired', 'cancelled', 'inactive'].map((v) => ({ value: v, label: v === 'all' ? 'All' : STATE_LABELS[v] }));
const TYPES = [{ value: 'all', label: 'All' }, { value: 'TRIAL', label: 'Trial' }, { value: 'ANNUAL', label: 'Annual' }, { value: 'LIFETIME', label: 'Lifetime' }];

const serialLink = (r) => <Link className={styles.link} to={`/tally/${r.product}/customers/${r.id}`}>{r.tally_serial}</Link>;

/** One report row. Memoised: while a long report loads chunk by chunk, rows already on screen are not re-rendered. */
const ReportRow = memo(function ReportRow({ kind, r, n }) {
  if (kind === 'performance') {
    return (
      <tr>
        <td>{n}</td><td className={styles.name}>{r.name}</td><td>{serialLink(r)}</td><td>{r.category_label}</td><td>{r.opens}</td>
        <td>{r.last_opened ? formatDateTime(r.last_opened) : NA}</td><td>{r.active_days}</td>
      </tr>
    );
  }
  if (kind === 'expiry') {
    return (
      <tr>
        <td>{n}</td><td className={styles.name}>{r.name}</td><td>{r.product_name}</td><td>{serialLink(r)}</td><td>{PLAN_LABELS[r.license_plan] || r.license_plan}</td>
        <td>{r.activated_at ? formatDate(r.activated_at) : NA}</td>
        <td>{r.license_type === 'LIFETIME' ? 'Never' : r.expires_at ? formatDate(r.expires_at) : NA}</td>
        <td>{STATE_LABELS[r.state] || r.state}</td>
      </tr>
    );
  }
  return (
    <tr>
      <td>{n}</td><td>{serialLink(r)}</td><td>{r.tally_email || NA}</td><td>{r.tally_expiry_date ? formatDate(r.tally_expiry_date) : NA}</td>
    </tr>
  );
});

/** Tally SL reports. kind = 'expiry' (Nexus licence expiry) | 'tally' (Tally serial, email, TSS expiry) | 'performance' (user category, opens, last opened, active days). */
export default function TallyReportPage({ kind }) {
  const { anyCan, products, loaded } = useTallyAccess();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const term = useDebounced(search);
  const { toast, clear, notify } = useToast();
  const product = params.get('product') || 'all';
  const status = params.get('status') || 'all';
  const type = params.get('type') || 'all';
  const isExpiry = kind === 'expiry';
  const isPerf = kind === 'performance';
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const category = params.get('category') || 'all';
  const filters = useMemo(() => ({ product, search: term, from, to, ...(isExpiry ? { status, license_type: type } : {}), ...(isPerf ? { category } : {}) }), [product, term, status, type, isExpiry, isPerf, from, to, category]);
  const allowed = anyCan('reports');
  const data = useAllRows((page, page_size) => (isExpiry ? tallyApi.expiryReport : isPerf ? tallyApi.performanceReport : tallyApi.tallyReport)({ ...filters, page, page_size }), [kind, filters], allowed);
  const { rows, loading, error, total, truncated } = data;
  const set = (k, v) => setParams((p) => { const n = new URLSearchParams(p); if (!v || v === 'all') n.delete(k); else n.set(k, v); return n; }, { replace: true });
  const productOptions = [{ value: 'all', label: 'All' }, ...products.filter((p) => p.actions?.reports).map((p) => ({ value: p.code, label: p.name }))];
  const dateLabel = 'From'; // the date range applies to: expiry date (License Expiry), last opened (User Performance), TSS expiry (Tally Details)

  if (loaded && !allowed) return <p>You do not have permission to view reports.</p>;
  const cols = isPerf
    ? ['S.No', 'Customer Name', 'Tally Serial No.', 'User Category', 'Total Opens', 'Last Opened', 'Active Days']
    : isExpiry
      ? ['S.No', 'Customer Name', 'Product', 'Tally Serial No.', 'License Plan', 'Nexus Activation Date', 'Nexus Expiry Date', 'Status']
      : ['S.No', 'Tally Serial No.', 'Tally Email ID', 'Tally Expiry Date'];

  return (
    <section className={`${styles.panel} ${styles.reportPanel}`} aria-label={isExpiry ? 'License Expiry Report' : isPerf ? 'User Performance Report' : 'Tally Details Report'}>
      <header className={styles.toolbar}>
        <SearchBox value={search} onChange={setSearch} placeholder="Search customer or serial" label="Search report" />
        <Filter label="Product" value={product} onChange={(v) => set('product', v)} options={productOptions} />
        {isExpiry && <Filter label="Plan" value={type} onChange={(v) => set('type', v)} options={TYPES} />}
        {isExpiry && <Filter label="Status" value={status} onChange={(v) => set('status', v)} options={STATUSES} />}
        {isPerf && <Filter label="Category" value={category} onChange={(v) => set('category', v)} options={CATEGORIES} />}
        <DateFilter label={dateLabel} value={from} onChange={(v) => set('from', v)} />
        <DateFilter label="To" value={to} onChange={(v) => set('to', v)} />
        <div className={styles.right}>
          <ExportButtons menu disabled={loading || total === 0} onError={(t) => notify('error', t)}
            run={(fmt) => (isExpiry ? tallyApi.exportExpiry : isPerf ? tallyApi.exportPerformance : tallyApi.exportTally)(fmt, filters)} />
        </div>
      </header>
      {(truncated || (loading && rows.length > 0)) && (
        <div className={styles.reportNote} role="status">
          {loading
            ? `Loading… ${rows.length.toLocaleString()} of ${Math.min(total, REPORT_MAX_ROWS).toLocaleString()} records${truncated ? ` (report limited to the first ${REPORT_MAX_ROWS.toLocaleString()} of ${total.toLocaleString()})` : ''}`
            : `Showing the first ${REPORT_MAX_ROWS.toLocaleString()} of ${total.toLocaleString()} records. Narrow the filters or use Export for the complete list.`}
        </div>
      )}
      <div className={styles.reportBody}>
        <table className={styles.table}>
          <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => <ReportRow key={r.id} kind={kind} r={r} n={i + 1} />)}
          </tbody>
        </table>
        {loading && rows.length === 0 && <div className={styles.reportEmpty} role="status">Loading…</div>}
        {!loading && !error && rows.length === 0 && <div className={styles.reportEmpty}>No records match.</div>}
        {error && <div className={`${styles.reportEmpty} ${styles.reportEmptyError}`} role="alert">{error}</div>}
      </div>
      <Toast toast={toast} onDone={clear} />
    </section>
  );
}
