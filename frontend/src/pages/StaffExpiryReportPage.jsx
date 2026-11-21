import { useEffect, useState } from 'react';
import { request } from '../api/client';

const TONE = { Active: '#1b7f4b', Expired: '#b3261e', Pending: '#8a5a00', Inactive: '#b3261e', Cancelled: '#b3261e' };

/** Consolidated This Month Expiry Report - Barcode AutoPrint, GSTR2Tally, Mothys and every UNIVERSE project in one list, limited to the projects the
 *  signed-in staff member may see. The only screen a restricted User (Report Viewer) can open. Its total reconciles with the Overall Dashboard tile. */
export default function StaffExpiryReportPage() {
  const [data, setData] = useState(null);
  const [f, setF] = useState({ project: '', month: '', license_type: '', status: '', search: '' });
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    setError('');
    const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    request(`/staff/expiry-report/?${q}`).then((d) => current && setData(d), (e) => current && setError(e.message));
    return () => { current = false; };
  }, [f]);
  const up = (patch) => setF((x) => ({ ...x, ...patch }));
  const fmt = (iso) => new Date(iso).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric' });
  const input = { padding: '6px 8px', border: '1px solid #b9cde4', borderRadius: 6, background: '#fff', font: 'inherit', fontSize: 13 };
  return (
    <section aria-label="Consolidated This Month Expiry Report" style={{ display: 'grid', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <select aria-label="Project" value={f.project} onChange={(e) => up({ project: e.target.value })} style={input}>
          <option value="">All authorized projects</option>
          {data?.projects.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
        </select>
        <input aria-label="Expiry month" type="month" value={f.month || data?.month || ''} onChange={(e) => up({ month: e.target.value })} style={input} />
        <select aria-label="License type" value={f.license_type} onChange={(e) => up({ license_type: e.target.value })} style={input}>
          <option value="">All license types</option><option>Trial</option><option>Yearly</option><option>Lifetime</option>
        </select>
        <select aria-label="Status" value={f.status} onChange={(e) => up({ status: e.target.value })} style={input}>
          <option value="">All statuses</option><option>Active</option><option>Expired</option><option>Pending</option><option>Inactive</option><option>Cancelled</option>
        </select>
        <input aria-label="Search customer" type="search" placeholder="Search customer, company, User ID" value={f.search} onChange={(e) => up({ search: e.target.value })} style={{ ...input, flex: '1 1 220px' }} />
        {data && <strong style={{ fontSize: 13, color: '#12305a' }}>{data.total} license{data.total === 1 ? '' : 's'} expiring in {data.month}</strong>}
      </div>
      {error && <p role="alert" style={{ color: '#b3261e' }}>{error}</p>}
      {!data && !error && <p>Loading…</p>}
      {data && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr>{['Project', 'Customer', 'Company', 'User ID / Serial', 'License Type', 'Expiry Date', 'Remaining Days', 'License Status'].map((h) => <th key={h} style={{ textAlign: 'left', padding: 6, background: '#e9f1fb', whiteSpace: 'nowrap' }}>{h}</th>)}</tr></thead>
            <tbody>
              {data.results.map((r) => (
                <tr key={`${r.project}-${r.user_id}`}>
                  {[r.product, r.customer, r.company || '—', r.user_id, r.license_type, fmt(r.expires_at), r.remaining_days].map((c, j) => <td key={j} style={{ padding: 6, borderBottom: '1px solid #dbe5f1' }}>{c}</td>)}
                  <td style={{ padding: 6, borderBottom: '1px solid #dbe5f1', color: TONE[r.status] || '#12305a', fontWeight: 600 }}>{r.status}</td>
                </tr>
              ))}
              {data.results.length === 0 && <tr><td colSpan={8} style={{ padding: 10 }}>No licenses match these filters.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
