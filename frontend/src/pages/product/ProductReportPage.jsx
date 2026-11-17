import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import ExportButtons from '../../components/ExportButtons';
import Toast from '../../components/Toast';
import { DateFilter, Filter, ListPanel, REPORT_MAX_ROWS, SearchBox, useAllRows, useDebounced, useToast } from '../tally/TallyShared';
import { STATUS_LABEL, TYPE_LABEL } from './ProductCustomersPage';
import styles from '../tally/Tally.module.css';

const TITLES = { expiry: 'License Expiry Report', details: 'Customer Details Report', performance: 'User Performance Report' };
const opts = (labels) => [{ value: 'all', label: 'All' }, ...Object.entries(labels).map(([value, label]) => ({ value, label }))];
const CATEGORIES = opts({ speed: 'Speed User', normal: 'Normal User', new: 'New User', one_time: 'One-Time User' });
const USAGES = opts({ used: 'Used', not_used: 'Not Used' });

/** Mothys reports: the same three report patterns as GSTR Reports, built only from Mothys data (no Tally). The customer name opens View More. */
export default function ProductReportPage({ kind }) {
  const { code = 'MOTHYS' } = useParams();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const term = useDebounced(search);
  const { toast, clear, notify } = useToast();
  const g = (k) => params.get(k) || 'all';
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const filters = useMemo(() => ({
    search: term, from, to,
    ...(kind === 'expiry' ? { license_type: g('type'), status: g('status') } : {}),
    ...(kind === 'performance' ? { category: g('category'), usage: g('usage') } : {}),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [kind, term, from, to, params]);
  const { rows, loading, error, total, truncated } = useAllRows((page, page_size) => integrationsApi.productReport(code, kind, { ...filters, page, page_size }), [code, kind, filters]);
  const set = (k, v) => setParams((p) => { const n = new URLSearchParams(p); if (!v || v === 'all') n.delete(k); else n.set(k, v); return n; }, { replace: true });
  const columns = ['S.No', 'Customer Name', ...({
    expiry: ['Email', 'Contact Number', 'Serial Number', 'License Type', 'Activation Date', 'Expiry Date', 'License Status'],
    details: ['Email', 'Contact Number', 'Mothys Customer ID', 'Nexus User ID / Serial Number', 'License Type', 'Active Device', 'Registration Date', 'Last Login'],
    performance: ['Serial Number', 'User Category', 'Login Count', 'Last Active Date', 'Usage History', 'Active Device'],
}[kind])];
  const note = truncated ? `Showing the first ${REPORT_MAX_ROWS.toLocaleString()} of ${total.toLocaleString()} records. Narrow the filters or use Export for the complete list.` : null;
  return (
    <>
      <ListPanel label={TITLES[kind]} columns={columns} loading={loading} error={error} empty="No records match." note={note}
        toolbar={(
          <>
            <SearchBox value={search} onChange={setSearch} placeholder="Search customer or serial" label="Search report" />
            {kind === 'expiry' && <Filter label="Plan" value={g('type')} onChange={(v) => set('type', v)} options={opts(TYPE_LABEL)} />}
            {kind === 'expiry' && <Filter label="Status" value={g('status')} onChange={(v) => set('status', v)} options={opts(STATUS_LABEL)} />}
            {kind === 'performance' && <Filter label="Category" value={g('category')} onChange={(v) => set('category', v)} options={CATEGORIES} />}
            {kind === 'performance' && <Filter label="Usage" value={g('usage')} onChange={(v) => set('usage', v)} options={USAGES} />}
            <DateFilter label="From" value={from} onChange={(v) => set('from', v)} />
            <DateFilter label="To" value={to} onChange={(v) => set('to', v)} />
            <span className={styles.count}>Total: {total}</span>
            <div className={styles.right}>
              <ExportButtons menu disabled={loading || total === 0} onError={(t) => notify('error', t)} run={(fmt) => integrationsApi.exportProductReport(code, kind, fmt, filters)} />
            </div>
          </>
        )}
      >
        {rows.map((r, i) => (
          <tr key={r.id}>
            <td>{i + 1}</td>
            <td className={styles.name}><Link className={styles.link} to={`/product/${code}/customers/${r.id}`}>{r.cells[0]}</Link></td>
            {r.cells.slice(1).map((c, j) => <td key={j}>{c}</td>)}
          </tr>
        ))}
      </ListPanel>
      <Toast toast={toast} onDone={clear} />
    </>
  );
}
