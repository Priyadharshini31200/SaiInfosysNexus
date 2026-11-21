import { Fragment, useCallback, useEffect, useState } from 'react';
import { dashboardApi } from '../../api/dashboard';
import { formatIst, istInputToUtc, utcToIstInput } from '../../utils/cardVisibility';
import styles from './Config.module.css';

export default function VisibilityTab({ active }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [schedule, setSchedule] = useState(null);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const load = useCallback(() => dashboardApi.cardVisibilityProjects().then((d) => setRows(d.projects), (e) => setError(e.message)), []);
  useEffect(() => { if (active) load(); }, [active, load]);

  const save = async (p, mode, visibleFrom) => {
    setBusy(p.code); setError('');
    try {
      const saved = await dashboardApi.saveCardVisibility(p.code, { mode, visible_from_utc: visibleFrom, version: p.version });
      setRows((list) => list.map((r) => r.code === p.code ? { ...r, ...saved } : r));
      setSchedule((current) => current?.code === p.code ? null : current);
    } catch (e) { setError(e.message); load(); }
    finally { setBusy(''); }
  };
  const openSchedule = (p) => {
    const current = utcToIstInput(p.visible_from_utc);
    setSchedule({ code: p.code, value: current });
  };
  const apply = (p) => {
    const iso = istInputToUtc(schedule.value);
    if (!iso || new Date(iso) <= new Date()) { setError('Choose a future date and time (IST).'); return; }
    save(p, 'scheduled', iso);
  };
  const filter = search.trim().toLowerCase();
  const visibleRows = rows?.filter((p) => (!category || p.category === category) && `${p.name} ${p.code}`.toLowerCase().includes(filter));
  return <>
    <div className={styles.visibilityToolbar}>
      <input type="search" placeholder="Search projects" aria-label="Search visibility projects" value={search} onChange={(e) => setSearch(e.target.value)} />
      <select aria-label="Project category" value={category} onChange={(e) => setCategory(e.target.value)}>
        <option value="">All categories</option><option>Tally</option><option>Non-Tally</option>
      </select>
    </div>
    {error && <p role="alert" className={styles.err}>{error}</p>}
    {!rows && !error && <p role="status" className={styles.muted}>Loading…</p>}
    {rows && <div className={styles.visibilityTable}>
      <table aria-label="Dashboard Visibility projects">
        <thead><tr><th>Project</th><th>Category</th><th>Visibility Status</th><th>Actions</th></tr></thead>
        <tbody>
          {visibleRows.map((p) => <Fragment key={p.code}>
            <tr><td><b>{p.name}</b><small>{p.code}</small></td><td>{p.category || '—'}</td>
              <td><span className={styles.visibilityStatus}>{p.mode === 'scheduled' ? 'Scheduled' : p.effective ? 'Visible' : 'Hidden'}</span>
                {p.mode === 'scheduled' && <small>{formatIst(p.visible_from_utc)} · {p.effective ? 'Visible' : 'Hidden'}</small>}</td>
              <td><span className={styles.seg} role="group" aria-label={`${p.name} visibility`}>
                <button type="button" className={`${styles.segBtn} ${p.mode === 'show' ? styles.segOn : ''}`} disabled={!!busy} onClick={() => save(p, 'show', null)}>Show Now</button>
                <button type="button" className={`${styles.segBtn} ${p.mode === 'hidden' ? styles.segOn : ''}`} disabled={!!busy} onClick={() => save(p, 'hidden', null)}>Hide</button>
                <button type="button" className={`${styles.segBtn} ${p.mode === 'scheduled' ? styles.segOn : ''}`} aria-expanded={schedule?.code === p.code} disabled={!!busy} onClick={() => openSchedule(p)}>Schedule</button>
              </span></td>
            </tr>
            {schedule?.code === p.code && <tr><td colSpan={4}>
              <div className={styles.vSched}>
                <label>Date and time (IST)<input type="datetime-local" value={schedule.value} onChange={(e) => setSchedule({ code: p.code, value: e.target.value })} /></label>
                <button type="button" className={styles.btn} disabled={!!busy} onClick={() => apply(p)}>Apply Schedule</button>
                <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={() => setSchedule(null)}>Cancel</button>
              </div>
            </td></tr>}
          </Fragment>)}
          {!visibleRows.length && <tr><td colSpan={4} className={styles.empty}>No projects match these filters.</td></tr>}
        </tbody>
      </table>
    </div>}
  </>;
}
