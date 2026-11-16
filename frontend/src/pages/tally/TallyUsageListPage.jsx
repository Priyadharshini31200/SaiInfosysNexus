import { memo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { tallyApi } from '../../api/tallysl';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDateTime } from '../../utils/date';
import { ListPanel, REPORT_MAX_ROWS, SearchBox, useAllRows, useDebounced } from './TallyShared';
import styles from './Tally.module.css';

const LABELS = { speed: 'Speed User', normal: 'Normal User', new: 'New User', one_time: 'One-Time User' };
const COLS = ['S.No', 'Customer Name', 'Tally Serial No.', 'Product', 'Last Opened', 'Opening Count', 'Active Usage Days'];

const UsageRow = memo(function UsageRow({ r, n, onOpen }) {
  return (
    <tr className={styles.row} tabIndex={0} onClick={() => onOpen(r)} onKeyDown={(e) => e.key === 'Enter' && onOpen(r)}>
      <td>{n}</td>
      <td className={styles.name}>{r.name}</td>
      <td>{r.tally_serial}</td>
      <td>{r.product_name}</td>
      <td>{formatDateTime(r.last_opened)}</td>
      <td>{r.opens}</td>
      <td>{r.active_days}</td>
    </tr>
  );
});

export default function TallyUsageListPage() {
  const { product, category } = useParams();
  const navigate = useNavigate();
  const { can, loaded } = useTallyAccess();
  const [search, setSearch] = useState('');
  const term = useDebounced(search);
  const allowed = can('view', product);
  const { rows, loading, error, total, truncated } = useAllRows((page, page_size) => tallyApi.usageList(category, { product, search: term, page, page_size }), [product, category, term], allowed);
  const open = (r) => navigate(`/tally/${r.product}/customers/${r.id}`);
  if (loaded && !allowed) return <p>You do not have permission to view this product.</p>;
  const note = truncated
    ? `${loading ? 'Loading… ' : ''}Limited to the first ${REPORT_MAX_ROWS.toLocaleString()} of ${total.toLocaleString()} customers. Use Search to narrow the list.`
    : (loading && rows.length > 0 ? `Loading… ${rows.length.toLocaleString()} of ${total.toLocaleString()} customers` : null);
  return (
    <ListPanel
      label={LABELS[category] || 'Users'}
      columns={COLS}
      loading={loading}
      error={error}
      empty="No customers in this category."
      note={note}
      toolbar={(
        <>
          <Link to={`/tally/${product}/dashboard`} className={styles.link}>← Dashboard</Link>
          <span className={styles.title}>{LABELS[category] || category}</span>
          <SearchBox value={search} onChange={setSearch} placeholder="Search name or Tally serial" />
          <span className={styles.count}>Total: {total}</span>
        </>
      )}
    >
      {rows.map((r, i) => <UsageRow key={r.id} r={r} n={i + 1} onOpen={open} />)}
    </ListPanel>
  );
}
