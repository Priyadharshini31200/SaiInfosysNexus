import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { integrationsApi } from '../api/integrations';
import { useAuth } from '../context/AuthContext';
import { useTallyAccess } from '../context/TallyAccessContext';
import { DashboardIcon, FolderIcon, SearchIcon, UserIcon } from '../components/Icons';
import ActionIcon from '../components/ActionIcon';
const PlugSvg = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 2v6" /><path d="M15 2v6" /><path d="M6 8h12v4a6 6 0 0 1-12 0Z" /><path d="M12 18v4" /></svg>;
import CustomerPicker from './CustomerPicker';
import styles from './ProjectDirectory.module.css';

const META = {
  tally: { title: 'Tally SL Projects', crumb: 'Tally SL Projects' },
  'non-tally': { title: 'Non-Tally SL Projects', crumb: 'Non-Tally SL Projects' },
};

/** PRODUCT MANAGEMENT -> Tally SL / Non-Tally SL Projects: ONE compact directory (Project | Dashboard | Customers). Icons jump straight to the existing
 *  pages (no intermediate screen). Search + page live in the URL so Back / refresh / deep links keep them. Authorisation is still enforced by the APIs. */
const statusLabel = (r) => (!r.universe ? 'Active' : r.status === 'Pending' ? 'Setup Pending' : r.status);
const statusTone = (r) => ({ Active: styles.ok, 'Setup Pending': styles.warn, Archived: styles.muted, Suspended: styles.bad }[statusLabel(r)] || styles.muted);
const apiLabel = (r) => {
  if (!r.universe) return '—';
  if (r.manual) return 'Manual';
  if (r.api?.sync === 'FAILED' || r.api?.last_error) return 'Sync failed';
  return r.api?.integration === 'ACTIVE' ? 'Connected' : 'Not activated';
};

export default function ProjectDirectoryPage({ kind }) {
  const { user, can } = useAuth();
  const [pick, setPick] = useState(null); // { kind, code, mode } - the project whose customer is being chosen for Edit / Delete
  const { products } = useTallyAccess();
  const [params, setParams] = useSearchParams();
  const search = params.get('search') || '';
  const statusFilter = params.get('status') || '';
  const [summary, setSummary] = useState({ total: 0, active: 0, pending: 0, archived: 0 });
  const navigate = useNavigate();
  const [revision, setRevision] = useState(0);
  const [mothys, setMothys] = useState([]);
  const [uni, setUni] = useState([]);
  const [state, setState] = useState('loading');

  const [uniTotal, setUniTotal] = useState(0);
  const [uniPage, setUniPage] = useState(1);
  const [more, setMore] = useState(false);
  const seq = useRef(0);
  const toRow = (p) => ({ code: p.code, name: p.name, desc: p.code, status: p.status, tally: p.tally_required, api: p.api_status, customerCount: p.customer_count, manual: p.manual, dashboard: p.can.dashboard ? `/universe/projects/${p.code}/dashboard` : null, customers: p.can.customers ? `/universe/projects/${p.code}/customers` : null, grants: p.can, universe: true });
  useEffect(() => { // UNIVERSE projects of this category: server-side search + incremental paging (no visible pager). Setup Pending projects are included.
    const mine = ++seq.current;
    setState('loading');
    const t = setTimeout(() => integrationsApi.universeHub({ type: kind === 'tally' ? 'tally' : 'non-tally', search: search.trim(), status: statusFilter, page: uniPage, page_size: 30 }).then((d) => {
      if (mine !== seq.current) return;
      if (uniPage === 1) setMothys((d.legacy || []).map((p) => ({
        code: p.code, name: p.name, desc: p.code,
        dashboard: p.can.dashboard ? `/product/${p.code}/dashboard` : null,
        customers: p.can.customers ? `/product/${p.code}/customers` : null,
        picker: { kind: 'product', edit: Boolean(p.can.customers) && can('edit', p.code), del: false }, // Mothys: own product API; it has no customer-delete endpoint
      })));
      if (uniPage === 1 && d.summary) setSummary(d.summary);
      setUni((cur) => (uniPage === 1 ? d.results.map(toRow) : [...cur, ...d.results.map(toRow)]));
      setUniTotal(d.total); setMore(false); setState('ok');
    }, () => { if (mine === seq.current) { if (uniPage === 1) { setUni([]); setMothys([]); } setMore(false); setState('ok'); } }), 200); // legacy products keep working even if the UNIVERSE registry is unavailable
    return () => { clearTimeout(t); seq.current++; };
  }, [kind, search, statusFilter, uniPage, revision]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setUniPage(1); }, [kind, search, statusFilter]);
  const onScroll = (e) => { const el = e.currentTarget; if (state === 'ok' && !more && uni.length < uniTotal && el.scrollTop + el.clientHeight >= el.scrollHeight - 80) { setMore(true); setUniPage((n) => n + 1); } };

  const rows = useMemo(() => {
    if (kind === 'tally') {
      const own = products.map((p) => ({
        code: p.code, name: p.name, desc: 'Tally SL product',
        dashboard: p.actions?.view ? `/tally/${p.code}/dashboard` : null, customers: p.actions?.view ? `/tally/${p.code}/customers` : null,
        picker: { kind: 'tally', edit: Boolean(p.actions?.view && p.actions?.edit), del: Boolean(p.actions?.view && p.actions?.delete) },
      })).filter((r) => r.dashboard || r.customers);
      return [...own, ...mothys, ...uni];
    }
    const ok = can('entry', 'prn-auto-print') || can('view', 'prn-auto-print') || can('approve', 'prn-auto-print') || can('device_transfer', 'prn-auto-print');
    const view = can('view', 'prn-auto-print') || can('approve', 'prn-auto-print') || can('device_transfer', 'prn-auto-print');
    return [...(ok ? [{ code: 'prn-auto-print', name: 'Barcode AutoPrint', desc: 'Non-Tally SL product', dashboard: null, customers: view ? '/projects/prn-auto-print/customers' : '/projects/prn-auto-print/add', picker: { kind: 'barcode', edit: can('edit', 'prn-auto-print'), del: can('delete', 'prn-auto-print') } }] : []), ...uni];
  }, [kind, products, mothys, can, uni]);

  const q = search.trim().toLowerCase();
  const filtered = rows.filter((r) => (!q || r.name.toLowerCase().includes(q) || r.code.toLowerCase().includes(q)) && (r.universe || !statusFilter || statusFilter === 'active'));
  const legacyCount = rows.filter((r) => !r.universe).length;
  const cards = [['Total Projects', summary.total + legacyCount], ['Active Projects', summary.active + legacyCount], ['Setup Pending', summary.pending], ['Archived', summary.archived]];
  const shown = [...new Map(filtered.map((row) => [row.code, row])).values()];
  const set = (patch) => { const n = new URLSearchParams(window.location.search); Object.entries({ page: 1, ...patch }).forEach(([k, v]) => (v && v !== 1 ? n.set(k, v) : n.delete(k))); setParams(n, { replace: true }); };
  const from = { from: `${window.location.pathname}${window.location.search}`, crumb: META[kind].crumb };
  const removeProject = async (r) => { // safe delete = archive: customers, licences and history are retained
    if (!window.confirm(`Delete Project - ${r.name}?

An empty project is removed permanently. A project that has customers or history is protected and cannot be deleted.`)) return;
    const refresh = () => { setUniPage(1); setRevision((v) => v + 1); };
    try { await integrationsApi.deleteProject(r.code); refresh(); } catch (e) {
      if (e.status !== 409) { window.alert(e.message); return; }
      if (!window.confirm(`${e.message}

Archive ${r.name} now?`)) return; // safe alternative: deactivate, keep every record
      try { await integrationsApi.updateProject(r.code, { lifecycle: 'archive' }); refresh(); } catch (x) { window.alert(x.message); }
    }
  };
  const Action = ({ to, label, name, Icon }) => to
    ? <Link to={to} state={{ ...from, project: name, page: label === 'Customers' ? 'Customer List' : label }} className={styles.act} title={`${name} ${label}`} aria-label={`${name} ${label}`}><Icon size={18} /></Link>
    : <span className={`${styles.act} ${styles.off}`} title={`${label} not available for ${name}`} aria-label={`${label} not available`}><Icon size={18} /></span>;

  return (
    <div className={styles.page}>
      <div className={styles.summaryRow}>
        <div className={styles.cards} role="list" aria-label="Project summary">
          {cards.map(([label, value]) => <div key={label} role="listitem" className={styles.card}><small>{label}</small><b>{value}</b></div>)}
        </div>
        {user?.role === 'SUPERADMIN' && <Link to={`/universe/projects/add?category=${kind}`} className={styles.add}>+ Add Project</Link>}
      </div>
      <div className={styles.toolbar}>
        <label className={styles.search}><SearchIcon size={16} /><input type="search" placeholder="Search project name or code..." value={search} onChange={(e) => set({ search: e.target.value })} aria-label="Search projects" />{search && <button type="button" className={styles.clear} onClick={() => set({ search: '' })} aria-label="Clear search">×</button>}</label>
        <select className={styles.filter} value={statusFilter} onChange={(e) => set({ status: e.target.value })} aria-label="Status filter">
          <option value="">All statuses</option><option value="active">Active</option><option value="pending">Setup Pending</option><option value="archived">Archived</option>
        </select>
      </div>
      <div className={styles.scroll} tabIndex={0} aria-label="Project directory" onScroll={onScroll}>
      <table className={styles.table}>
        <thead><tr><th>Project</th><th>Project Code</th>{kind === 'tally' && <th>Tally Integration</th>}<th>Status</th>{kind === 'tally' && <th>API Status</th>}<th className={styles.c}>Customers</th><th className={styles.actionsHead}>Actions</th></tr></thead>
        <tbody>
          {shown.map((r) => {
            const target = r.dashboard || r.customers;
            const linkState = { ...from, project: r.name, page: r.dashboard ? 'Dashboard' : 'Customer List' };
            const Name = target ? Link : 'span';
            return (
            <tr key={r.code} className={target ? styles.clickable : undefined} onClick={(event) => {
              if (target && !event.target.closest('a,button,[popover],[data-project-action]')) navigate(target, { state: linkState });
            }}>
              <td><Name className={styles.name} to={target || undefined} state={linkState}><FolderIcon size={18} /><span><b>{r.name}</b><small>{r.desc}{r.universe && r.status ? ` · ${r.status === 'Pending' ? 'Setup Pending' : r.status}` : ''}</small></span></Name></td>
              <td className={styles.mono}>{r.code}</td>
              {kind === 'tally' && <td>{r.universe ? (r.tally ? 'Required' : 'Not required') : r.picker?.kind === 'tally' || r.picker?.kind === 'product' ? 'Required' : '—'}</td>}
              <td><span className={`${styles.pill} ${statusTone(r)}`}>{statusLabel(r)}</span></td>
              {kind === 'tally' && <td>{apiLabel(r)}</td>}
              <td className={styles.c}>{r.universe ? r.customerCount ?? '—' : '—'}</td>
              <td className={styles.actionsCell}><div className={styles.actions}>
              <span className={styles.cell}><Action to={r.dashboard} label="Dashboard" name={r.name} Icon={DashboardIcon} /></span>
              <span className={styles.cell}><Action to={r.customers} label="Customers" name={r.name} Icon={UserIcon} /></span>
              {kind === 'tally' && <span className={styles.cell} data-project-action onClick={(e) => e.stopPropagation()}>{r.universe && r.grants?.configure
                ? <Link to={`/universe/api?project=${encodeURIComponent(r.code)}`} className={styles.act} style={{ color: '#0056b3' }} title="API Integration" aria-label={`${r.name} API Integration`}><PlugSvg /></Link>
                : <span className={`${styles.act} ${styles.off}`} role="img" tabIndex={0} title={r.universe ? 'You do not have permission to configure this API.' : 'This legacy product has no API Integration workspace here.'} aria-label="API Integration unavailable"><PlugSvg /></span>}</span>}
              <span className={styles.cell} data-project-action onClick={(e) => e.stopPropagation()}>{r.universe && r.grants?.edit
                ? <ActionIcon kind="edit" to={`/universe/projects/${r.code}/edit`} state={{ ...from, project: r.name, page: 'Edit Project' }} title={`Edit ${r.name}`} />
                : r.picker?.edit
                  ? <ActionIcon kind="edit" title="Edit Customer" onClick={() => setPick({ kind: r.picker.kind, code: r.code, mode: 'edit' })} />
                  : <ActionIcon kind="edit" disabled title={r.universe ? 'You do not have permission to edit this project.' : r.picker ? 'You do not have permission to edit customers.' : 'Legacy project settings cannot be edited here.'} />}</span>
              <span className={styles.cell} data-project-action onClick={(e) => e.stopPropagation()}>{r.universe && r.grants?.archive
                ? <ActionIcon kind="delete" title={`Delete ${r.name}`} onClick={() => removeProject(r)} />
                : r.picker?.del
                  ? <ActionIcon kind="delete" title="Delete Customer" onClick={() => setPick({ kind: r.picker.kind, code: r.code, mode: 'delete' })} />
                  : <ActionIcon kind="delete" disabled title={r.universe ? 'You do not have permission to delete this project.' : r.picker?.kind === 'product' ? 'Customer deletion is not supported for this product.' : r.picker ? 'You do not have permission to delete customers.' : 'Legacy projects are protected from deletion.'} />}</span>
              </div></td>
            </tr>
          ); })}
          {state === 'loading' && !shown.length && <tr><td colSpan={kind === 'tally' ? 7 : 5} className={styles.empty}>Loading projects…</td></tr>}
          {state !== 'loading' && !shown.length && <tr><td colSpan={kind === 'tally' ? 7 : 5} className={styles.empty}>{q ? 'No projects match your search.' : 'No projects are available to you.'}</td></tr>}
        </tbody>
      </table>
      </div>
      {pick && <CustomerPicker kind={pick.kind} code={pick.code} mode={pick.mode} onClose={() => setPick(null)} />}
    </div>
  );
}
