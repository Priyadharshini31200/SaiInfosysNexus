import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { tallyApi } from '../api/tallysl';
import { formatDateTime } from '../utils/date';
import styles from './NotificationBell.module.css';

const POLL_MS = 30000;

/**
 * Header bell: completed GSTR2Tally device transfers (notification only - there is nothing to approve here).
 * Opening one marks it read and goes to that customer's details with the transfer highlighted.
 * Barcode AutoPrint sign-up requests keep their own ApprovalGate; this bell does not touch them.
 */
export default function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [feed, setFeed] = useState({ unread_count: 0, results: [] });
  const wrapRef = useRef(null);

  const load = useCallback(() => tallyApi.notifications().then(setFeed).catch(() => {}), []);
  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => wrapRef.current && !wrapRef.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  async function openItem(n) {
    setOpen(false);
    navigate(`/tally/${n.product}/customers/${n.customer_id}?transfer=${n.id}`);
    if (!n.read) {
      setFeed((f) => ({ unread_count: Math.max(f.unread_count - 1, 0), results: f.results.map((x) => (x.id === n.id ? { ...x, read: true } : x)) }));
      tallyApi.markNotificationRead(n.id).catch(load);
    }
  }

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <button type="button" className={styles.bell} onClick={() => { setOpen((o) => !o); load(); }} aria-haspopup="true" aria-expanded={open}
        aria-label={`Notifications${feed.unread_count ? `, ${feed.unread_count} unread` : ''}`}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {feed.unread_count > 0 && <span className={styles.badge}>{feed.unread_count > 99 ? '99+' : feed.unread_count}</span>}
      </button>
      {open && (
        <div className={styles.panel} role="menu" aria-label="Notifications">
          <p className={styles.head}>Notifications</p>
          {feed.results.length === 0 && <p className={styles.empty}>No notifications.</p>}
          <ul className={styles.list}>
            {feed.results.map((n) => (
              <li key={n.id}>
                <button type="button" role="menuitem" className={`${styles.item} ${n.read ? '' : styles.unread}`} onClick={() => openItem(n)}>
                  <span className={styles.title}>{n.title}</span>
                  <span className={styles.msg}>{n.message}</span>
                  <span className={styles.meta}>{n.customer_name} · {n.tally_serial} · Completed · {formatDateTime(n.occurred_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
