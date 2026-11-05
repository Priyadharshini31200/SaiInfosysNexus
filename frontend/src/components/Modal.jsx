import { useEffect, useRef } from 'react';
import { CloseIcon } from './Icons';
import styles from './Modal.module.css';

const open = []; // open dialogs, innermost last: Esc closes only the innermost

/** Small centred dialog: backdrop click and Esc close it, focus moves in and is given back on close. */
export default function Modal({ title, onClose, children }) {
  const ref = useRef(null);

  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.focus();
    const token = {};
    open.push(token);
    const onKey = (e) => e.key === 'Escape' && open[open.length - 1] === token && onClose();
    document.addEventListener('keydown', onKey);
    return () => {
      open.splice(open.indexOf(token), 1);
      document.removeEventListener('keydown', onKey);
      previous?.isConnected && previous.focus?.();
    };
  }, [onClose]);

  return (
    <div className={styles.backdrop} data-modal onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <header className={styles.head}>
          <h2 className={styles.title}>{title}</h2>
          <button type="button" className={styles.close} onClick={onClose} aria-label="Close">
            <CloseIcon size={18} />
          </button>
        </header>
        <div className={styles.body}>{children}</div>
      </div>
    </div>
  );
}
