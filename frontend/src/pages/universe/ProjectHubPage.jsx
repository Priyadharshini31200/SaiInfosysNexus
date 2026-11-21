import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import { SearchIcon } from '../../components/Icons';
import styles from './ProjectHub.module.css';

/** UNIVERSE -> Projects: the onboarding entry point. No permanent project table: search / type / status are a quick finder (permission-filtered, server-side)
 *  that opens the project in its Tally / Non-Tally directory (or its API Integration). Clearing the search returns to the clean landing state. */
export default function ProjectHubPage() {
  const navigate = useNavigate();
  const [text, setText] = useState('');
  const [type, setType] = useState('');
  const [status, setStatus] = useState('');
  const [res, setRes] = useState({ results: [], can_add: true, total: 0 });
  const [state, setState] = useState('idle');
  const seq = useRef(0);
  const active = !!(text.trim() || type || status);

  useEffect(() => { integrationsApi.universeHub({ page: 1, page_size: 1 }).then((d) => setRes((r) => ({ ...r, can_add: d.can_add })), () => {}); }, []);
  useEffect(() => {
    if (!active) { setState('idle'); return undefined; }
    const mine = ++seq.current;
    setState('loading');
    const t = setTimeout(() => integrationsApi.universeHub({ search: text.trim(), type, status, page: 1, page_size: 8 }).then(
      (d) => { if (mine === seq.current) { setRes(d); setState('ok'); } }, () => { if (mine === seq.current) setState('error'); }), 250);
    return () => clearTimeout(t);
  }, [text, type, status, active]);

  const open = (p) => navigate(`/directory/${p.type === 'Tally' ? 'tally' : 'non-tally'}?search=${encodeURIComponent(p.code)}`);
  const reset = () => { setText(''); setType(''); setStatus(''); };
  const statusLabel = (s) => (s === 'Pending' ? 'Setup Pending' : s);

  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <label className={styles.search}><SearchIcon size={16} /><input type="search" placeholder="Search project name or code..." value={text} onChange={(e) => setText(e.target.value)} aria-label="Search projects" /></label>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Project type"><option value="">All Types</option><option value="tally">Tally</option><option value="non-tally">Non-Tally</option></select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Integration status"><option value="">All Statuses</option><option value="pending">Setup Pending</option><option value="active">Active</option><option value="suspended">Suspended</option></select>
        {res.can_add && <Link to="/universe/projects/add" className={styles.add}>+ Add Project</Link>}
      </div>
      {active && (
        <div className={styles.results} role="listbox" aria-label="Matching projects">
          {state === 'loading' && <p className={styles.note}>Searching…</p>}
          {state === 'error' && <p className={styles.note} role="alert">Could not search projects.</p>}
          {state === 'ok' && res.results.length === 0 && <p className={styles.note}>No projects match.</p>}
          {state === 'ok' && res.results.map((p) => (
            <div key={p.code} className={styles.row} role="option" tabIndex={0} aria-selected="false" onClick={() => open(p)} onKeyDown={(e) => e.key === 'Enter' && open(p)}>
              <span><b>{p.name}</b><small>{p.code} · {p.type === 'Tally' ? 'Tally SL' : 'Non-Tally SL'} · {statusLabel(p.status)}</small></span>
              {p.can.configure && <Link className={styles.cfg} to={`/universe/api?project=${p.code}`} onClick={(e) => e.stopPropagation()}>Configure API</Link>}
            </div>
          ))}
          <button type="button" className={styles.clear} onClick={reset}>Clear search</button>
        </div>
      )}
    </div>
  );
}
