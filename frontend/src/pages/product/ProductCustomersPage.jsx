import { memo, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import ProductCustomerDrawer from './ProductCustomerDrawer';
import { integrationsApi } from '../../api/integrations';
import ExportButtons from '../../components/ExportButtons';
import Toast from '../../components/Toast';
import { formatDate, formatDateTime } from '../../utils/date';
import { Filter, ListPanel, REPORT_MAX_ROWS, SearchBox, useAllRows, useDebounced, useToast } from '../tally/TallyShared';
import styles from '../tally/Tally.module.css';

const COLS = ['S.No', 'Customer Name', 'Email', 'Contact Number', 'Serial Number / User ID', 'License Type', 'Trial Days', 'License Status', 'Expiry Date', 'Active Device', 'Activation Date', 'Last Login', 'Usage Category'];
export const TYPE_LABEL = { TRIAL: 'Trial', ONE_YEAR: '1 Year', LIFETIME: 'Lifetime' };
export const STATUS_LABEL = { pending: 'Pending Approval', not_activated: 'Not Activated', active: 'Active', expired: 'Expired', cancelled: 'Cancelled', inactive: 'Inactive' };
const USAGE_LABEL = { speed: 'Speed', normal: 'Normal', new: 'New', one_time: 'One-Time', not_used: 'Not Used' };
const TYPES = [{ value: 'all', label: 'All' }, ...Object.entries(TYPE_LABEL).map(([value, label]) => ({ value, label }))];
const STATUSES = [{ value: 'all', label: 'All' }, ...Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }))];
const FILTER_NAMES = { used: 'Used', not_used: 'Not Used', expired: 'Expired', expiring: 'This Month Expiring', trial: 'Trial', annual: '1 Year', lifetime: 'Lifetime', active_devices: 'Active Devices', pending: 'Pending Approval' };

const Row = memo(function Row({ code, r, n, onOpen }) {
  return (
    <tr className={styles.row} tabIndex={0} onClick={() => onOpen(r.id)} onKeyDown={(e) => e.key === 'Enter' && onOpen(r.id)}>
      <td>{n}</td><td className={styles.name}>{r.name}</td><td>{r.email}</td><td>{r.contact_number}</td>
      <td><Link className={styles.link} to={`/product/${code}/customers/${r.id}`} onClick={(e) => e.stopPropagation()}>{r.serial_number}</Link></td>
      <td>{TYPE_LABEL[r.license_type]}</td><td>{r.trial_days ?? '—'}</td><td>{STATUS_LABEL[r.status]}</td>
      <td>{r.license_type === 'LIFETIME' ? 'Never' : r.expires_at ? formatDate(r.expires_at) : '—'}</td><td>{r.active_device || '—'}</td><td>{r.activated_at ? formatDate(r.activated_at) : '—'}</td><td>{r.last_login ? formatDateTime(r.last_login) : '—'}</td>
      <td>{USAGE_LABEL[r.usage_category] || '—'}</td>
    </tr>
  );
});

/** Mothys Customers: same list as GSTR2Tally. Customers arrive automatically from the Mothys backend (API); nothing is typed in here. */
export default function ProductCustomersPage() {
  const { code, id: openId } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [reload, setReload] = useState(0);
  const { toast, clear, notify } = useToast();
  const term = useDebounced(search);
  const type = params.get('type') || 'all';
  const status = params.get('status') || 'all';
  const filter = params.get('filter') || '';
  const usage = params.get('usage') || '';
  const filters = useMemo(() => ({ search: term, license_type: type, status, filter, usage }), [term, type, status, filter, usage]);
  const { rows, loading, error, total, truncated } = useAllRows((page, page_size) => integrationsApi.productCustomers(code, { ...filters, page, page_size }), [code, filters, reload]);
  const set = (k, v) => setParams((p) => { const n = new URLSearchParams(p); if (!v || v === 'all') n.delete(k); else n.set(k, v); return n; }, { replace: true });
  const keep = params.toString() ? `?${params}` : '';
  const active = filter ? FILTER_NAMES[filter] : usage ? `${USAGE_LABEL[usage]} users` : '';
  const note = active ? `Showing: ${active} (from the Dashboard).` : truncated ? `Showing the first ${REPORT_MAX_ROWS.toLocaleString()} of ${total.toLocaleString()} customers.` : null;
  return (
    <>
      <ListPanel label="Customers" columns={COLS} loading={loading} error={error} empty="No customers yet. They appear automatically when the Mothys backend registers them through the Nexus API." note={note}
        toolbar={(
          <>
            <SearchBox value={search} onChange={setSearch} placeholder="Search name, serial, email or phone" label="Search customers" />
            <Filter label="License Type" value={type} onChange={(v) => set('type', v)} options={TYPES} />
            <Filter label="Status" value={status} onChange={(v) => set('status', v)} options={STATUSES} />
            {(filter || usage) && <button type="button" className={styles.refresh} onClick={() => { set('filter', ''); set('usage', ''); }}>Clear dashboard filter</button>}
            <span className={styles.count}>Total: {total}</span>
            <div className={styles.right}>
              <ExportButtons menu disabled={loading || total === 0} onError={(t) => notify('error', t)} run={(fmt) => integrationsApi.exportProductCustomers(code, fmt, { ...filters, license_type: type })} />
              <button type="button" className={styles.refresh} onClick={() => setReload((n) => n + 1)} disabled={loading} aria-label="Refresh customer list">{loading ? 'Refreshing…' : 'Refresh'}</button>
            </div>
          </>
        )}
      >
        {rows.map((r, i) => <Row key={r.id} code={code} r={r} n={i + 1} onOpen={(id) => navigate(`/product/${code}/customers/${id}${keep}`)} />)}
      </ListPanel>
      {openId && <ProductCustomerDrawer key={openId} code={code} id={openId} onClose={() => navigate(`/product/${code}/customers${keep}`)} onChanged={() => setReload((n) => n + 1)} onNotify={notify} />}
      <Toast toast={toast} onDone={clear} />
    </>
  );
}
