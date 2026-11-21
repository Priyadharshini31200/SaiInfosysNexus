import { useEffect, useState } from 'react';

const EVENT = 'nexus:toast';
/** Small top-right notification (copy confirmations, saves). Call notify('Project code copied.') from anywhere; render <TopToast /> once on the page. */
export const notify = (text, type = 'success') => window.dispatchEvent(new CustomEvent(EVENT, { detail: { text, type, id: Date.now() + Math.random() } }));

export default function TopToast() {
  const [t, setT] = useState(null);
  useEffect(() => {
    let timer;
    const on = (e) => { setT(e.detail); clearTimeout(timer); timer = setTimeout(() => setT(null), 3000); };
    window.addEventListener(EVENT, on);
    return () => { window.removeEventListener(EVENT, on); clearTimeout(timer); };
  }, []);
  if (!t) return null;
  const ok = t.type !== 'error';
  return (
    <div role={ok ? 'status' : 'alert'} style={{ position: 'fixed', top: 70, right: 20, zIndex: 120, display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 8, background: ok ? '#0f5132' : '#842029', color: '#fff', fontSize: 13.5, boxShadow: '0 6px 18px rgba(0,0,0,.2)', maxWidth: 'min(380px, calc(100vw - 32px))' }}>
      <span aria-hidden="true">{ok ? '✓' : '!'}</span>{t.text}
    </div>
  );
}
