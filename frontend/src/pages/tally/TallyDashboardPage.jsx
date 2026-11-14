import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { tallyApi } from '../../api/tallysl';
import { useTallyAccess } from '../../context/TallyAccessContext';
import styles from './Tally.module.css';

/** User performance summary: exactly four categories (Speed / Normal / New / One-Time). Each card opens its filtered customer list. */
export default function TallyDashboardPage() {
  const { product } = useParams();
  const navigate = useNavigate();
  const { can, loaded } = useTallyAccess();
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const allowed = can('view', product);

  useEffect(() => {
    if (!allowed) return undefined;
    let current = true;
    tallyApi
      .dashboard(product)
      .then((data) => current && setState({ loading: false, error: '', data }))
      .catch((err) => current && setState({ loading: false, error: err.message, data: null }));
    return () => {
      current = false;
    };
  }, [product, allowed]);

  if (loaded && !allowed) return <p>You do not have permission to view this product.</p>;
  const { loading, error, data } = state;
  if (loading) return <p className={styles.muted}>Loading…</p>;
  if (error) return <p role="alert" className={styles.emptyError}>{error}</p>;
  const reg = data.registrations;
  return (
    <>
      <div className={styles.cards} role="list" aria-label="Customers by usage category">
        {data.categories.map((c) => (
          <button key={c.key} type="button" role="listitem" className={styles.card} onClick={() => navigate(`/tally/${product}/usage/${c.key}`)}>
            <span className={styles.cardLabel}>{c.label}</span>
            <span className={styles.cardCount}>{c.count}</span>
            <span className={styles.cardLink}>View customers</span>
          </button>
        ))}
      </div>
      <section className={styles.summary} aria-label="Registrations">
        <div className={styles.metrics}>
          <span className={styles.summaryTitle}>Registrations</span>
          <span className={styles.metric}>Total Customers <strong>{reg.total}</strong></span>
          <span className={styles.metric}>Last 7 Days <strong>{reg.last_7_days}</strong></span>
          <span className={styles.metric}>Pending Activation <strong>{reg.pending_license}</strong></span>
        </div>
      </section>
    </>
  );
}
