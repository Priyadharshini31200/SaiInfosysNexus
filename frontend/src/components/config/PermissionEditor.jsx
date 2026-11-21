import { useEffect, useState } from 'react';
import { countSelected, has, setAction, setAll, slots, toggleAssigned } from './permissionModel';
import styles from './Config.module.css';

/** Two-panel editor: assigned projects on the left, the selected project's real menus/actions on the right.
 *  Fully controlled (`value`/`onChange`), so every pending change survives switching projects. */
export default function PermissionEditor({ role, catalog, labels, value, onChange, disabled = false }) {
  const [code, setCode] = useState(catalog[0]?.code || '');
  useEffect(() => { if (!catalog.some((p) => p.code === code)) setCode(catalog[0]?.code || ''); }, [catalog, code]);
  const project = catalog.find((p) => p.code === code);
  if (!project) return <p className={styles.muted}>No projects are registered.</p>;
  const on = value.assigned.includes(project.code);
  const { selected, total } = countSelected(value, project);
  const label = (menu, a) => (project.kind === 'tally' ? project.labels?.[a] : labels[a]) || a;

  return (
    <div className={styles.split}>
      <nav className={styles.projects} aria-label="Assigned projects">
        <div className={styles.paneTitle}>Assigned Projects</div>
        {catalog.map((p) => {
          const assigned = value.assigned.includes(p.code);
          const n = role === 'USER' || role === 'DEVELOPER' ? null : countSelected(value, p).selected;
          return (
            <button key={p.code} type="button" className={`${styles.projectRow} ${p.code === code ? styles.projectActive : ''}`} aria-current={p.code === code ? 'true' : undefined} onClick={() => setCode(p.code)}>
              <span className={styles.projectName}>{p.name}</span>
              <span className={assigned ? styles.badgeOn : styles.badgeOff}>{assigned ? (role === 'USER' ? 'Report access' : role === 'DEVELOPER' ? 'Integration access' : `${n} granted`) : 'No access'}</span>
            </button>
          );
        })}
      </nav>

      <section className={styles.detail} aria-label={`${project.name} permissions`}>
        <header className={styles.detailHead}>
          <h3>{project.name}</h3>
          <label className={styles.allow}>
            <input type="checkbox" checked={on} disabled={disabled} onChange={(e) => onChange(toggleAssigned(value, project.code, e.target.checked))} /> Allow Project Access
          </label>
        </header>

        {role === 'DEVELOPER' ? (
          <p className={styles.note}>{on ? 'This developer can configure ONLY this project\'s integration (API endpoints, authentication reference, field mapping, tests, own logs, documentation). No customers, licences, staff or approvals.' : 'Enable access to assign this project\'s integration workspace to the developer.'}</p>
        ) : role === 'USER' ? (
          <p className={styles.note}>{on ? 'This User can open only the Consolidated This Month Expiry Report, filtered to the projects that have access enabled.' : 'Enable access to include this project in the User\'s Consolidated This Month Expiry Report.'}</p>
        ) : !on ? (
          <p className={styles.note}>Project access is off. Nothing in {project.name} is accessible to this account, and any selections below are discarded when you save.</p>
        ) : (
          <>
            <div className={styles.bulk}>
              <span className={styles.muted}>{selected} of {total} permissions selected</span>
              <span>
                <button type="button" className={styles.link} disabled={disabled} onClick={() => onChange(setAll(value, project, true))}>Select all</button>
                <button type="button" className={styles.link} disabled={disabled} onClick={() => onChange(setAll(value, project, false))}>Clear</button>
              </span>
            </div>
            {project.menus.map((menu) => (
              <fieldset key={menu.module} className={styles.feature} disabled={disabled}>
                <legend>{menu.label}</legend>
                <div className={styles.actions}>
                  {menu.actions.map((a) => {
                    const slot = { bucket: project.kind === 'tally' ? 'tally_grants' : 'module_grants', key: project.kind === 'tally' ? project.code : menu.module, action: a };
                    return (
                      <label key={a} className={styles.check}>
                        <input type="checkbox" checked={has(value, slot.bucket, slot.key, a)} onChange={(e) => onChange(setAction(value, slot, e.target.checked))} /> {label(menu, a)}
                      </label>
                    );
                  })}
                </div>
              </fieldset>
            ))}
            <fieldset className={styles.feature} disabled={disabled}>
              <legend>Overall Dashboard</legend>
              <div className={styles.actions}>
                <label className={styles.check}>
                  <input type="checkbox" checked={value.visibility.includes(project.code)} onChange={(e) => onChange({ ...value, visibility: e.target.checked ? [...new Set([...value.visibility, project.code])] : value.visibility.filter((c) => c !== project.code) })} /> Manage Dashboard Visibility
                </label>
              </div>
            </fieldset>
          </>
        )}
      </section>
    </div>
  );
}

export { slots };
