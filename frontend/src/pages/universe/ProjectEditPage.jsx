import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import CustomerPicker from '../CustomerPicker';
import styles from '../ProjectDirectory.module.css';

export default function ProjectEditPage() {
  const { code } = useParams();
  const { state } = useLocation();
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false); // Edit Customer -> Select Customer popup (this project only)
  useEffect(() => {
    let active = true;
    Promise.all([integrationsApi.universeProject(code), integrationsApi.project(code)]).then(([scope, record]) => {
      if (!active) return;
      if (scope.can.edit) setProject(record.project);
      else setError('You do not have permission to edit this project.');
    }, (e) => active && setError(e.message));
    return () => { active = false; };
  }, [code]);
  const manual = project?.manual;
  const back = state?.from || `/directory/${manual ? 'non-tally' : 'tally'}`;
  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await integrationsApi.updateProject(code, { name: project.name, ...(manual && !project.developer_email ? {} : { developer_email: project.developer_email }) }); // code, category, customers, licences, credentials and API mappings are never touched here
      navigate(back, { replace: true }); // the directory reloads on arrival
    } catch (e) { setError(Object.values(e.errors || {}).flat().join(' ') || e.message); }
    finally { setBusy(false); }
  }
  return <section className={styles.page} aria-label="Edit project">
    {error && <p role="alert">{error}</p>}
    {!project && !error && <p role="status">Loading project…</p>}
    {project && <form className={styles.editForm} onSubmit={save}>
      <label>Project Name<input required maxLength={120} value={project.name} onChange={(e) => setProject({ ...project, name: e.target.value })} /></label>
      <label>Project Code<input readOnly value={project.code} /></label>
      <label>Registration Mode<input readOnly value={manual ? 'Manual' : 'API'} /></label>
      <label>Status<input readOnly value={{ ACTIVE: 'Active', DISABLED: 'Disabled', ARCHIVED: 'Archived', SETUP_PENDING: 'Setup Pending' }[project.status] || project.status} /></label>
      <label>Project Category<input readOnly value={manual ? 'Non-Tally SL Project (manual customers)' : project.tally_serial_required ? 'Tally SL Project (Tally verification required)' : 'Tally SL Project (API, no Tally verification)'} /></label>
      <label>Developer Email<input required={!manual} type="email" maxLength={150} value={project.developer_email} onChange={(e) => setProject({ ...project, developer_email: e.target.value })} /></label>
      <div className={styles.editActions}><button className={styles.manage} type="button" onClick={() => setPicking(true)}>Edit Customer</button>{!manual && <button className={styles.manage} type="button" onClick={() => navigate(`/universe/api?project=${encodeURIComponent(code)}`)}>API Settings</button>}<button className={styles.manage} type="button" onClick={() => navigate(back)}>Cancel</button><button className={styles.add} disabled={busy}>{busy ? 'Saving…' : 'Save Changes'}</button></div>
    </form>}
    {picking && <CustomerPicker kind="universe" code={code} mode="edit" onClose={() => setPicking(false)} />}
  </section>;
}
