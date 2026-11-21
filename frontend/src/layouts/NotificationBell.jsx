import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { tallyApi } from '../api/tallysl';
import { integrationsApi } from '../api/integrations';
import { useAuth } from '../context/AuthContext';
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
  const { user } = useAuth();
  const [feed, setFeed] = useState({ unread_count: 0, results: [] });
  const [uni, setUni] = useState({ unread_count: 0, results: [] });
  const [toast, setToast] = useState('');
  const lastUni = useRef(null);
  const wrapRef = useRef(null);

  const load = useCallback(() => {
    tallyApi.notifications().then(setFeed).catch(() => {});
    if (user?.role === 'SUPERADMIN' || user?.role === 'ADMIN') {
      integrationsApi.notifications().then((d) => {
        setUni(d);
        const newest = d.results.find((n) => !n.read);
        if (lastUni.current !== null && newest && newest.id > lastUni.current) setToast(newest.title); // once per new item, never a modal
        lastUni.current = Math.max(lastUni.current ?? 0, ...d.results.map((n) => n.id), 0);
      }).catch(() => {});
    }
  }, [user?.role]);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(''), 7000); return () => clearTimeout(t); }, [toast]);
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

  async function openUni(n) {
    setOpen(false);
    navigate(n.kind === 'SYNC_FAILURE' ? `/universe/api?project=${n.project_code}&tab=logs` : n.kind === 'EXPIRY' ? `/universe/projects/${n.project_code}/customers?open=customer:${n.customer_id}` : `/universe/projects/${n.project_code}/customers?open=registration:${n.registration_id}`);
    if (!n.read) {
      setUni((f) => ({ unread_count: Math.max(f.unread_count - 1, 0), results: f.results.map((x) => (x.id === n.id ? { ...x, read: true } : x)) }));
      integrationsApi.readNotification(n.id).catch(load);
    }
  }

  /** X: remove the row at once (and from the bell count if unread); the server stores the dismissal for this user only. */
  function dismiss(e, kind, n) {
    e.stopPropagation();
    const drop = (f) => ({ unread_count: n.read ? f.unread_count : Math.max(f.unread_count - 1, 0), results: f.results.filter((x) => x.id !== n.id) });
    if (kind === 'uni') { setUni(drop); integrationsApi.dismissNotification(n.id).catch(load); }
    else { setFeed(drop); tallyApi.dismissNotification(n.id).catch(load); }
  }
  const closeBtn = (kind, n) => (
    <button type="button" className={styles.dismiss} title="Dismiss Notification" aria-label={`Dismiss notification: ${n.title}`} onClick={(e) => dismiss(e, kind, n)}>
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
    </button>
  );

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
      {toast && <div role="status" aria-live="polite" style={{ position: 'fixed', top: 70, right: 16, zIndex: 120, padding: '10px 14px', background: '#12305a', color: '#fff', borderRadius: 8, boxShadow: '0 6px 18px rgba(0,0,0,.25)', cursor: 'pointer' }} onClick={() => { setToast(''); navigate('/dashboard'); }}>{toast}</div>}
      <button type="button" className={styles.bell} onClick={() => { setOpen((o) => !o); load(); }} aria-haspopup="true" aria-expanded={open}
        aria-label={`Notifications${feed.unread_count + uni.unread_count ? `, ${feed.unread_count + uni.unread_count} unread` : ''}`}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {feed.unread_count + uni.unread_count > 0 && <span className={styles.badge}>{feed.unread_count + uni.unread_count > 99 ? '99+' : feed.unread_count + uni.unread_count}</span>}
      </button>
      {open && (
        <div className={styles.panel} role="menu" aria-label="Notifications">
          <p className={styles.head}>Notifications</p>
          {uni.results.slice(0, 8).map((n) => (
            <div key={`u${n.id}`} className={styles.row}>
              <button type="button" role="menuitem" className={styles.item} onClick={() => openUni(n)} style={n.read ? undefined : { fontWeight: 700 }}>
                <span>{n.title}</span><small>{n.customer ? `${n.customer} · ` : ''}{formatDateTime(n.created_at)}</small>
              </button>
              {closeBtn('uni', n)}
            </div>
          ))}
          {feed.results.length === 0 && uni.results.length === 0 && <p className={styles.empty}>No notifications.</p>}
          <ul className={styles.list}>
            {feed.results.map((n) => (
              <li key={n.id} className={styles.row}>
                <button type="button" role="menuitem" className={`${styles.item} ${n.read ? '' : styles.unread}`} onClick={() => openItem(n)}>
                  <span className={styles.title}>{n.title}</span>
                  <span className={styles.msg}>{n.message}</span>
                  <span className={styles.meta}>{n.customer_name} · {n.tally_serial} · Completed · {formatDateTime(n.occurred_at)}</span>
                </button>
                {closeBtn('tally', n)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
