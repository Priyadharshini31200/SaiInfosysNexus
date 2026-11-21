import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import styles from '../ProjectDirectory.module.css';

export default function ProjectActions({ project, grants, onChange, state, label }) {
  const id = useId();
  const [error, setError] = useState('');
  const archived = ['ARCHIVED', 'Archived'].includes(project.status);
  if (!grants || !Object.values(grants).some(Boolean)) return null;
  const lifecycle = async () => {
    const text = archived
      ? `Restore ${project.name} to Setup Pending for revalidation?`
      : `Archive ${project.name}? Synchronisation will stop. Customers, licences and history will be retained.`;
    if (!window.confirm(text)) return;
    setError('');
    try {
      await integrationsApi.updateProject(project.code, { lifecycle: archived ? 'restore' : 'archive' });
      document.getElementById(id)?.hidePopover();
      onChange?.();
    } catch (e) { setError(e.message); }
  };
  return <>
    <button type="button" className={label ? styles.manage : styles.act} aria-label={label || `${project.name} actions`}
      popoverTarget={id} onClick={(e) => {
        const bounds = e.currentTarget.getBoundingClientRect();
        const menu = document.getElementById(id);
        menu.style.top = `${Math.max(8, Math.min(bounds.bottom + 4, window.innerHeight - 180))}px`;
        menu.style.left = `${Math.min(Math.max(8, bounds.right - 205), window.innerWidth - 213)}px`;
      }}>{label || '⋯'}</button>
    <div id={id} popover="auto" className={styles.actionMenu}>
      {grants.edit && <Link to={`/universe/projects/${project.code}/edit`} state={state}>Edit Project</Link>}
      {grants.configure && <Link to={`/universe/api?project=${encodeURIComponent(project.code)}`}>Configure API</Link>}
      {grants.archive && <button type="button" onClick={lifecycle}>{archived ? 'Restore Project' : 'Archive Project'}</button>}
      {error && <p role="alert">{error}</p>}
    </div>
  </>;
}
