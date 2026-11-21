import { useEffect, useRef, useState } from 'react';
import { dashboardApi } from '../api/dashboard';
import { formatIst, istInputToUtc, notifyVisibilityChanged, utcToIstInput } from '../utils/cardVisibility';
import styles from './CardVisibilityPanel.module.css';

const OPTIONS = [['show', 'Show Now'], ['hidden', 'Hide'], ['scheduled', 'Schedule']];
const MESSAGES = { show: 'Card is now visible.', hidden: 'Card hidden from the dashboard.', scheduled: 'Scheduled. Card stays hidden until then.' };

/** Back face of a project card. Show Now / Hide apply instantly; Schedule asks for IST date + time, then Apply Schedule.
 *  Everything goes through the one shared visibility API (version-checked, so a stale change returns 409). */
export default function CardVisibilityPanel({ code, setting, onSaved, onCancel, title = 'Card Visibility' }) {
  const existing = utcToIstInput(setting?.visible_from_utc);
  const [scheduling, setScheduling] = useState(setting?.mode === 'scheduled');
  const [date, setDate] = useState(existing.slice(0, 10));
  const [time, setTime] = useState(existing.slice(11, 16));
  const existingEnd = utcToIstInput(setting?.visible_until_utc);
  const [endDate, setEndDate] = useState(existingEnd.slice(0, 10));
  const [endTime, setEndTime] = useState(existingEnd.slice(11, 16));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');
  const version = useRef(setting?.version ?? 0);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const save = async (mode, visibleFrom, visibleUntil = null) => {
    setBusy(true);
    setError('');
    try {
      const saved = await dashboardApi.saveCardVisibility(code, { mode, visible_from_utc: visibleFrom, visible_until_utc: visibleUntil, version: version.current });
      version.current = saved.version;
      notifyVisibilityChanged(code);
      setDone(MESSAGES[mode]);
      timer.current = setTimeout(() => onSaved(saved), 900); // brief confirmation, then the card closes / disappears
    } catch (e) {
      setError(e.message || 'Could not save. Try again.');
      setBusy(false);
    }
  };

  const apply = () => {
    const iso = date && time ? istInputToUtc(`${date}T${time}`) : null;
    if (!iso || new Date(iso) <= new Date()) {
      setError('Choose a future date and time (IST).');
      return;
    }
    let end = null;
    if (endDate || endTime) {
      end = endDate && endTime ? istInputToUtc(`${endDate}T${endTime}`) : null;
      if (!end || new Date(end) <= new Date(iso)) { setError('The end date and time must be after the start.'); return; }
    }
    save('scheduled', iso, end);
  };

  const today = utcToIstInput(new Date().toISOString()).slice(0, 10);
  const pick = (value) => {
    if (busy || done) return;
    if (value === 'scheduled') {
      setScheduling(true);
      setError('');
    } else {
      setScheduling(false);
      save(value, null);
    }
  };

  return (
    <div className={styles.panel} role="group" aria-label={title}>
      <div className={styles.head}>
        <span className={styles.title}>{title}</span>
        <button type="button" className={styles.close} onClick={onCancel} aria-label="Back without changes" disabled={busy || !!done}>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M10 3L5 8l5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </div>
      <div className={styles.segments} role="group" aria-label="Visibility">
        {OPTIONS.map(([value, label]) => {
          const active = value === 'scheduled' ? scheduling : !scheduling && setting?.mode === value;
          return (
            <button key={value} type="button" className={`${styles.segment} ${active ? styles.active : ''}`} aria-pressed={active} disabled={busy || !!done} onClick={() => pick(value)}>{label}</button>
          );
        })}
      </div>
      {scheduling && !done && (
        <div className={styles.schedule}>
          <div className={styles.fields}>
            <label>Start Date<input type="date" value={date} min={today} onChange={(e) => setDate(e.target.value)} disabled={busy} /></label>
            <label>Start Time<input type="time" value={time} onChange={(e) => setTime(e.target.value)} disabled={busy} /></label>
            <label>End Date (optional)<input type="date" value={endDate} min={date || today} onChange={(e) => setEndDate(e.target.value)} disabled={busy} /></label>
            <label>End Time<input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} disabled={busy} /></label>
          </div>
          <div className={styles.row}>
            <span className={styles.tz}>Asia/Kolkata (IST)</span>
            <button type="button" className={styles.apply} onClick={apply} disabled={busy}>{busy ? 'Saving…' : 'Apply Schedule'}</button>
          </div>
          {setting?.mode === 'scheduled' && setting.visible_from_utc && <p className={styles.hint}>Current: {formatIst(setting.visible_from_utc)}{setting.visible_until_utc ? ` → ${formatIst(setting.visible_until_utc)}` : ''}</p>}
        </div>
      )}
      <p className={styles.status} role="status">{done}</p>
      {error && <p role="alert" className={styles.error}>{error}</p>}
    </div>
  );
}
