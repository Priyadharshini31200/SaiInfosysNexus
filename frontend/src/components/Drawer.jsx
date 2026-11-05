import { useEffect, useRef } from 'react';
import { CloseIcon } from './Icons';
import styles from './Drawer.module.css';

/**
 * Right-side slide-in panel shared by My Profile and Configuration.
 * Children are mounted only while open, so each opening starts from a clean state.
 * `actions` renders extra header buttons left of the close button. `flush` removes the body padding and scrolling, for content
 * that scrolls itself above a fixed footer.
 */
export default function Drawer({ open, onClose, title, titleId, closeLabel = 'Close', actions = null, flush = false, children }) {
  const closeRef = useRef(null);
  const returnFocusRef = useRef(null);

  // Move focus in on open; give it back on close. Esc closes.
  useEffect(() => {
    if (!open) return undefined;
    returnFocusRef.current = document.activeElement;
    closeRef.current?.focus();
    // A dialog opened from inside the drawer (e.g. a confirmation) takes Esc first.
    const onKey = (e) => e.key === 'Escape' && !document.querySelector('[data-modal]') && onClose();
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      // The menu item that opened the drawer is gone by now, so fall back to the account menu button.
      const prev = returnFocusRef.current;
      const target = prev && prev !== document.body && prev.isConnected ? prev : document.querySelector('[aria-haspopup="menu"]');
      target?.focus?.();
    };
  }, [open, onClose]);

  return (
    <>
      <div className={`${styles.backdrop} ${open ? styles.backdropVisible : ''}`} onClick={onClose} aria-hidden="true" />
      <aside
        className={`${styles.drawer} ${open ? styles.open : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-hidden={!open}
        inert={!open}
      >
        <header className={styles.head}>
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
          <div className={styles.headActions}>
            {actions}
            <button ref={closeRef} type="button" className={styles.iconBtn} onClick={onClose} aria-label={closeLabel}>
              <CloseIcon size={20} />
            </button>
          </div>
        </header>
        {open && <div className={`${styles.body} ${flush ? styles.flush : ''}`}>{children}</div>}
      </aside>
    </>
  );
}
