import { memo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { tallyApi } from '../../api/tallysl';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDateTime } from '../../utils/date';
import { ListPanel, useAllRows } from './TallyShared';
import styles from './Tally.module.css';

const COLS = ['S.No', 'Customer', 'Tally Serial', 'Previous Device', 'New Device', 'Transfer Date', 'Status'];

const Row = memo(function Row({ r, n, onOpen }) {
  return (
    <tr className={styles.row} tabIndex={0} onClick={() => onOpen(r)} onKeyDown={(e) => e.key === 'Enter' && onOpen(r)}>
      <td>{n}</td><td className={styles.name}>{r.customer_name}</td><td>{r.tally_serial}</td><td>{r.old_device || '—'}</td>
      <td>{r.new_device}</td><td>{formatDateTime(r.occurred_at)}</td><td>Completed</td>
    </tr>
  );
});

/** Read-only list of completed device transfers (last 7 days). A row opens that customer with the transfer highlighted. */
export default function TallyTransfersPage() {
  const { product } = useParams();
  const navigate = useNavigate();
  const { can, loaded } = useTallyAccess();
  const allowed = can('view', product);
  const { rows, loading, error, total } = useAllRows((page, page_size) => tallyApi.transfers(product, { recent: 1, page, page_size }), [product], allowed);
  if (loaded && !allowed) return <p>You do not have permission to view this product.</p>;
  return (
    <ListPanel label="Recent Device Transfers" columns={COLS} loading={loading} error={error} empty="No device transfers in the last 7 days."
      toolbar={<span className={styles.count}>Recent Device Transfers (last 7 days): {total}</span>}>
      {rows.map((r, i) => <Row key={r.id} r={r} n={i + 1} onOpen={(x) => navigate(`/tally/${product}/customers/${x.customer_id}?transfer=${x.id}`)} />)}
    </ListPanel>
  );
}
