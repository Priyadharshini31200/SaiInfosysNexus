import { useCallback, useEffect, useRef, useState } from 'react';
import { integrationsApi } from '../../api/integrations';
import { formatDateTime } from '../../utils/date';
import styles from './Integrations.module.css';
import CustomAuthFields from './CustomAuthFields';
import ProtocolFields from './ProtocolFields';

const AUTH_FORMS = ['BASIC', 'OAUTH2', 'CUSTOM'];

const MAP = [['external_customer_id', 'Customer ID field'], ['customer_name', 'Name field'], ['email', 'Email field'], ['contact_number', 'Mobile field'], ['company_name', 'Company field (optional)'], ['address', 'Address field (optional)']];
const TALLY_MAP = [['tally_serial', 'Tally Serial field'], ['tally_edition', 'Tally Edition field'], ['tally_tss_active', 'TSS active field'], ['tally_license_admin', 'License Administrator field']];
const DEFAULT = {
  customer_method: 'GET', customer_path: '/customers', auth_type: 'NONE', auth_header: '', credential_ref: '', auth_config: {}, api_protocol: { type: 'REST' }, send_welcome_email: false, request_headers: {},
  field_mapping: { external_customer_id: 'id', customer_name: 'name', email: 'email', contact_number: 'phone' },
  pagination: { type: 'page', page_param: 'page', size_param: 'page_size', page_size: 100, results_path: 'results' },
  tally_path: '', tally_id_param: 'customer_id', product_path: '', identity_path: '', usage_path: '', sync_mode: 'POLL', sync_interval_minutes: 60, developer_name: '', developer_email: '', validation: {},
};
const CHECKS = [['customer', 'Validate Customer API'], ['mapping', 'Test Field Mapping'], ['product', 'Validate Product API'], ['tally', 'Validate Tally API']];

/** Project API configuration (UI-driven, no per-project code). One component instance holds ALL the state so switching between the workspace tabs never loses
 *  unsaved edits: `view` = 'config' (endpoints / auth / sync settings), 'mapping' (field mapping + tests + activate + sync) or 'all'. Sections that are not in the
 *  current view are hidden, not unmounted. A developer sees only configuration + tests. */
export default function CustomerSyncPanel({ code, developer = false, skipDelivery = false, tallyRequired = false, view = 'all', project = null, onDirty, onGoMapping, onProjectSaved, onNext, onBack }) {
  const [sampleText, setSampleText] = useState(''); // pasted sample payload: parsed by the backend as data only, never stored
  const [showSample, setShowSample] = useState(false);
  const [detected, setDetected] = useState(null); // {fields:[{path,type}], suggested:{}, source}
  const [cfg, setCfg] = useState(DEFAULT);
  const [headersText, setHeadersText] = useState('{}');
  const [cp, setCp] = useState(null);
  const [history, setHistory] = useState([]);
  const [msg, setMsg] = useState('');
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState('');
  const [urls, setUrls] = useState({ frontend_url: project?.frontend_url || '', backend_url: project?.backend_url || '', framework: project?.framework || '' });
  const savedUrls = useRef({ frontend_url: project?.frontend_url || '', backend_url: project?.backend_url || '', framework: project?.framework || '' }); // what the server holds (never a stale prop)
  const [advOpen, setAdvOpen] = useState(false);
  const [conn, setConn] = useState(null);
  const [baseline, setBaseline] = useState('');
  const snap = JSON.stringify([cfg, headersText, urls]);
  const dirty = !!baseline && snap !== baseline;
  useEffect(() => { onDirty?.(dirty); }, [dirty]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!dirty) return undefined;
    const h = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);
  const show = (section) => (view === 'all' || view === section ? undefined : { display: 'none' });

  const load = useCallback(async () => {
    try {
      const c = await integrationsApi.apiConfig(code);
      const nc = c.config ? { ...DEFAULT, ...c.config } : DEFAULT; const nh = JSON.stringify(c.config?.request_headers || {}, null, 1);
      const nu = { ...savedUrls.current };
      setCfg(nc); setHeadersText(nh); setUrls(nu); setBaseline(JSON.stringify([nc, nh, nu]));
      setCp(c.checkpoint);
      if (!developer && !skipDelivery) setHistory((await integrationsApi.docEmails(code)).history);
    } catch (e) { setMsg(e.message); }
  }, [code, developer, skipDelivery]);
  useEffect(() => { load(); }, [load]);

  const act = (name, fn, ok) => async () => {
    setBusy(name); setMsg(''); setErrors({}); setResult(null);
    try {
      const r = await fn();
      setMsg(typeof ok === 'function' ? ok(r) : ok);
      await load();
      if (project && ['save', 'activate', 'conn', 'validate'].includes(name)) onProjectSaved?.();
    } catch (e) { setMsg(e.message); setErrors(e.errors || {}); }
    finally { setBusy(''); }
  };
  const up = (patch) => setCfg((c) => ({ ...c, ...patch }));
  const err = (k) => errors[k] && <span className={styles.err}>{[].concat(errors[k]).join(' ')}</span>;
  const save = () => {
    let headers = {};
    try { headers = JSON.parse(headersText || '{}'); } catch { throw new Error('Request headers must be valid JSON, e.g. {"Accept": "application/json"}.'); }
    return (async () => {
      if (project && (urls.frontend_url !== savedUrls.current.frontend_url || urls.backend_url !== savedUrls.current.backend_url || urls.framework.trim() !== savedUrls.current.framework.trim())) {
        const changed = { ...urls, framework: urls.framework.trim() };
        await integrationsApi.updateProject(code, changed);
        savedUrls.current = { ...urls };
      }
      return integrationsApi.saveApiConfig(code, { ...cfg, request_headers: headers });
    })();
  };
  const check = (kind) => act(kind, async () => { await save(); const r = await integrationsApi.validateApi(code, kind); setResult({ kind, ...r }); return r; }, (r) => (r.ok ? `✓ ${r.message}` : `✗ ${r.message}`));
  const v = cfg.validation || {};

  if (project) {
    const rowsOf = (x) => [].concat(x || []).filter((r) => r && typeof r === 'object');
    const preview = rowsOf(result?.mapped || cfg.__preview).slice(0, 5);
    const status = (k) => (k === 'conn' ? (conn ? (conn.ok ? 'Passed' : 'Failed') : 'Not Tested') : v[k] ? (v[k].ok ? 'Passed' : 'Failed') : 'Not Tested');
    const detail = (k) => (k === 'conn' ? (conn ? conn.detail : '') : v[k] ? (v[k].message || (v[k].at ? `Tested ${formatDateTime(v[k].at)}` : '')) : '');
    const push = cfg.sync_mode === 'PUSH';
    const required = ['customer', 'mapping', ...(tallyRequired ? ['tally'] : []), ...(cfg.product_path ? ['product'] : [])];
    const ready = required.every((k) => status(k) === 'Passed');
    const wc = v.customer?.checks; // webhook sub-results stored by the backend
    const sub = (flag, okText) => (!v.customer ? ['Not Tested', ''] : wc ? (wc[flag] ? ['Passed', okText] : ['Failed', v.customer.message]) : [status('customer'), detail('customer')]);
    const rows = push ? [
      ['conn', 'Backend Connected (optional in webhook mode)', status('conn'), detail('conn')],
      ['route', 'Inbound Route Available', ...sub('route', 'POST /integrations/v1/customers/register is registered on this Nexus server.')],
      ['creds', 'Project Credentials Configured', ...sub('credentials', `Active Client ID + API Secret for ${code}.`)],
      ['mapping', 'Sample Payload & Field Mapping Validated', status('mapping'), detail('mapping')],
      ['live', 'Real Authenticated Registration', !wc ? 'Not Tested' : wc.live_events ? 'Passed' : 'Awaiting', !wc ? '' : wc.live_events ? `${wc.live_events} received.` : 'Waiting for the external application to send its first registration (not required to continue).'],
      ...(tallyRequired ? [['tally', 'Tally Details', status('tally'), detail('tally')]] : []), ...(cfg.product_path ? [['product', 'Product Information API', status('product'), detail('product')]] : []),
    ] : [
      ['conn', 'Backend Connected', status('conn'), detail('conn')], ['customer', 'Customer API Verified', status('customer'), detail('customer')], ['mapping', 'Field Mapping Validated', status('mapping'), detail('mapping')],
      ...(tallyRequired ? [['tally', 'Tally Details', status('tally'), detail('tally')]] : []), ...(cfg.product_path ? [['product', 'Product Information API', status('product'), detail('product')]] : []),
    ];
    const runAll = act('validate', async () => {
      await save();
      const c = await integrationsApi.testConnection(code);
      setConn({ ok: !!c.project_backend_reachable, detail: c.project_backend_reachable ? `Reachable (${c.detail})` : `Not reachable: ${c.detail}` });
      let out = null;
      for (const k of ['customer', 'mapping', ...(tallyRequired ? ['tally'] : []), ...(cfg.product_path ? ['product'] : [])]) {
        const r = await integrationsApi.validateApi(code, k, k === 'mapping' && sampleText.trim() ? { sample: sampleText } : {});
        if (k === 'mapping') { out = r; if (r.needs_sample) setShowSample(true); }
      }
      if (out?.mapped) setResult({ kind: 'mapping', mapped: out.mapped });
      return out;
    }, (r) => (r?.ok ? 'Validation finished.' : `Validation finished - ${r?.message || 'see the failed checks.'}`));
    const detect = act('detect', async () => {
      const r = await integrationsApi.detectFields(code, sampleText.trim() || undefined);
      if (!r.ok) { if (r.needs_sample) setShowSample(true); throw new Error(r.message); }
      setDetected(r);
      const current = cfg.field_mapping || {};
      const conflicts = Object.entries(r.suggested).filter(([k, v]) => current[k] && current[k] !== v);
      const apply = conflicts.length === 0 || window.confirm(`Replace ${conflicts.length} saved mapping(s) with the detected suggestions?\n\n${conflicts.map(([k, v]) => `${k}: ${current[k]} → ${v}`).join('\n')}`);
      up({ field_mapping: apply ? { ...current, ...r.suggested } : { ...r.suggested, ...current } });
      return r;
    }, (r) => `${r.message} Review the suggested mapping, then Save.`);
    const maps = [['external_customer_id', 'External Customer ID'], ['customer_name', 'Customer Name'], ['email', 'Email'], ['contact_number', 'Contact Number']];
    const opt = [['company_name', 'Company Name'], ['address', 'Address'], ['gstin', 'GSTIN'], ...(tallyRequired ? TALLY_MAP : [])];
    const used = Object.values(cfg.field_mapping || {}).filter(Boolean);
    const field = (k, l) => {
      const val = cfg.field_mapping[k] || '';
      const dupe = val && used.filter((x) => x === val).length > 1;
      return (
        <label key={k} className={styles.field}><span className={styles.fieldLabel}>{l}{!push && maps.some(([m]) => m === k) && <b className={styles.req} aria-hidden="true">*</b>}</span>
          <input list={`paths-${code}`} value={val} placeholder={detected ? 'Select a detected field' : 'JSON path, e.g. customer.email'} aria-invalid={dupe || undefined}
            onChange={(e) => up({ field_mapping: { ...cfg.field_mapping, [k]: e.target.value } })} />
          {dupe && <span className={styles.err}>Mapped twice.</span>}
        </label>
      );
    };
    const saveNext = (name, okMsg) => act(name, async () => { const r = await save(); onNext?.(); return r; }, okMsg);
    const safeMsg = (m) => m && <p role="status" className={styles.note}>{m}</p>;
    return (
      <div className={styles.panel}>
        {view === 'config' ? (
          <>
            <div className={styles.setupTwo}>
              <label className={styles.field}><span className={styles.fieldLabel}>Backend API Base URL<b className={styles.req} aria-hidden="true">*</b></span><input type="url" value={urls.backend_url} onChange={(e) => setUrls({ ...urls, backend_url: e.target.value })} placeholder="https://api.example.com" aria-invalid={!!errors.backend_url} />{err('backend_url')}</label>
              <label className={styles.field}><span className={styles.fieldLabel}>{push ? 'Customer List API (not used in webhook mode)' : 'Customer Registration API'}{!push && <b className={styles.req} aria-hidden="true">*</b>}</span><input value={cfg.customer_path} disabled={push} onChange={(e) => up({ customer_path: e.target.value })} placeholder={push ? 'Not required - customers are pushed to Nexus' : '/api/customers'} />{err('customer_path')}</label>
              <ProtocolFields cfg={cfg} up={up} err={err} framework={urls.framework} setFramework={(v) => setUrls({ ...urls, framework: v })} />
              <label className={styles.field}><span className={styles.fieldLabel}>{push ? 'External API Authentication (optional in webhook mode)' : 'Authentication Method'}{!push && <b className={styles.req} aria-hidden="true">*</b>}</span><select value={cfg.auth_type} onChange={(e) => up({ auth_type: e.target.value })}><option value="NONE">None (public endpoint)</option><option value="BEARER">Bearer / JWT Token</option><option value="API_KEY_HEADER">API Key</option><option value="BASIC">Basic Authentication</option><option value="OAUTH2">OAuth 2.0</option><option value="CUSTOM">Other (Custom Authentication)</option></select>{err('auth_type')}</label>
              <label className={styles.field}>Customer Sync Mode<select value={cfg.sync_mode} onChange={(e) => up({ sync_mode: e.target.value })}><option value="POLL">Scheduled Polling</option><option value="PUSH">Authenticated Webhook</option></select>{err('sync_mode')}</label>
              {AUTH_FORMS.includes(cfg.auth_type) && <CustomAuthFields key={cfg.auth_type} mode={cfg.auth_type} cfg={cfg} up={up} err={err} />}
              {(cfg.auth_type === 'BEARER' || cfg.auth_type === 'API_KEY_HEADER') && <label className={styles.field}><span className={styles.fieldLabel}>Secure Credential Reference{!push && <b className={styles.req} aria-hidden="true">*</b>}</span><input value={cfg.credential_ref} onChange={(e) => up({ credential_ref: e.target.value })} placeholder="SERVER_ENV_VARIABLE_NAME" />{err('credential_ref')}</label>}
              {cfg.auth_type === 'API_KEY_HEADER' && <label className={styles.field}><span className={styles.fieldLabel}>API Key Header Name<b className={styles.req} aria-hidden="true">*</b></span><input value={cfg.auth_header} onChange={(e) => up({ auth_header: e.target.value })} placeholder="X-API-Key" />{err('auth_header')}</label>}
              {tallyRequired && <label className={`${styles.field} ${styles.wide}`}><span className={styles.fieldLabel}>Tally License Details API<b className={styles.req} aria-hidden="true">*</b></span><input value={cfg.tally_path} onChange={(e) => up({ tally_path: e.target.value })} placeholder="/api/customers/tally" />{err('tally_path')}</label>}
            </div>
            <details className={styles.adv} open={advOpen} onToggle={(e) => setAdvOpen(e.currentTarget.open)}>
              <summary>Advanced Settings</summary>
              <div className={styles.setupTwo}>
                <label className={styles.field}>Customer API HTTP Method<select value={cfg.customer_method} onChange={(e) => up({ customer_method: e.target.value })}><option>GET</option><option>POST</option></select></label>
                {cfg.sync_mode === 'POLL' && <label className={styles.field}>Sync Interval (minutes)<input type="number" min="5" max="1440" value={cfg.sync_interval_minutes} onChange={(e) => up({ sync_interval_minutes: e.target.value })} />{err('pagination')}</label>}
                <label className={styles.field}>Product Information API<input value={cfg.product_path} onChange={(e) => up({ product_path: e.target.value })} placeholder="/product" />{err('product_path')}</label>
                <label className={styles.field}>Customer Identity API<input value={cfg.identity_path} onChange={(e) => up({ identity_path: e.target.value })} placeholder="/customers/identity" />{err('identity_path')}</label>
                <label className={styles.field}>Usage / Activity API<input value={cfg.usage_path} onChange={(e) => up({ usage_path: e.target.value })} placeholder="/usage" />{err('usage_path')}</label>
                <label className={styles.field}>Frontend URL (optional)<input type="url" value={urls.frontend_url} onChange={(e) => setUrls({ ...urls, frontend_url: e.target.value })} placeholder="https://app.example.com" /></label>
                {!push && <label className={styles.field}>Pagination<select value={cfg.pagination.type} onChange={(e) => up({ pagination: { ...cfg.pagination, type: e.target.value } })}><option value="page">Page number</option><option value="offset">Offset</option><option value="none">None</option></select></label>}
                {!push && <label className={styles.field}>Page Size<input type="number" min="1" max="1000" value={cfg.pagination.page_size} onChange={(e) => up({ pagination: { ...cfg.pagination, page_size: e.target.value } })} /></label>}
                {!push && <label className={styles.field}>Results List Path<input value={cfg.pagination.results_path} onChange={(e) => up({ pagination: { ...cfg.pagination, results_path: e.target.value } })} placeholder="results (blank = top-level list)" /></label>}
                <label className={`${styles.field} ${styles.wide}`}>Optional Headers (JSON, no secrets)<textarea rows={2} value={headersText} onChange={(e) => setHeadersText(e.target.value)} style={{ fontFamily: 'monospace' }} /></label>
              </div>
            </details>
            {push && <p className={styles.note}><b>Authenticated Webhook:</b> the external application sends each new customer to Nexus with <span className={styles.mono}>POST {'{NEXUS_API_BASE}'}customers/register</span> and the headers <span className={styles.mono}>X-Client-Id</span> / <span className={styles.mono}>X-Client-Secret</span> (this project&apos;s Nexus credentials, shown in the header above). Nexus does not call any external Customer List API in this mode, so the external path, pagination and interval are not used.</p>}
            <div className={styles.stepFooter}>
              <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={act('save', save, 'Draft saved. Validation is still pending.')}>{busy === 'save' ? 'Saving…' : 'Save Draft'}</button>
              <span className={styles.stepHint}>Next: validate the API and map the customer fields.</span>
              <span className={styles.actions}>
                <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={act('conn', async () => { await save(); return integrationsApi.testConnection(code); }, (r) => (r.project_backend_reachable ? `✓ Backend reachable (${r.detail}).` : `✗ Backend not reachable: ${r.detail}.${push ? ' (Not required for webhook mode.)' : ''}`))}>{busy === 'conn' ? 'Testing…' : 'Test Connection'}</button>
                {onNext && <button className={styles.btn} disabled={!!busy} onClick={saveNext('savenext', 'Configuration saved.')}>{busy === 'savenext' ? 'Saving…' : 'Save & Next →'}</button>}
                {!onNext && <button className={styles.btn} disabled={!!busy} onClick={act('save', save, 'Configuration saved.')}>Save Configuration</button>}
              </span>
            </div>
            {safeMsg(msg)}
          </>
        ) : (
          <>
            <div className={styles.head}><h3 className={styles.title}>API Validation</h3><button className={styles.btn} disabled={!!busy} onClick={runAll}>{busy === 'validate' ? 'Validating…' : 'Run Validation'}</button></div>
            <div className={styles.tableWrap}>
              <table className={styles.table}><thead><tr><th>Check</th><th>Status</th><th>Details</th></tr></thead>
                <tbody>{rows.map(([k, l, st, d]) => <tr key={k}><td>{l}</td><td><span className={`${styles.badge} ${st === 'Passed' ? styles.ok : st === 'Failed' ? styles.off : styles.pending}`}>{st}</span></td><td>{d || '—'}</td></tr>)}</tbody></table>
            </div>
            {safeMsg(msg)}
            <div className={styles.head} style={{ marginTop: 12 }}>
              <h3 className={styles.title}>Customer Field Mapping</h3>
              <span className={styles.actions}>
                <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={() => setShowSample((x) => !x)}>{showSample ? 'Hide Sample JSON' : 'Paste Sample JSON'}</button>
                <button type="button" className={styles.btn} disabled={!!busy} onClick={detect}>{busy === 'detect' ? 'Detecting…' : 'Auto Detect Fields'}</button>
              </span>
            </div>
            {showSample && (
              <div className={styles.sampleBox}>
                <label className={styles.fieldLabel} htmlFor={`sample-${code}`}>{push ? 'No customer payload is captured in webhook mode - paste one sample registration JSON (it is only read, never stored or imported).' : 'Optional: paste a sample customer JSON instead of reading the customer API.'}</label>
                <textarea id={`sample-${code}`} spellCheck={false} value={sampleText} onChange={(e) => setSampleText(e.target.value)} placeholder={'{"customer_id": "C101", "full_name": "Kumar", "email_address": "k@example.com", "mobile": "9876543210"}'} />
              </div>
            )}
            <datalist id={`paths-${code}`}>{(detected?.fields || []).map((f) => <option key={f.path} value={f.path}>{f.type}</option>)}</datalist>
            {push && <p className={styles.note}>Webhook mode: the external backend must send the Nexus field names <span className={styles.mono}>external_customer_id, customer_name, email, contact_number</span>. Run Validation checks your pasted sample against exactly that contract; the mapping below is used only for polling.</p>}
            <div className={styles.mapRow}>{maps.map(([k, l]) => field(k, l))}</div>
            <details className={styles.adv} style={{ marginTop: 10 }}><summary>Optional fields{tallyRequired ? ' and Tally fields' : ''}</summary><div className={styles.mapGrid}>{opt.map(([k, l]) => field(k, l))}</div></details>
            {detected && (
              <div className={styles.summaryStrip} aria-label="Mapping preview">
                {[...maps, ...opt].filter(([k]) => cfg.field_mapping[k]).map(([k, l]) => <span key={k}>{cfg.field_mapping[k]} → <b>{l}</b></span>)}
              </div>
            )}
            {preview.length > 0 && (
              <>
                <h3 className={styles.title} style={{ marginTop: 16 }}>Customer Preview</h3>
                <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Customer ID</th><th>Name</th><th>Email</th><th>Contact</th></tr></thead>
                  <tbody>{preview.map((r, i) => <tr key={i}><td>{String(r.external_customer_id ?? '—')}</td><td>{String(r.customer_name ?? '—')}</td><td>{String(r.email ?? '—')}</td><td>{String(r.contact_number ?? '—')}</td></tr>)}</tbody></table></div>
              </>
            )}
            <div className={styles.stepFooter}>
              {onBack ? <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={onBack}>← Back</button> : <span />}
              <span className={styles.stepHint}>{ready ? (push ? 'Sample validated. Live delivery is verified when the first real registration arrives.' : 'All checks passed.') : 'Run Validation; every check except Backend Connected must pass.'}</span>
              <span className={styles.actions}>
                <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={act('save', save, 'Mapping saved.')}>Save Mapping</button>
                <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy || !ready || project.status === 'ARCHIVED'} title={ready ? '' : 'All validation checks must pass first.'} onClick={act('activate', () => integrationsApi.activateIntegration(code), 'Integration activated.')}>{busy === 'activate' ? 'Activating…' : 'Activate Integration'}</button>
                {onNext && <button className={styles.btn} disabled={!!busy || !ready} title={ready ? '' : 'Run Validation first; the failed check explains what to fix.'} onClick={saveNext('savenext', 'Mapping saved.')}>{busy === 'savenext' ? 'Saving…' : 'Save & Next →'}</button>}
              </span>
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className={styles.panel}>
      <h3 className={styles.title}>{view === 'mapping' ? 'Test & Field Mapping' : view === 'config' ? 'API Configuration' : 'API Configuration & Automatic Synchronisation'}</h3>
      <p className={styles.note}>Nexus calls the project&apos;s own backend, maps each record into the project&apos;s Customers list (nothing is issued before approval) and repeats on the interval. Credentials are stored only as a reference to a server environment variable.</p>

      <div className={styles.setupTwo} style={show('config')}>
        <label className={styles.field}><span className={styles.fieldLabel}>Backend API Base URL<b className={styles.req} aria-hidden="true">*</b></span><input type="url" disabled={!project} value={urls.backend_url} onChange={(e) => setUrls({ ...urls, backend_url: e.target.value })} placeholder="https://api.example.com" /></label>
        <label className={styles.field}><span className={styles.fieldLabel}>Customer Registration API<b className={styles.req} aria-hidden="true">*</b></span><input value={cfg.customer_path} onChange={(e) => up({ customer_path: e.target.value })} placeholder="/api/customers" />{err('customer_path')}</label>
        <label className={styles.field}>Frontend URL<input type="url" disabled={!project} value={urls.frontend_url} onChange={(e) => setUrls({ ...urls, frontend_url: e.target.value })} placeholder="https://app.example.com (optional for backend-only)" /></label>
        <label className={styles.field}><span className={styles.fieldLabel}>Authentication Method<b className={styles.req} aria-hidden="true">*</b></span><select value={cfg.auth_type} onChange={(e) => up({ auth_type: e.target.value })}><option value="NONE">None</option><option value="BEARER">Bearer / JWT token</option><option value="API_KEY_HEADER">API key header</option><option value="BASIC">Basic Authentication</option><option value="OAUTH2">OAuth 2.0</option><option value="CUSTOM">Other (Custom Authentication)</option></select>{err('auth_type')}</label>
        <label className={styles.field}><span className={styles.fieldLabel}>Tally License Details API{tallyRequired && <b className={styles.req} aria-hidden="true">*</b>}</span><input value={tallyRequired ? cfg.tally_path : ''} disabled={!tallyRequired} onChange={(e) => up({ tally_path: e.target.value })} placeholder={tallyRequired ? '/api/customers/tally' : 'Not required for Non-Tally projects'} />{err('tally_path')}</label>
        <label className={styles.field}>Customer Sync Mode<select value={cfg.sync_mode} onChange={(e) => up({ sync_mode: e.target.value })}><option value="POLL">Scheduled polling</option><option value="PUSH">Authenticated webhook</option></select>{err('sync_mode')}</label>
        {project && <ProtocolFields cfg={cfg} up={up} err={err} framework={urls.framework} setFramework={(v) => setUrls({ ...urls, framework: v })} />}
        {AUTH_FORMS.includes(cfg.auth_type) && view !== 'mapping' && <CustomAuthFields key={cfg.auth_type} mode={cfg.auth_type} cfg={cfg} up={up} err={err} />}
      </div>
      {view === 'config' && (
        <details className={styles.adv} open={advOpen} onToggle={(e) => setAdvOpen(e.currentTarget.open)}>
          <summary>Advanced API Configuration</summary>
          <div className={styles.setupTwo}>
            <label className={styles.field}>Secure Credential Reference (server env variable)<input value={cfg.credential_ref} disabled={cfg.auth_type === 'NONE' || AUTH_FORMS.includes(cfg.auth_type)} onChange={(e) => up({ credential_ref: e.target.value })} placeholder={cfg.auth_type === 'NONE' ? 'Not needed' : AUTH_FORMS.includes(cfg.auth_type) ? 'Set in the authentication section' : 'ENV_VARIABLE_NAME'} />{err('credential_ref')}</label>
            <label className={styles.field}>API key header name<input value={cfg.auth_header} disabled={cfg.auth_type !== 'API_KEY_HEADER'} onChange={(e) => up({ auth_header: e.target.value })} placeholder="X-API-Key" /></label>
            <label className={styles.field}>Customer API HTTP Method<select value={cfg.customer_method} onChange={(e) => up({ customer_method: e.target.value })}><option>GET</option><option>POST</option></select>{err('customer_method')}</label>
            <label className={styles.field}>Sync Interval (minutes)<input type="number" min="5" max="1440" disabled={cfg.sync_mode !== 'POLL'} value={cfg.sync_interval_minutes} onChange={(e) => up({ sync_interval_minutes: e.target.value })} />{err('pagination')}</label>
            <label className={styles.field}>Product Information API<input value={cfg.product_path} onChange={(e) => up({ product_path: e.target.value })} placeholder="/product" />{err('product_path')}</label>
            <label className={styles.field}>Customer Identity API<input value={cfg.identity_path} onChange={(e) => up({ identity_path: e.target.value })} placeholder="/customers/identity" />{err('identity_path')}</label>
            <label className={styles.field}>Usage / Activity API<input value={cfg.usage_path} onChange={(e) => up({ usage_path: e.target.value })} placeholder="/usage" />{err('usage_path')}</label>
            <div className={styles.field}>Field Mapping<span className={styles.summary}><span>{Object.values(cfg.field_mapping || {}).filter(Boolean).length} fields mapped</span><button type="button" className={styles.linkBtn} onClick={() => onGoMapping?.()}>Edit in Test &amp; Mapping</button></span>{err('field_mapping')}</div>
            <label className={styles.field}>Pagination type<select value={cfg.pagination.type} onChange={(e) => up({ pagination: { ...cfg.pagination, type: e.target.value } })}><option value="page">Page number</option><option value="offset">Offset</option><option value="none">None</option></select></label>
            <label className={styles.field}>Page size<input type="number" min="1" max="1000" value={cfg.pagination.page_size} onChange={(e) => up({ pagination: { ...cfg.pagination, page_size: e.target.value } })} /></label>
            <label className={styles.field}>Results list path<input value={cfg.pagination.results_path} onChange={(e) => up({ pagination: { ...cfg.pagination, results_path: e.target.value } })} placeholder="results (blank = top-level list)" /></label>
            <label className={styles.field}>Developer name<input value={cfg.developer_name} onChange={(e) => up({ developer_name: e.target.value })} /></label>
            <label className={`${styles.field} ${styles.wide}`}>Request headers (JSON, no secrets)<textarea rows={2} value={headersText} onChange={(e) => setHeadersText(e.target.value)} style={{ fontFamily: 'monospace' }} /></label>
          </div>
        </details>
      )}

      <div style={show('mapping')}>
        <div className={styles.grid}>
          {MAP.map(([k, l]) => <label key={k} className={styles.field}>{l}<input value={cfg.field_mapping[k] || ''} onChange={(e) => up({ field_mapping: { ...cfg.field_mapping, [k]: e.target.value } })} /></label>)}
          {TALLY_MAP.map(([k, l]) => <label key={k} className={styles.field}>{l}<input value={cfg.field_mapping[k] || ''} onChange={(e) => up({ field_mapping: { ...cfg.field_mapping, [k]: e.target.value } })} /></label>)}
        </div>
        <label className={styles.field} style={{ marginTop: 8 }}>Required request headers (JSON, no secrets)<textarea rows={2} value={headersText} onChange={(e) => setHeadersText(e.target.value)} style={{ fontFamily: 'monospace' }} /></label>
        {err('field_mapping')}
      </div>

      <div className={styles.actions} style={{ marginTop: 12, justifyContent: 'flex-end' }}>
        <button className={styles.btn} disabled={!!busy} onClick={act('save', save, 'Configuration saved.')}>{busy === 'save' ? 'Saving…' : 'Save Configuration'}</button>
        {<button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={act('conn', async () => { await save(); return integrationsApi.testConnection(code); }, (r) => (r.project_backend_reachable ? `✓ Backend reachable (${r.detail}).` : `✗ Backend not reachable: ${r.detail}.`))}>{busy === 'conn' ? 'Testing…' : 'Test Connection'}</button>}
        {view === 'config' && !developer && <button className={styles.btn} disabled={!!busy || !(v.customer && v.customer.ok && v.mapping && v.mapping.ok && (!tallyRequired || (v.tally && v.tally.ok)))} title={'Enabled once the Customer API, Field Mapping' + (tallyRequired ? ' and Tally API' : '') + ' checks have passed (Test & Field Mapping tab).'} onClick={act('activate', () => integrationsApi.activateIntegration(code), 'Integration activated.')}>{busy === 'activate' ? 'Activating…' : 'Activate Integration'}</button>}
        {view !== 'config' && (
          <>
            {CHECKS.filter(([k]) => k !== 'tally' || tallyRequired || cfg.tally_path).map(([k, l]) => (
              <button key={k} className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={check(k)}>{busy === k ? 'Checking…' : l}{v[k] && <span style={{ marginLeft: 6 }}>{v[k].ok ? '✓' : '✗'}</span>}</button>
            ))}
            <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={check('preview')}>Preview API Response</button>
            {!developer && <button className={styles.btn} disabled={!!busy} onClick={act('activate', () => integrationsApi.activateIntegration(code), 'Integration activated.')}>Activate Integration</button>}
            {!developer && <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={act('sync', () => integrationsApi.syncCustomers(code), (r) => `Sync finished: ${r.result.created} new registrations, ${r.result.existing} already known, ${r.result.pending_tally} pending Tally verification, ${r.result.failed} failed.`)}>{busy === 'sync' ? 'Syncing…' : 'Sync Now'}</button>}
          </>
        )}
        {view === 'all' && !developer && <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy || !cfg.developer_email} onClick={act('docs', () => integrationsApi.sendDocs(code, cfg.developer_email), (r) => `Documentation email: ${r.status}.`)}>Send API Documentation</button>}
      </div>
      {msg && <p role="status" className={styles.note}>{msg}</p>}
      {result && (result.mapped || result.raw_sample) && <pre className={styles.code}>{JSON.stringify(result.mapped || result.raw_sample, null, 2)}</pre>}
      {cp && <p className={styles.note}>Status: <b>{cp.status}</b> · Last successful sync: <b>{cp.last_success_at ? formatDateTime(cp.last_success_at) : 'never'}</b> · Next automatic run: <b>{cp.next_run_at ? formatDateTime(cp.next_run_at) : 'next scheduler pass'}</b> · {cp.fetched_total} records fetched{cp.consecutive_failures > 0 && <> · {cp.consecutive_failures} consecutive failure(s), retrying with backoff</>}{cp.last_error && <> · <span className={styles.err}>{cp.last_error}</span></>}</p>}
      {view === 'all' && history.length > 0 && <details><summary>Documentation e-mail history ({history.length})</summary><ul>{history.map((h, i) => <li key={i}>{formatDateTime(h.at)} — {h.to} — {h.status} (by {h.sent_by})</li>)}</ul></details>}
    </div>
  );
}
