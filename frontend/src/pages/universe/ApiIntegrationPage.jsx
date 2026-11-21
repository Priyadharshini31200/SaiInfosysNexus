import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { integrationsApi } from '../../api/integrations';
import { useAuth } from '../../context/AuthContext';
import { formatDateTime } from '../../utils/date';
import CopyValue from '../../components/CopyValue';
import TopToast, { notify } from '../../components/TopToast';
import CustomerSyncPanel from '../integrations/CustomerSyncPanel';
import { TallyPanel } from '../integrations/TallyUsagePanels';
import { useProjectSecret } from '../integrations/useProjectSecret';
import styles from '../integrations/Integrations.module.css';

const STATUS = { ARCHIVED: 'Archived', ACTIVE: 'Active', DISABLED: 'Disabled', SETUP_PENDING: 'Setup Pending' };
const label = (x) => `${x.code} – ${x.name} – ${STATUS[x.status] || 'Setup Pending'}`;

/** UNIVERSE → API Integration: ONE workspace for every project - selected-project row, compact credentials, four tabs. Project + tab live in the URL. */
export default function ApiIntegrationPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const code = (params.get('project') || '').trim().toUpperCase();
  const { user } = useAuth();
  const isSuper = user?.role === 'SUPERADMIN';
  const { secret, clear: clearSecret, consume: consumeSecret, rotate: revealRotation } = useProjectSecret(code, true);
  const requestedTab = params.get('tab') || 'config';
  const tab = !isSuper && requestedTab === 'docs' ? 'config' : requestedTab;
  const [projects, setProjects] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [tick, setTick] = useState(0);
  const [reload, setReload] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [changing, setChanging] = useState(false);
  const [steps, setSteps] = useState(null); // {cfg, docs} - real saved state for the step badges
  useEffect(() => {
    if (!code) { setSteps(null); return; }
    Promise.all([integrationsApi.apiConfig(code).catch(() => null), isSuper ? integrationsApi.docEmails(code).catch(() => null) : null])
      .then(([c, d]) => setSteps({ cfg: c?.config || null, docs: d?.history || [] }));
  }, [code, tick, tab, isSuper]);
  const [text, setText] = useState('');

  useEffect(() => { setProjects(null); setLoadError(''); integrationsApi.projects().then((d) => setProjects((d.projects || []).filter((p) => p.license_policy === 'CALENDAR' && !p.manual && p.status !== 'ARCHIVED')), (e) => { setProjects([]); setLoadError(e.message); }); }, [reload]);
  useEffect(() => {
    if (!code) { setData(null); return undefined; }
    let current = true;
    setError('');
    integrationsApi.project(code).then((d) => {
      if (!current) return;
      if (!isSuper && !d.management?.configure) {
        setData(null);
        setError('You do not have permission to configure this project.');
        return;
      }
      setData(d);
    }, (e) => current && (setData(null), setError(e.status === 404 ? 'Project not found.' : e.message)));
    return () => { current = false; };
  }, [code, tick, isSuper]);
  useEffect(() => { if (location.state?.created) notify('Project created successfully. Continue with API setup.'); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const p = data?.project?.code === code ? data.project : null;
  const set = (patch) => { const n = new URLSearchParams(params); Object.entries(patch).forEach(([k, v]) => (v ? n.set(k, v) : n.delete(k))); setParams(n); };
  const pick = (value) => {
    setText(value);
    const m = (projects || []).find((x) => label(x) === value || x.code === value.trim().toUpperCase());
    if (m && m.code !== code) {
      if (dirty && !window.confirm('You have unsaved API Setup changes. Discard them and switch project?')) return;
      clearSecret(); setChanging(false); setText(''); set({ project: m.code, tab: '' });
    }
  };
  const list = useMemo(() => (projects || []).slice(0, 300), [projects]);
  // One-time credentials: kept ONLY in this component's memory (never URL, storage or logs). Step changes do not clear it;
  // leaving the page or closing the tab does - a lost secret needs another authorised rotation.
  const [issued, setIssued] = useState(null); // { code, client_id, secret }
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [rotating, setRotating] = useState(false);
  useEffect(() => { // secret handed over by "Create Project" (same one-time slot)
    if (secret && p && !issued) { setIssued({ code: p.code, client_id: p.client_id, secret }); consumeSecret(); }
  }, [secret, p, issued]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const wipe = () => setIssued(null);
    window.addEventListener('pagehide', wipe);
    return () => window.removeEventListener('pagehide', wipe);
  }, []);
  const rotate = async () => {
    if (rotating) return;
    setRotating(true);
    try {
      const r = await integrationsApi.rotate(code); // explicit action only - never on load or refresh
      setConfirmRotate(false);
      setIssued({ code, client_id: r.credentials.client_id, secret: r.credentials.api_secret });
      setTick((n) => n + 1); // header now shows the new Client ID
    } catch (e) { notify(e.message, 'error'); } finally { setRotating(false); }
  };
  void revealRotation; void clearSecret;

  return (
    <div className={styles.page}>
      <TopToast />
      <div className={styles.panel}>
        {loadError && <p role="alert" className={styles.err}>Could not load projects: {loadError} <button className={styles.linkBtn} onClick={() => setReload((n) => n + 1)}>Retry</button></p>}
        {projects && !loadError && projects.length === 0 && <p className={styles.note}>No UNIVERSE projects yet. <Link className={styles.btn} to="/directory/tally">Go to Project Directory</Link></p>}
        {(!p || changing) && (
          <label className={styles.field} style={{ maxWidth: 560, marginTop: 8 }}>Select Project
            <input list="universe-projects" value={text} onChange={(e) => pick(e.target.value)} placeholder="Search by project code or name…" disabled={!projects || !!loadError} aria-label="Select Project" autoComplete="off" />
            <datalist id="universe-projects">{list.map((x) => <option key={x.code} value={label(x)} />)}</datalist>
          </label>
        )}
        {p && !changing && (
          <div className={styles.infoRow}>
            <div className={styles.infoCol}><small>Project</small>
              <div className={styles.projLine}><b title={p.name}>{p.name}</b><span className={`${styles.badge} ${p.status === 'ACTIVE' ? styles.ok : styles.pending}`}>{STATUS[p.status] || 'Setup Pending'}</span>
                <button type="button" className={styles.swapBtn} data-tip="Change Project" aria-label="Change Project" onClick={() => { clearSecret(); setChanging(true); }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M17 3l4 4-4 4" /><path d="M3 7h18" /><path d="M7 21l-4-4 4-4" /><path d="M21 17H3" /></svg>
                </button></div></div>
            <div className={styles.infoCol}><small>Project Code</small><CopyValue value={p.code} label="Project code" /></div>
            <div className={styles.infoCol}><small>Client ID (current)</small><CopyValue value={p.client_id} label="Client ID" /></div>
            <div className={styles.infoCol}><small>API Secret</small>
              <span className={styles.secretMask}>{p.secret_hint ? `••••••••${p.secret_hint.replace('…', '')}` : '••••••••'}{isSuper && <button type="button" className={styles.linkBtn} onClick={() => setConfirmRotate(true)}>Rotate Secret</button>}</span>
            </div>
          </div>
        )}
        {error && <p role="alert" className={styles.err}>{error}</p>}
      </div>
      {confirmRotate && p && (
        <Dialog title="Rotate API credentials?" onClose={rotating ? null : () => setConfirmRotate(false)}>
          <p className={styles.note} style={{ margin: 0 }}>For <b>{p.name} ({p.code})</b> Nexus will issue a <b>new Client ID and a new API Secret</b>. The current pair (Client ID <span className={styles.mono}>{p.client_id}</span>) <b>stops working immediately</b>, so the external application must be updated with the new pair.</p>
          <div className={styles.stepFooter}>
            <button type="button" className={`${styles.btn} ${styles.ghost}`} disabled={rotating} onClick={() => setConfirmRotate(false)}>Cancel</button><span />
            <button type="button" className={styles.btn} disabled={rotating} onClick={rotate}>{rotating ? 'Rotating…' : 'Rotate Credentials'}</button>
          </div>
        </Dialog>
      )}
      {issued && <CredentialsModal issued={issued} onDone={() => setIssued(null)} />}

      {p && (
        <>
          <Stepper tab={tab} isSuper={isSuper} project={p} steps={steps} onGo={(k) => set({ tab: k })} />
          {/* one mounted instance for setup + mapping so unsaved edits survive switching tabs */}
          <div style={tab === 'config' || tab === 'mapping' ? undefined : { display: 'none' }}>
            <CustomerSyncPanel key={p.code} skipDelivery={!isSuper} code={p.code} project={p} tallyRequired={!!p.tally_serial_required} view={tab === 'mapping' ? 'mapping' : 'config'} onDirty={setDirty} onGoMapping={() => set({ tab: 'mapping' })} onProjectSaved={() => setTick((n) => n + 1)}
              onNext={() => { setTick((n) => n + 1); set({ tab: tab === 'config' ? 'mapping' : isSuper ? 'docs' : 'logs' }); }} onBack={() => set({ tab: 'config' })} />
            {isSuper && tab === 'mapping' && p.tally_serial_required && <TallyPanel code={p.code} project={p} onChanged={() => setTick((n) => n + 1)} />}
          </div>
          {tab === 'docs' && <DocsTab key={p.code} code={p.code} data={data} onBack={() => set({ tab: 'mapping' })} onNext={() => { setTick((n) => n + 1); set({ tab: 'logs' }); }} />}
          {tab === 'logs' && <LogsTab key={p.code} code={p.code} allowSync={isSuper && p.enabled} apiBase={data.api_base} onBack={() => set({ tab: isSuper ? 'docs' : 'mapping' })} />}
        </>
      )}
    </div>
  );
}

function DocsTab({ code, data, onBack, onNext }) {
  const devBase = /localhost|127\.0\.0\.1|:5173|:5174|:5175/.test(data.api_base || '');
  const [email, setEmail] = useState('');
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const loadHistory = useCallback(() => integrationsApi.docEmails(code).then((h) => setHistory(h.history), () => {}), [code]);
  useEffect(() => {
    integrationsApi.apiConfig(code).then((c) => setEmail((cur) => cur || c.config?.developer_email || ''), () => {});
    loadHistory();
  }, [code, loadHistory]);
  const [confirmSend, setConfirmSend] = useState(false);
  const [withSecret, setWithSecret] = useState(true);
  const [handoff, setHandoff] = useState(null); // last credential-link state from the audit trail (opened or not)
  useEffect(() => {
    integrationsApi.audit(code).then((d) => {
      const rows = d.audit || [];
      const sent = rows.find((a) => a.action === 'docs.emailed' && /credential link/.test(a.detail || ''));
      const opened = rows.find((a) => a.action === 'credentials.handoff_redeemed');
      setHandoff(sent ? { sentAt: sent.at, openedAt: opened && opened.at >= sent.at ? opened.at : null } : null);
    }, () => {});
  }, [code, history]);
  const ask = () => {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { notify('Enter a valid developer email address.', 'error'); return; }
    setConfirmSend(true);
  };
  const send = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await integrationsApi.sendDocs(code, email.trim(), withSecret ? { provision_secret: true, confirm: true } : {});
      setConfirmSend(false);
      if (r.status === 'accepted') notify(`Email accepted for ${r.to} (${r.environment})${r.credential_link === 'sent' ? '. The credential handoff completes when the developer opens the link.' : '.'}`);
      else if (r.status === 'not_delivered') notify('Prepared, but this server uses a development mail backend: nothing was delivered.', 'error');
      else notify('Unable to send documentation. Check email settings and retry.', 'error');
    } catch (e) { notify(e.message || 'Unable to send documentation. Check email settings and retry.', 'error'); } finally { setBusy(false); loadHistory(); }
  };
  const last = history[0];
  return (
    <div className={styles.panel}>
      <div className={styles.docRow}>
        <div className={styles.field}>Documentation Version<CopyValue value={`v${data.api_version}`} label="API version" /></div>
        <label className={styles.field}>Developer Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="developer@company.com" /></label>
        <div className={styles.field}>Last Sent Status<span className={styles.summary}>{last ? last.status : 'Not Sent'}</span></div>
        <div className={styles.docActions}>
          <button className={`${styles.btn} ${styles.ghost}`} onClick={() => integrationsApi.downloadDocs(code, 'markdown')}>Download Guide</button>
          <button className={styles.btn} disabled={busy || !email} onClick={ask}>{busy ? 'Sending…' : 'Send API Documentation'}</button>
        </div>
      </div>
      {confirmSend && (
        <Dialog title="Send API documentation?" onClose={busy ? null : () => setConfirmSend(false)}>
          <p className={styles.note} style={{ marginTop: 0 }}>Project <b>{data.project.name} ({code})</b> → <b>{email.trim()}</b><br />Client ID <span className={styles.mono}>{data.project.client_id}</span> · API base <span className={styles.mono}>{data.api_base}</span>{devBase && <> · <b>LOCAL DEVELOPMENT</b></>}</p>
          <label className={styles.ackRow}><input type="checkbox" checked={withSecret} onChange={(e) => setWithSecret(e.target.checked)} /> Include a one-time API Secret link (24 h, single use). When the developer opens it, Nexus issues a <b>new secret</b> for this Client ID and the current secret stops working.</label>
          <div className={styles.stepFooter}><button type="button" className={`${styles.btn} ${styles.ghost}`} disabled={busy} onClick={() => setConfirmSend(false)}>Cancel</button><span />
            <button type="button" className={styles.btn} disabled={busy} onClick={send}>{busy ? 'Sending…' : withSecret ? 'Confirm & Send with Credential Link' : 'Send Documentation Only'}</button></div>
        </Dialog>
      )}
      {handoff && <p className={styles.note} role="status">Credential link: sent {formatDateTime(handoff.sentAt)} · <b>{handoff.openedAt ? `Handoff completed ${formatDateTime(handoff.openedAt)} (developer received the API Secret)` : 'Not opened yet - email acceptance does not mean the developer has the secret'}</b></p>}
      <p className={devBase ? styles.logErr : styles.note} role="status">Nexus API base in this guide: <span className={styles.mono}>{data.api_base}</span>{devBase && <> - <b>DEVELOPMENT address.</b> Do not send this guide as production documentation; set NEXUS_PUBLIC_API_BASE on the server for the real base URL.</>}</p>
      <h3 className={styles.title} style={{ marginTop: 12 }}>Delivery History</h3>
      <div className={styles.scrollTable} style={{ marginTop: 6 }}>
        <table className={styles.table}><thead><tr><th>Date &amp; Time</th><th>Recipient</th><th>Status</th><th>Details</th></tr></thead>
          <tbody>{history.map((h, i) => <tr key={i}><td>{formatDateTime(h.at)}</td><td>{h.to}</td><td>{h.status}</td><td>{h.sent_by ? `Sent by ${h.sent_by}` : '—'}{i === 0 && <> <button className={styles.linkBtn} disabled={busy} onClick={ask}>Resend</button></>}</td></tr>)}
            {history.length === 0 && <tr><td colSpan={4}>No documentation has been sent yet.</td></tr>}</tbody></table>
      </div>
      <StepFooter onBack={onBack} next={<button type="button" className={styles.btn} onClick={onNext}>Next: Sync Logs</button>} hint="Sending is optional for local testing; the guide can also be downloaded and handed over." />
    </div>
  );
}

const friendly = (m = '') => (/url|scheme|host|resolve|unsafe|connect/i.test(m) ? 'Invalid Backend API URL. Open API Setup to correct it.' : /401|403|auth|credential/i.test(m) ? 'API authentication failed. Check the configured credentials.' : /tally/i.test(m) ? 'Tally verification requires a valid Tally API or connector.' : m);

function LogsTab({ code, allowSync, apiBase, onBack }) {
  const [cfg, setCfg] = useState(null);
  const [audit, setAudit] = useState([]);
  const [busy, setBusy] = useState(false);
  const [project, setProject] = useState(null);
  const load = useCallback(() => {
    integrationsApi.apiConfig(code).then(setCfg, () => {});
    integrationsApi.audit(code).then((d) => setAudit(d.audit || []), () => {});
    integrationsApi.project(code).then((d) => setProject(d.project), () => {});
  }, [code]);
  useEffect(() => { load(); }, [load]);
  const sync = async () => {
    setBusy(true);
    try { const r = await integrationsApi.syncCustomers(code); notify(`Sync finished: ${r.result.created} new, ${r.result.existing} existing, ${r.result.failed} failed.`); } catch (e) { notify(friendly(e.message), 'error'); } finally { setBusy(false); load(); }
  };
  const cp = cfg?.checkpoint;
  const push = cfg?.config?.sync_mode === 'PUSH';
  const regs = audit.filter((a) => /^(registration\.received|registration\.rejected|customer\.registered)$/.test(a.action || '')); // inbound events only, not staff approvals
  const lastReg = regs.find((a) => a.ok);
  const status = project?.status === 'ACTIVE' ? 'Integration Active' : push ? (lastReg ? 'Live webhook received - ready to activate' : 'Awaiting Live Webhook') : STATUS[project?.status] || '—';
  const tiles = push
    ? [['Mode', 'Authenticated Webhook'], ['Status', status], ['Last Received Event', lastReg ? formatDateTime(lastReg.at) : 'None yet'], ['Successful Registrations', regs.filter((a) => a.ok).length],
      ['Rejected Events', regs.filter((a) => !a.ok).length], ['Authentication Failures', audit.filter((a) => a.action === 'api.unauthenticated').length]]
    : [['Mode', 'Scheduled Polling'], ['Status', status], ['Last Successful Sync', cp?.last_success_at ? formatDateTime(cp.last_success_at) : 'Never'], ['Customers Fetched', cp?.fetched_total ?? 0], ['Failed Attempts', cp?.consecutive_failures ?? 0], ['Next Sync', cp?.next_run_at ? formatDateTime(cp.next_run_at) : '—']];
  const endpoint = `${apiBase || ''}customers/register`;
  return (
    <div className={styles.panel}>
      <div className={styles.head}><h3 className={styles.title}>Sync Logs</h3>
        <div className={styles.actions}><button className={`${styles.btn} ${styles.ghost}`} onClick={load}>Refresh Logs</button>{allowSync && !push && <button className={styles.btn} disabled={busy} onClick={sync}>{busy ? 'Syncing…' : 'Sync Now'}</button>}</div></div>
      <div className={styles.summaryStrip} role="status">{tiles.map(([k, v]) => <span key={k}>{k}: <b>{v}</b></span>)}</div>
      {push && <p className={styles.note}>Customers arrive when the external application sends an authenticated POST to <CopyValue value={endpoint} label="Webhook endpoint" /> with the headers X-Client-Id and X-Client-Secret. Nexus cannot force the external application to send an event, so Sync Now is not used in webhook mode.{!lastReg && ' Remaining step: wait for the first authenticated registration, then activate the integration.'}</p>}
      {cp?.last_error && !push && <p role="alert" className={styles.logErr}>{friendly(cp.last_error)} <details style={{ display: 'inline' }}><summary style={{ display: 'inline', cursor: 'pointer' }}>Details</summary><span className={styles.mono}>{cp.last_error}</span></details></p>}
      <div className={styles.scrollTable}>
        <table className={styles.table}><thead><tr><th>Date &amp; Time</th><th>Activity</th><th>Result</th><th>Details</th></tr></thead>
          <tbody>{audit.map((a, i) => <tr key={i}><td>{formatDateTime(a.at)}</td><td>{a.action}</td><td><span className={`${styles.badge} ${a.ok ? styles.ok : styles.off}`}>{a.ok ? 'OK' : 'Failed'}</span></td><td>{a.ok ? a.detail : friendly(a.detail)}</td></tr>)}
            {audit.length === 0 && <tr><td colSpan={4}>No activity yet.</td></tr>}</tbody></table>
      </div>
      <StepFooter onBack={onBack} />
    </div>
  );
}

const STEP_LABELS = [['config', 'API Setup'], ['mapping', 'Test & Mapping'], ['docs', 'API Delivery'], ['logs', 'Sync Logs']];

/** Four-step guided workflow. Badges come only from saved backend state, never from a click alone. */
function Stepper({ tab, isSuper, project, steps, onGo }) {
  const v = steps?.cfg?.validation || {};
  const push = steps?.cfg?.sync_mode === 'PUSH';
  const failed = (v.customer && !v.customer.ok) || (v.mapping && !v.mapping.ok);
  const state = {
    config: !steps ? 'Loading' : steps.cfg ? 'Saved' : 'Not Started',
    mapping: !steps?.cfg ? 'Not Started' : v.customer?.ok && v.mapping?.ok ? 'Completed' : failed ? 'Needs Attention' : 'Pending Validation',
    docs: steps?.docs?.length ? 'Completed' : 'Not Started',
    logs: project?.status === 'ACTIVE' ? 'Completed' : push && v.mapping?.ok ? 'Awaiting Live Webhook' : 'Not Started',
  };
  const list = STEP_LABELS.filter(([k]) => isSuper || k !== 'docs');
  return (
    <ol className={styles.stepper} aria-label="API integration steps">
      {list.map(([k, l], i) => {
        const st = state[k];
        const done = st === 'Completed' || st === 'Saved';
        return (
          <li key={k} className={`${styles.step} ${tab === k ? styles.stepOn : ''} ${done ? styles.stepDone : ''} ${st === 'Needs Attention' ? styles.stepBad : ''}`}>
            <button type="button" aria-current={tab === k ? 'step' : undefined} onClick={() => onGo(k)}>
              <span className={styles.stepNum} aria-hidden="true">{done ? '✓' : i + 1}</span>
              <span className={styles.stepText}><b>Step {i + 1}: {l}</b><small>{st}</small></span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function StepFooter({ onBack, next, hint }) {
  return (
    <div className={styles.stepFooter}>
      {onBack ? <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={onBack}>← Back</button> : <span />}
      {hint ? <small className={styles.stepHint}>{hint}</small> : <span />}
      {next || <span />}
    </div>
  );
}

function Dialog({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && onClose) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className={styles.modalBack} role="presentation">
      <div className={styles.modalBox} role="dialog" aria-modal="true" aria-label={title}>
        <h3 className={styles.title} style={{ margin: '0 0 10px' }}>{title}</h3>
        {children}
      </div>
    </div>
  );
}

/** Copies the FULL value; reports success only after the clipboard accepted it, otherwise shows a selectable field. */
function SecretField({ label, value, secret }) {
  const [shown, setShown] = useState(!secret);
  const [state, setState] = useState(''); // '' | 'copied' | 'failed'
  const ref = useRef(null);
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('unavailable');
      await navigator.clipboard.writeText(value);
      setState('copied');
    } catch {
      setState('failed');
      setShown(true);
      setTimeout(() => { ref.current?.focus(); ref.current?.select(); }, 0);
    }
  };
  return (
    <div className={styles.field} style={{ marginBottom: 10 }}>
      <span className={styles.fieldLabel}>{label}</span>
      <div className={styles.secretRow}>
        <input ref={ref} readOnly type={shown ? 'text' : 'password'} value={value} className={styles.mono} aria-label={label} onFocus={(e) => e.target.select()} spellCheck={false} autoComplete="off" />
        {secret && <button type="button" className={`${styles.btn} ${styles.ghost}`} onClick={() => setShown((x) => !x)} aria-pressed={shown}>{shown ? 'Hide' : 'Show'}</button>}
        <button type="button" className={styles.btn} onClick={copy}>Copy</button>
      </div>
      {state === 'copied' && <small role="status" className={styles.copyOk}>Copied Successfully</small>}
      {state === 'failed' && <small role="alert" className={styles.err}>Clipboard unavailable - the value is selected above; press Ctrl+C to copy it.</small>}
    </div>
  );
}

function CredentialsModal({ issued, onDone }) {
  const [saved, setSaved] = useState(false);
  return (
    <Dialog title={`New API credentials - ${issued.code}`} onClose={null}>
      <p className={styles.logErr} style={{ marginTop: 0 }}>Shown <b>once</b>. Nexus stores only a hash of the secret and can never display it again. If it is lost, rotate again.</p>
      <SecretField label="Client ID (current, valid)" value={issued.client_id} />
      <SecretField label="API Secret (new)" value={issued.secret} secret />
      <label className={styles.ackRow}><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> I have stored the Client ID and API Secret securely.</label>
      <div className={styles.stepFooter}><span /><span />
        <button type="button" className={styles.btn} disabled={!saved} onClick={onDone} title={saved ? '' : 'Confirm that the credentials are stored first.'}>Done</button>
      </div>
    </Dialog>
  );
}
