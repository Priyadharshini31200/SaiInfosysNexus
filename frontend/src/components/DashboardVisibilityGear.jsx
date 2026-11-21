import { useEffect, useRef, useState } from 'react';
import { dashboardApi } from '../api/dashboard';
import CardVisibilityPanel from './CardVisibilityPanel';
import { onVisibilityChanged } from '../utils/cardVisibility';
import styles from './DashboardVisibilityGear.module.css';

const GearIcon = () => (
  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
  </svg>
);

/** Project dashboard header: gear -> Dashboard Visibility Settings (Show Now / Hide / Schedule). Uses the SAME record and API as
 *  Configuration -> Dashboard Visibility and the Main Dashboard card flip; shown only to users the server says may change it. */
export default function DashboardVisibilityGear({ code }) {
  const [setting, setSetting] = useState(null);
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    let live = true;
    const load = () => dashboardApi.cardVisibilityOne(code).then((d) => live && setSetting(d), () => live && setSetting(null));
    load();
    const off = onVisibilityChanged(load);
    return () => { live = false; off(); };
  }, [code]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (e.key === 'Escape' || (e.type === 'mousedown' && box.current && !box.current.contains(e.target))) setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  if (!setting?.can_edit) return null;
  const label = setting.mode === 'scheduled' ? 'Scheduled' : setting.effective ? 'Visible' : 'Hidden';
  return (
    <span className={styles.wrap} ref={box}>
      <button type="button" className={styles.gear} title="Dashboard Visibility Settings" aria-label={`Dashboard Visibility Settings (currently ${label})`} aria-expanded={open} onClick={() => setOpen((o) => !o)}><GearIcon /></button>
      {open && (
        <div className={styles.pop} role="dialog" aria-label="Dashboard Visibility Settings">
          <CardVisibilityPanel key={setting.version} code={code} setting={setting} title="Dashboard Visibility Settings" onCancel={() => setOpen(false)} onSaved={(saved) => { setSetting((s) => ({ ...s, ...saved })); setOpen(false); }} />
        </div>
      )}
    </span>
  );
}
