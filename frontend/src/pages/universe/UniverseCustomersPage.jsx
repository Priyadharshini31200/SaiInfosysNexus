import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import { useAuth } from '../../context/AuthContext';
import { formatDateTime } from '../../utils/date';
import ExportButtons from '../../components/ExportButtons';
import { RefreshIcon } from '../../components/Icons';
import UniverseCustomerDrawer from './UniverseCustomerDrawer';
import styles from '../integrations/Integrations.module.css';

const TONE = { 'Pending Review': styles.pending, 'Pending Tally Verification': styles.off, 'On Hold': styles.pending, Rejected: styles.off, Active: styles.ok, Expired: styles.off, Suspended: styles.off, 'Pending Activation': styles.pending };
const USAGE = { speed: 'Speed', normal: 'Normal', new: 'New', one_time: 'One-Time', not_used: 'Not used' };
const LABEL = { 'Pending Review': 'Pending Approval' }; // display only - the stored status value is unchanged

/** UNIVERSE → <Project> → Customers: fetched registrations and approved customers in one list; a row opens the customer popup (review / approve / edit). */
export default function UniverseCustomersPage() {
  const { code } = useParams();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState({ search: '', status: params.get('status') || 'all', license_type: 'all', usage: params.get('usage') || 'all' });
  const [data, setData] = useState({ total: 0, results: [], statuses: [], project: null });
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const seq = useRef(0); // ignore answers of superseded requests
  const open = (params.get('open') || '').split(':');
  const openKind = open[0] || null;
  const openId = Number(open[1]) || null;

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try { const d = await integrationsApi.universeCustomers(code, { ...filters, page, page_size: 25 }); if (mine === seq.current) { setData(d); setError(''); } } catch (e) { if (mine === seq.current) setError(e.message); }
  }, [code, filters, page]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [filters, code]);
  const [mode, setMode] = useState(null); // POLL | PUSH, from the saved API configuration
  useEffect(() => { integrationsApi.apiConfig(code).then((c) => setMode(c.config?.sync_mode || 'POLL'), () => setMode('POLL')); }, [code]);
  const push = mode === 'PUSH' || mode === 'REALTIME';
  useEffect(() => { // webhook projects: new registrations arrive by themselves - refresh quietly every 30 s while the tab is visible
    if (!push) return undefined;
    const t = setInterval(() => { if (document.visibilityState === 'visible') load(); }, 30000);
    return () => clearInterval(t);
  }, [push, load]);

  const setOpen = (kind, id) => { const p = new URLSearchParams(params); if (kind) p.set('open', `${kind}:${id}`); else p.delete('open'); setParams(p); };
  const sync = async () => {
    setBusy(true); setMsg('');
    try { const r = await integrationsApi.syncCustomers(code); setMsg(`Sync finished: ${r.result.created} new, ${r.result.existing} already known, ${r.result.pending_tally} pending Tally, ${r.result.failed} failed.`); await load(); } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => { // same request as the automatic refresh; filters and search are kept
    if (refreshing) return;
    setRefreshing(true); setMsg('');
    try { await load(); } finally { setRefreshing(false); }
  };
  const up = (patch) => setFilters((f) => ({ ...f, ...patch }));
  const pages = Math.max(Math.ceil(data.total / 25), 1);

  return (
    <div className={styles.page}>
      <div className={styles.panel}>
        {data.project?.manual ? (
          <div className={styles.actions} style={{ marginBottom: 10, alignItems: 'center' }}>
            <input type="search" placeholder="Search customer" value={filters.search} onChange={(e) => up({ search: e.target.value })} aria-label="Search customers" style={{ flex: '1 1 260px', padding: 7 }} />
            <select value={filters.license_type} onChange={(e) => up({ license_type: e.target.value })} aria-label="License type"><option value="all">All license types</option><option>Trial</option><option>Yearly</option><option>Lifetime</option></select>
            <select value={filters.status} onChange={(e) => up({ status: e.target.value })} aria-label="Status"><option value="all">All statuses</option>{['Active', 'Expired', 'Suspended', 'Pending Activation'].map((x) => <option key={x}>{x}</option>)}</select>
            <b>Total Customers: {data.total}</b>
            <ExportButtons menu disabled={data.total === 0} onError={(t) => setMsg(t)} run={(fmt) => integrationsApi.exportProductCustomers(code, fmt, { search: filters.search, license_type: { Trial: 'TRIAL', Yearly: 'ONE_YEAR', Lifetime: 'LIFETIME' }[filters.license_type] })} />
            {user?.role === 'SUPERADMIN' && <Link className={styles.btn} to={`/universe/projects/${code}/customers/add`} state={{ crumb: 'Customers', project: data.project.name, page: 'Add Customer' }}>+ Add Customer</Link>}
          </div>
        ) : null}
        {!data.project?.manual && <div className={styles.listToolbar} role="search" aria-label="Customer filters">
          <input type="search" className={styles.listSearch} placeholder="Search name, company, email, contact, User ID" value={filters.search} onChange={(e) => up({ search: e.target.value })} aria-label="Search customers" />
          <select value={filters.status} onChange={(e) => up({ status: e.target.value })} aria-label="Status"><option value="all">All statuses</option>{['Pending Review', 'Pending Tally Verification', 'On Hold', 'Pending Activation', 'Active', 'Expired', 'Suspended', 'Rejected'].map((s) => <option key={s} value={s}>{LABEL[s] || s}</option>)}</select>
          <select value={filters.license_type} onChange={(e) => up({ license_type: e.target.value })} aria-label="License type"><option value="all">All license types</option><option>Trial</option><option>Yearly</option><option>Lifetime</option></select>
          <select value={filters.usage} onChange={(e) => up({ usage: e.target.value })} aria-label="Usage category"><option value="all">All usage categories</option><option value="used">Used</option><option value="speed">Speed</option><option value="normal">Normal</option><option value="new">New</option><option value="one_time">One-Time</option><option value="not_used">Not used</option></select>
          {!push && mode && user?.role === 'SUPERADMIN' && <button type="button" className={`${styles.btn} ${styles.ghost} ${styles.toolBtn}`} disabled={busy} onClick={sync}>{busy ? 'Syncing…' : 'Sync Now'}</button>}
          <button type="button" className={styles.iconBtn} onClick={refresh} disabled={refreshing} title="Refresh Customers" aria-label="Refresh Customers" aria-busy={refreshing}>
            <RefreshIcon size={18} className={refreshing ? styles.spin : undefined} />
          </button>
        </div>}
        {error && <p role="alert" className={styles.err}>{error}</p>}
        {msg && <p role="status" className={styles.note}>{msg}</p>}
        <div className={styles.tableWrap}>
          <table className={`${styles.table} ${data.project?.manual ? '' : styles.compactTable}`}>
            <thead>{data.project?.manual ? <tr><th>S.No</th><th>Name</th><th>Company Name</th><th>Contact No.</th><th>License Type</th><th>Serial No.</th><th>Expiry Date</th></tr> : <tr><th>Customer Name</th><th>Company</th><th>Email</th><th>Contact</th><th>Project</th><th>User ID / Serial</th><th>License</th><th>Expiry</th><th>Status</th></tr>}</thead>
            <tbody>
              {data.project?.manual && data.results.map((r, i) => (
                <tr key={`${r.kind}-${r.id}`} tabIndex={0} onClick={() => setOpen(r.kind, r.id)} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setOpen(r.kind, r.id)} style={{ cursor: 'pointer' }} aria-label={`Open ${r.name}`}>
                  <td>{(page - 1) * 25 + i + 1}</td><td><b>{r.name}</b></td><td>{r.company_name || '—'}</td><td>{r.contact_number}</td><td>{r.license_type || '—'}</td><td className={styles.mono}>{r.user_id || '—'}</td>
                  <td>{r.expires_at ? formatDateTime(r.expires_at) : r.license_type === 'Lifetime' ? 'No expiry' : '—'}</td>
                </tr>
              ))}
              {!data.project?.manual && data.results.map((r) => (
                <tr key={`${r.kind}-${r.id}`} tabIndex={0} onClick={() => setOpen(r.kind, r.id)} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setOpen(r.kind, r.id)} style={{ cursor: 'pointer' }} aria-label={`Open ${r.name}`}>
                  <td title={r.name}><b>{r.name}</b>{r.usage_category && r.kind === 'customer' && <small className={styles.usageTag}>{USAGE[r.usage_category]}</small>}</td>
                  <td title={r.company_name || ''}>{r.company_name || '—'}</td><td title={r.email}>{r.email}</td><td>{r.contact_number}</td><td title={r.project}>{r.project}</td><td className={styles.mono}>{r.user_id || '—'}</td>
                  <td>{r.license_type || '—'}</td><td>{r.kind === 'customer' ? (r.expires_at ? formatDateTime(r.expires_at) : r.license_type === 'Lifetime' ? 'No expiry' : '—') : '—'}</td>
                  <td><span className={`${styles.badge} ${TONE[r.status] || styles.pending}`}>{LABEL[r.status] || r.status}</span></td>
                </tr>
              ))}
              {data.results.length === 0 && <tr><td colSpan={9}>{data.project?.manual ? 'No customers yet. Click + Add Customer.' : push && !filters.search && filters.status === 'all' ? 'Waiting for the first authenticated customer registration from the external application.' : push ? 'No customers match the selected filters.' : 'No customers match. New registrations appear here after the next synchronisation.'}</td></tr>}
            </tbody>
          </table>
        </div>
        {pages > 1 && <div className={styles.actions} style={{ marginTop: 10 }}><button className={`${styles.btn} ${styles.ghost}`} disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages} · {data.total} customers</span><button className={`${styles.btn} ${styles.ghost}`} disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>}
      </div>
      {openKind && openId && <UniverseCustomerDrawer key={`${openKind}:${openId}`} code={code} kind={openKind} id={openId} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}
