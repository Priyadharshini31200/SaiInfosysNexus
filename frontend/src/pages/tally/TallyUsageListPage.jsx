import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { tallyApi } from '../../api/tallysl';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDateTime } from '../../utils/date';
import { Pager, SearchBox, useDebounced, usePaged } from './TallyShared';
import styles from './Tally.module.css';

const LABELS = { speed: 'Speed User', normal: 'Normal User', new: 'New User', one_time: 'One-Time User' };
const COLS = ['S.No', 'Customer Name', 'Tally Serial No.', 'Product', 'Last Opened', 'Opening Count', 'Active Usage Days'];

export default function TallyUsageListPage() {
  const { product, category } = useParams();
  const navigate = useNavigate();
  const { can, loaded } = useTallyAccess();
  const [search, setSearch] = useState('');
  const term = useDebounced(search);
  const allowed = can('view', product);
  const paged = usePaged((page, page_size) => tallyApi.usageList(category, { product, search: term, page, page_size }), [product, category, term], allowed);
  if (loaded && !allowed) return <p>You do not have permission to view this product.</p>;
  return (
    <section className={styles.panel} aria-label={LABELS[category] || 'Users'}>
      <header className={styles.toolbar}>
        <Link to={`/tally/${product}/customers`} className={styles.link}>← Customers</Link>
        <span className={styles.title}>{LABELS[category] || category}</span>
        <SearchBox value={search} onChange={setSearch} placeholder="Search name or Tally serial" />
        <span className={styles.count}>Total: {paged.total}</span>
      </header>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead><tr>{COLS.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {paged.results.map((r, i) => (
              <tr key={r.id} className={styles.row} tabIndex={0} onClick={() => navigate(`/tally/${r.product}/customers/${r.id}`)}>
                <td>{(paged.page - 1) * paged.pageSize + i + 1}</td>
                <td className={styles.name}>{r.name}</td>
                <td>{r.tally_serial}</td>
                <td>{r.product_name}</td>
                <td>{formatDateTime(r.last_opened)}</td>
                <td>{r.opens}</td>
                <td>{r.active_days}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!paged.loading && !paged.error && paged.results.length === 0 && <p className={styles.empty}>No customers in this category.</p>}
        {paged.error && <p className={`${styles.empty} ${styles.emptyError}`}>{paged.error}</p>}
      </div>
      <Pager {...paged} showTotal={false} />
    </section>
  );
}
