import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { reportsApi } from '../api/reports';
import { useAuth } from '../context/AuthContext';
import { PROJECT_PAGES } from '../layouts/navigation';
import { formatDate } from '../utils/date';
import CustomerDetailsView from './CustomerDetailsView';
import ExportButtons from './ExportButtons';
import FilterSelect from './FilterSelect';
import { PlusIcon, SearchIcon } from './Icons';
import styles from './CustomerList.module.css';

// Trial / 1 Year / Lifetime is the headline; the badge colour carries the current state.
const TYPES = [
  { value: 'all', label: 'All' },
  { value: 'TRIAL', label: 'Trial' },
  { value: 'ONE_YEAR', label: '1 Year' },
  { value: 'LICENSED', label: 'Lifetime' },
];

/** The real end of the licence: none for Lifetime, and a device trial has none until a computer activates it. */
const expiryText = (c) => c.expiry_label || (c.effective_expires_at ? formatDate(c.effective_expires_at) : c.license_type === 'LICENSED' ? 'Lifetime' : '—');

const BADGES = { active: styles.active, expired: styles.warn, cancelled: styles.muted, inactive: styles.muted };

/**
 * How the License Type cell reads. A trial (and any cancelled / inactive / expired licence) keeps its status badge. An active
 * 1 Year licence shows its term in an outlined tag; an active Lifetime licence is plain text.
 */
function licenseTypeCell(customer) {
  if (customer.state === 'active' && customer.license_type === 'LICENSED') return <span className={styles.lifetime}>Lifetime</span>;
  if (customer.state === 'active' && customer.license_type === 'ONE_YEAR') return <span className={styles.term}>1 Year</span>;
  return <span className={`${styles.badge} ${BADGES[customer.state]}`}>{customer.status_label}</span>;
}

/**
 * The one Customer List, used by every project: search, + Add Customer, and per-customer View / Edit /
 * license actions. `customers` is null while loading, or when the user may not view the list.
 */
export default function CustomerList({ project, label, customers, loading, error, onChanged, onDeleted, onEdit, onNotify }) {
  const { can } = useAuth();
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [status, setStatus] = useState('all');
  const [type, setType] = useState('all');
  const [viewingId, setViewingId] = useState(null);
  const viewing = customers?.find((c) => c.id === viewingId) ?? null; // follows license actions done in the panel

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (customers ?? []).filter(
      (c) =>
        (status === 'all' || c.state === status) &&
        (type === 'all' || c.license_type === type) &&
        (!term || [c.name, c.company_name, c.user_id, c.contact_number, c.email, c.gstin, c.serial_number].some((v) => (v || '').toLowerCase().includes(term))),
    );
  }, [customers, search, type, status]);

  const currentPage = Math.min(page, Math.max(1, Math.ceil(visible.length / pageSize)));
  const pageRows = visible.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const filterText = [status !== 'all' && `Status: ${status}`,type !== 'all' && `License Type: ${TYPES.find((t) => t.value === type).label}`, search.trim() && `Search: ${search.trim()}`]
    .filter(Boolean)
    .join('; ');

  const canView = can('view', project);
  let empty = '';
  if (!canView) empty = 'You do not have permission to view the customer list.';
  else if (loading) empty = 'Loading…';
  else if (error) empty = error;
  else if (!customers || customers.length === 0) empty = `No customers registered for ${label} yet.`;
  else if (visible.length === 0) empty = 'No customers match your search or filter.';

  return (
    <section className={styles.panel} aria-label={`${label} customers`}>
      <header className={styles.head}>
        <label className={styles.search}>
          <SearchIcon size={17} />
          <input
            type="search"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            placeholder="Search name, company, user ID, phone or serial"
            aria-label="Search customers"
            disabled={!canView}
            maxLength={100}
          />
        </label>
        <FilterSelect label="License Type" value={type} onChange={(value) => { setType(value); setPage(1); }} options={TYPES} disabled={!canView} />
        <FilterSelect label="Status" value={status} onChange={(value) => { setStatus(value); setPage(1); }} options={[
          {value: 'all', label: 'All'}, {value: 'active', label: 'Active'}, {value: 'expired', label: 'Expired'},
          {value: 'cancelled', label: 'Cancelled'}, {value: 'inactive', label: 'Inactive'},
        ]} disabled={!canView} />
        <span className={styles.count}>{canView && customers ? `Total: ${visible.length}` : ''}</span>
        <div className={styles.tools}>
          {can('export_print', project) && canView && (
            <ExportButtons
              disabled={!customers || visible.length === 0}
              run={(fmt) => reportsApi.exportCustomers(project, fmt, visible.map((c) => c.id), filterText)}
              onError={(text) => onNotify('error', text)}
            />
          )}
          {can('entry', project) && (
            <Link to={PROJECT_PAGES.add.path(project)} className={styles.addBtn}>
              <PlusIcon size={17} /> Add Customer
            </Link>
          )}
        </div>
      </header>

      {!empty && (
        <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">S.No</th>
              <th scope="col">Name</th>
              <th scope="col">Company Name</th>
              <th scope="col">Contact No.</th>
              <th scope="col">License Type</th>
              <th scope="col">Serial No.</th>
              <th scope="col">Expiry Date</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((customer, index) => (
              <tr
                key={customer.id}
                className={styles.row}
                tabIndex={canView ? 0 : undefined}
                onClick={() => setViewingId(customer.id)}
                onKeyDown={(e) => {
                  if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    setViewingId(customer.id);
                  }
                }}
                aria-label={`View details of ${customer.name}`}
              >
                <td data-label="S.No">{(currentPage - 1) * pageSize + index + 1}</td>
                <td data-label="Name" className={styles.name}>{customer.name}</td>
                <td data-label="Company Name">{customer.company_name}</td>
                <td data-label="Contact No.">{customer.contact_number}</td>
                <td data-label="License Type">{licenseTypeCell(customer)}</td>
                <td data-label="Serial No.">{customer.serial_number || '—'}</td>
                <td data-label="Expiry Date">{expiryText(customer)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {canView && visible.length > 0 && <div className={styles.tools}>
        <label>Rows <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}>
          {[10, 25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
        </select></label>
        <button type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Previous</button>
        <span>Page {currentPage} of {Math.max(1, Math.ceil(visible.length / pageSize))}</span>
        <button type="button" disabled={currentPage * pageSize >= visible.length} onClick={() => setPage(currentPage + 1)}>Next</button>
      </div>}
      {empty && <p className={`${styles.empty} ${error && canView ? styles.emptyError : ''}`}>{empty}</p>}

      {canView && viewing && <CustomerDetailsView project={project} customer={viewing} onEdit={onEdit} onChanged={onChanged} onDeleted={onDeleted} onNotify={onNotify} onClose={() => setViewingId(null)} />}
    </section>
  );
}
