import { memo, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import TallyCustomerDrawer from './TallyCustomerDrawer';
import { tallyApi } from '../../api/tallysl';
import ExportButtons from '../../components/ExportButtons';
import Toast from '../../components/Toast';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDate } from '../../utils/date';
import { Filter, ListPanel, PLAN_LABELS, REPORT_MAX_ROWS, SearchBox, STATE_LABELS, useAllRows, useDebounced, useToast } from './TallyShared';
import styles from './Tally.module.css';

const COLS = ['S.No', 'Customer Name', 'Company Name', 'Contact Number', 'Email', 'Tally Serial / User ID', 'License Type', 'License Status', 'Expiry Date'];
const TYPES = [{ value: 'all', label: 'All' }, { value: 'TRIAL', label: 'Trial' }, { value: 'ANNUAL', label: '1 Year' }, { value: 'LIFETIME', label: 'Lifetime' }];
const STATUSES = ['all', 'pending', 'active', 'expired', 'cancelled', 'inactive'].map((v) => ({ value: v, label: v === 'all' ? 'All' : STATE_LABELS[v] }));
const TYPE_LABEL = { ...PLAN_LABELS, ANNUAL: '1 Year' };

/** One customer row; the Tally Serial Number is a link that opens that customer's details. Memoised for long lists. */
const CustomerRow = memo(function CustomerRow({ r, n, onOpen }) {
  return (
    <tr className={styles.row} tabIndex={0} onClick={() => onOpen(r.id)} onKeyDown={(e) => e.key === 'Enter' && onOpen(r.id)}>
      <td>{n}</td>
      <td className={styles.name}>{r.name}</td>
      <td>{r.company_name || '—'}</td>
      <td>{r.contact_number}</td>
      <td>{r.email}</td>
      <td>
        <Link className={styles.link} to={`/tally/${r.product}/customers/${r.id}`} onClick={(e) => e.stopPropagation()}>{r.tally_serial}</Link>
      </td>
      <td>{TYPE_LABEL[r.license_plan] || r.license_plan}</td>
      <td>{STATE_LABELS[r.state] || r.state}</td>
      <td>{r.license_plan === 'LIFETIME' ? 'Never' : r.expires_at ? formatDate(r.expires_at) : '—'}</td>
    </tr>
  );
});

export default function TallyCustomersPage() {
  const { product, id: openId } = useParams();
  const navigate = useNavigate();
  const { can, loaded } = useTallyAccess();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [reload, setReload] = useState(0);
  const { toast, clear, notify } = useToast();
  const term = useDebounced(search);
  const type = params.get('type') || 'all';
  const status = params.get('status') || 'all';
  const allowed = can('view', product);
  const filters = useMemo(() => ({ search: term, license_plan: type, state: status }), [term, type, status]);
  const { rows, loading, error, total, truncated } = useAllRows((page, page_size) => tallyApi.customers(product, { ...filters, page, page_size }), [product, filters, reload], allowed);
  const set = (k, v) => setParams((p) => { const n = new URLSearchParams(p); if (!v || v === 'all') n.delete(k); else n.set(k, v); return n; }, { replace: true });
  const open = (id) => navigate(`/tally/${product}/customers/${id}${params.get('transfer') ? `?transfer=${params.get('transfer')}` : ''}`);
  const closeDrawer = () => navigate(`/tally/${product}/customers`);

  if (loaded && !allowed) return <p>You do not have permission to view this product.</p>;
  const note = truncated
    ? (loading ? `Loading… ${rows.length.toLocaleString()} of ${REPORT_MAX_ROWS.toLocaleString()} (limited to the first ${REPORT_MAX_ROWS.toLocaleString()} of ${total.toLocaleString()})` : `Showing the first ${REPORT_MAX_ROWS.toLocaleString()} of ${total.toLocaleString()} customers. Use Search or the filters to narrow the list.`)
    : (loading && rows.length > 0 ? `Loading… ${rows.length.toLocaleString()} of ${total.toLocaleString()} customers` : null);
  return (
    <>
      <ListPanel
        label="Customers"
        columns={COLS}
        loading={loading}
        error={error}
        empty="No customers yet."
        note={note}
        toolbar={(
          <>
            <SearchBox value={search} onChange={setSearch} placeholder="Search name, serial, email or phone" label="Search customers" />
            <Filter label="License Type" value={type} onChange={(v) => set('type', v)} options={TYPES} />
            <Filter label="Status" value={status} onChange={(v) => set('status', v)} options={STATUSES} />
            <span className={styles.count}>Total: {total}</span>
            <div className={styles.right}>
              <ExportButtons menu disabled={loading || total === 0} onError={(t) => notify('error', t)} run={(fmt) => tallyApi.exportCustomers(product, fmt, filters)} />
              <button type="button" className={styles.refresh} onClick={() => setReload((n) => n + 1)} disabled={loading} aria-label="Refresh customer list">{loading ? 'Refreshing…' : 'Refresh'}</button>
            </div>
          </>
        )}
      >
        {rows.map((r, i) => <CustomerRow key={r.id} r={r} n={i + 1} onOpen={open} />)}
      </ListPanel>
      {openId && (
        <TallyCustomerDrawer key={openId} product={product} id={openId} highlight={Number(params.get('transfer')) || 0} onClose={closeDrawer}
          onChanged={() => setReload((n) => n + 1)} onDeleted={() => { setReload((n) => n + 1); closeDrawer(); }} onNotify={notify} />
      )}
      <Toast toast={toast} onDone={clear} />
    </>
  );
}
