import { useEffect } from 'react';
import styles from './Toast.module.css';

export const TOAST_DURATION = 1000; // ms; every temporary notification disappears after this

/** Brief status message. `toast` is { id, type: 'success' | 'error', text } or null. A newer toast replaces the one showing and restarts the timer. */
export default function Toast({ toast, onDone }) {
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(onDone, TOAST_DURATION);
    return () => clearTimeout(timer);
  }, [toast, onDone]);

  if (!toast) return null;
  return (
    <div className={`${styles.toast} ${toast.type === 'error' ? styles.error : styles.success}`} role={toast.type === 'error' ? 'alert' : 'status'}>
      {toast.text}
    </div>
  );
}
