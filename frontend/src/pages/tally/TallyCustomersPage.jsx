import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { tallyApi } from '../../api/tallysl';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDate } from '../../utils/date';
import TallyDashboardPage from './TallyDashboardPage';
import { PLAN_LABELS, Pager, SearchBox, STATE_LABELS, useDebounced, usePaged } from './TallyShared';
import styles from './Tally.module.css';

const COLS = ['S.No', 'Customer', 'Tally Serial / User ID', 'Email', 'License Plan', 'Status', 'Nexus Expiry'];

export default function TallyCustomersPage() {
  const { product } = useParams();
  const navigate = useNavigate();
  const { can, loaded } = useTallyAccess();
  const [search, setSearch] = useState('');
  const [reload, setReload] = useState(0);
  const term = useDebounced(search);
  const allowed = can('view', product);
  const paged = usePaged((page, page_size) => tallyApi.customers(product, { search: term, page, page_size }), [product, term, reload], allowed);

  if (loaded && !allowed) return <p>You do not have permission to view this product.</p>;
  return (
    <div className={styles.page}>
      <TallyDashboardPage />
      <section className={styles.panel} aria-label="Customers">
        <header className={styles.toolbar}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search name, serial, email or phone" label="Search customers" />
          <span className={styles.count}>Total: {paged.total}</span>
          <div className={styles.right}>
            <button type="button" className={styles.refresh} onClick={() => setReload((n) => n + 1)} disabled={paged.loading} aria-label="Refresh customer list">{paged.loading ? 'Refreshing…' : 'Refresh'}</button>
          </div>
        </header>
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead><tr>{COLS.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {paged.results.map((r, i) => (
                <tr key={r.id} className={styles.row} tabIndex={0} onClick={() => navigate(`/tally/${product}/customers/${r.id}`)}
                  onKeyDown={(e) => e.key === 'Enter' && navigate(`/tally/${product}/customers/${r.id}`)}>
                  <td>{(paged.page - 1) * paged.pageSize + i + 1}</td>
                  <td className={styles.name}>{r.name}</td>
                  <td>{r.tally_serial}</td>
                  <td>{r.email}</td>
                  <td>{PLAN_LABELS[r.license_plan] || r.license_plan}</td>
                  <td>{STATE_LABELS[r.state] || r.state}</td>
                  <td>{r.license_type === 'LIFETIME' ? 'Never' : formatDate(r.expires_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {paged.loading && paged.results.length === 0 && <p className={styles.empty}>Loading…</p>}
          {!paged.loading && !paged.error && paged.results.length === 0 && <p className={styles.empty}>No customers yet.</p>}
          {paged.error && <p className={`${styles.empty} ${styles.emptyError}`}>{paged.error}</p>}
        </div>
        <Pager {...paged} showTotal={false} />
      </section>
    </div>
  );
}
