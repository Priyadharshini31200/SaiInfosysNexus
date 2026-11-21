import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { download, request } from '../api/client';
import { openPrintWindow, saveFile } from '../utils/exporter';
import styles from './ConsolidatedReportsPage.module.css';

const ENDPOINT = '/reports/consolidated/';
const TABS = [
  ['expiry', 'License Expiry Report'],
  ['tally', 'Tally Details Report'],
  ['performance', 'User Performance Report'],
];
const DATE_OPTIONS = [
  ['expiry', 'License Expiry Date'],
  ['tss_expiry', 'Tally TSS Expiry'],
  ['purchase_date', 'Purchase Date'],
  ['activation', 'Activation Date'],
];
const PERIODS = [
  ['this_month', 'This Month'],
  ['next_month', 'Next Month'],
  ['this_year', 'This Year'],
  ['custom', 'Custom Date Range'],
];
const options = (entries) => entries.map(([value, label]) => <option key={value} value={value}>{label}</option>);
const defaults = (report, project = 'all') => ({
  report, project, project_type: 'all', filter_by: { expiry: 'expiry', tally: 'tss_expiry', performance: 'activation' }[report],
  period: 'this_month', search: '', from: '', to: '',
});
function initialFilters(report, project, params, restricted) {
  const values = defaults(report, project);
  for (const key of restricted ? ['search'] : ['project_type', 'filter_by', 'period', 'search', 'from', 'to']) {
    if (params.get(key)) values[key] = params.get(key);
  }
  if (!restricted && values.from && values.to && !params.has('period')) values.period = 'custom';
  return values;
}
const formatDate = (value) => value ? new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(value)) : '—';
const dateKeys = new Set(['expiry', 'purchase_date', 'tss_expiry', 'last_used']);
const count = new Intl.NumberFormat('en-IN');

export default function ConsolidatedReportsPage({ restricted = false }) {
  const [params, setParams] = useSearchParams();
  const [access, setAccess] = useState(restricted ? ['expiry'] : null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (restricted) return undefined;
    let active = true;
    request(ENDPOINT + '?meta=1').then((data) => active && setAccess(data.reports), (err) => active && setError(err.message));
    return () => { active = false; };
  }, [restricted]);

  if (error) return <p className={styles.error} role="alert">{error}</p>;
  if (!access) return <p className={styles.message} role="status">Loading reports…</p>;
  const report = restricted ? 'expiry' : access.includes(params.get('report')) ? params.get('report') : access[0];
  if (!report) return <p className={styles.message}>You do not have access to any reports.</p>;
  const project = params.get('project') || 'all';

  return (
    <div className={styles.workspace}>
      {!restricted && <div className={styles.tabs} aria-label="Report type">
        {TABS.filter(([value]) => access.includes(value)).map(([value, label]) => (
          <button type="button" key={value} aria-pressed={report === value} onClick={() => setParams({ report: value })}>{label}</button>
        ))}
      </div>}
      <Report key={params.toString()} report={report} initial={initialFilters(report, project, params, restricted)} restricted={restricted} />
    </div>
  );
}

/** Summary strip per report: only values the backend really computes for that report (null = not available, never a fake 0). */
const SUMMARY = {
  expiry: [['license_month', 'Expiring this month'], ['expired', 'Expired'], ['purchased_month', 'Purchased this month']],
  tally: [['tss_month', 'TSS expiring this month']],
  performance: [],
};
const NOT_REPORTED = new Set(['edition', 'tss_details', 'administrator', 'tss_expiry']); // Tally fields the connector may not have sent yet

function Report({ report, initial, restricted }) {
  const [draft, setDraft] = useState(initial); // what the controls show; every change is applied automatically
  const [filters, setFilters] = useState(initial); // what the table is loaded with
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');

  // Auto-apply: selects at once, search after a 300 ms pause, a custom range only once both dates are valid.
  useEffect(() => {
    const customIncomplete = draft.period === 'custom' && (!draft.from || !draft.to || draft.from > draft.to);
    if (customIncomplete || JSON.stringify(draft) === JSON.stringify(filters)) return undefined;
    const t = setTimeout(() => { setExportError(''); setPage(1); setFilters(draft); }, draft.search !== filters.search ? 300 : 0);
    return () => clearTimeout(t);
  }, [draft, filters]);

  useEffect(() => {
    let active = true; // a newer request makes this one stale: its answer is ignored
    setLoading(true);
    setError('');
    const query = new URLSearchParams({ ...filters, page, ...(sort ? { sort } : {}) });
    request(ENDPOINT + '?' + query).then((result) => {
      if (active) { setData(result); setLoading(false); }
    }, (err) => {
      if (active) { setError(err.message); setLoading(false); }
    });
    return () => { active = false; };
  }, [filters, page, sort, retry]);

  const set = (key) => (event) => {
    const value = event.target.value;
    setDraft((old) => key === 'project_type' ? { ...old, project: 'all', project_type: value } : { ...old, [key]: value });
  };
  const reset = () => { const next = defaults(report); setDraft(next); setSort(''); setPage(1); setFilters(next); };
  const filteredProjects = (data?.projects || []).filter((p) => draft.project_type === 'all' || p.tally === (draft.project_type === 'tally'));
  const changePage = (value) => setPage(value);
  const sortBy = (key) => { setPage(1); setSort((s) => (s === key ? `-${key}` : key)); };
  const cell = (key, value) => {
    if (key === 'status' && value) return <span className={styles.status} data-status={value}>{value}</span>;
    if (key === 'expiry' && !value && report === 'expiry') return '—';
    if (key === 'remaining_days' && value != null) return value < 0 ? <span className={styles.overdue}>Expired {-value} day{value === -1 ? '' : 's'} ago</span> : `${value} day${value === 1 ? '' : 's'}`;
    if (value == null || value === '') return report === 'tally' && NOT_REPORTED.has(key) ? <span className={styles.na}>Not Reported</span> : '—';
    return dateKeys.has(key) ? formatDate(value) : value;
  };

  async function exportReport(fmt) {
    const print = fmt === 'print' ? openPrintWindow() : null;
    setExportError('');
    setExporting(true);
    try {
      const body = { fmt, filters: { ...filters, ...(sort ? { sort } : {}) } }; // same filters and order as the table, all pages
      if (print) print.finish(await request(ENDPOINT, { method: 'POST', body }));
      else saveFile(await download(ENDPOINT, body));
    } catch (err) {
      print?.fail();
      setExportError(err.message);
    } finally { setExporting(false); }
  }

  const summaryItems = (restricted ? SUMMARY.expiry.slice(0, 1) : SUMMARY[report]);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const emptyText = report === 'tally' ? 'No Tally records match the selected filters.' : 'No records match the selected filters.';
  return (
    <>
      <div className={styles.filters} role="search" aria-label="Report filters (applied automatically)">
        <label className={styles.wideField}>Project<select value={draft.project} onChange={set('project')}>
          <option value="all">All Projects</option>
          {filteredProjects.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
        </select></label>
        <label>Project Type<select value={draft.project_type} onChange={set('project_type')}>
          <option value="all">All</option><option value="tally">Tally</option><option value="non_tally">Non-Tally</option>
        </select></label>
        {!restricted && <label>Filter By<select value={draft.filter_by} onChange={set('filter_by')}>{options(DATE_OPTIONS)}</select></label>}
        {!restricted && <label>Period<select value={draft.period} onChange={set('period')}>{options(PERIODS)}</select></label>}
        {!restricted && draft.period === 'custom' && <>
          <label className={styles.dateField}>From<input type="date" value={draft.from} max={draft.to || undefined} onChange={set('from')} /></label>
          <label className={styles.dateField}>To<input type="date" value={draft.to} min={draft.from || undefined} onChange={set('to')} /></label>
        </>}
        <label className={styles.searchField}>Search<input type="search" value={draft.search} onChange={set('search')} maxLength={100} placeholder="Name, ID, serial, email" /></label>
        <button type="button" className={`${styles.secondary} ${styles.reset}`} onClick={reset} title="Restore the default filters">Reset</button>
      </div>
      <section className={styles.tablePanel} aria-label={data?.title || 'Report results'} aria-busy={loading}>
        <div className={styles.tableBar}>
          <div className={styles.resultCount} role="status">
            {error ? <span className={styles.failed}>Report could not be loaded</span> : !data ? 'Loading report…' : (
              <>
                <b>{count.format(data.total)} {data.total === 1 ? 'Record' : 'Records'}</b>
                {summaryItems.map(([key, label]) => (
                  <span key={key} className={styles.kpi}>{label}: <b>{data.summary[key] == null ? 'Not available' : count.format(data.summary[key])}</b></span>
                ))}
                <span className={styles.range}>{formatDate(data.from)} – {formatDate(data.to)}</span>
                {loading && <span className={styles.spinner}>Updating…</span>}
              </>
            )}
          </div>
          {data?.can_export && !error && <div className={styles.exportActions} aria-label="Export report">
            {[['xlsx', 'Excel'], ['pdf', 'PDF'], ['print', 'Print']].map(([fmt, label]) => (
              <button key={fmt} type="button" className={styles.secondary} disabled={loading || exporting || !data.total} onClick={() => exportReport(fmt)}>{label}</button>
            ))}
          </div>}
        </div>
        {exportError && <p className={styles.error} role="alert">{exportError}</p>}
        {error && <p className={styles.error} role="alert">{error} <button type="button" className={styles.secondary} onClick={() => setRetry((n) => n + 1)}>Retry</button></p>}
        {!error && data && <div className={`${styles.tableScroll} ${loading ? styles.stale : ''}`}>
          <table>
            <thead><tr>{data.columns.map(([key, label]) => {
              const dir = sort === key ? 'ascending' : sort === `-${key}` ? 'descending' : 'none';
              return (
                <th scope="col" key={key} aria-sort={dir}>
                  <button type="button" className={styles.sortBtn} onClick={() => sortBy(key)} title={`Sort by ${label}`}>
                    {label}<span aria-hidden="true">{dir === 'ascending' ? ' ▲' : dir === 'descending' ? ' ▼' : ''}</span>
                  </button>
                </th>
              );
            })}</tr></thead>
            <tbody>{data.results.map((row, i) => (
              <tr key={i}>{data.columns.map(([key]) => (
                <td key={key} className={key === 'customer' ? styles.customer : ['remaining_days', 'login_count', 'active_minutes'].includes(key) ? styles.num : undefined}>
                  {key === 'expiry' && !row[key] && String(row.license_type || '').toUpperCase().includes('LIFETIME') ? 'Lifetime' : cell(key, row[key])}
                </td>
              ))}</tr>
            ))}{!data.results.length && <tr><td colSpan={data.columns.length} className={styles.empty}>{emptyText}</td></tr>}</tbody>
          </table>
        </div>}
        {!error && data && data.total > data.page_size && <div className={styles.pagination}>
          <button type="button" className={styles.secondary} disabled={loading || page === 1} onClick={() => changePage(page - 1)}>Previous</button>
          <span>Page {page} of {pages}</span>
          <button type="button" className={styles.secondary} disabled={loading || page >= pages} onClick={() => changePage(page + 1)}>Next</button>
        </div>}
      </section>
    </>
  );
}
