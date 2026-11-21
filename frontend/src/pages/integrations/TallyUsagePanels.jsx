import { useCallback, useEffect, useState } from 'react';
import { integrationsApi } from '../../api/integrations';
import { formatDateTime } from '../../utils/date';
import styles from './Integrations.module.css';

/** Tally verification (Tally Required = Yes): the Tally switch (Superadmin, audited) and the Pending Tally Verification list with Retry. */
export function TallyPanel({ code, project, onChanged }) {
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');
  const required = !!project.tally_serial_required;
  const load = useCallback(() => integrationsApi.tallyPending(code).then((d) => setRows(d.results), () => {}), [code]);
  useEffect(() => { load(); }, [load]);

  const flip = async () => {
    if (!window.confirm(`Change "Tally Integration Required" to ${required ? 'No' : 'Yes'}? This is audited and applies to NEW registrations only.`)) return;
    setBusy('flip');
    try { await integrationsApi.updateProject(code, { tally_serial_required: !required }); await onChanged(); setMsg('Setting saved.'); } catch (e) { setMsg(e.message); } finally { setBusy(''); }
  };
  const retry = async (row) => {
    setBusy(row.id); setMsg('');
    try {
      const r = await integrationsApi.retryTally(code, row.id);
      setMsg(r.verified ? `${row.name} verified. Tally Serial Number is now the User ID.` : `Still pending: ${(r.problems || []).join(' ')}`);
      await load();
    } catch (e) { setMsg(e.message); } finally { setBusy(''); }
  };

  return (
    <div className={styles.panel}>
      <h3 className={styles.title}>Tally Verification</h3>
      <p className={styles.note}>
        Tally Integration Required: <b>{required ? 'Yes' : 'No'}</b>. {required
          ? 'Tally Serial Number, Edition, TSS and License Administrator must be verified before a licence is issued. The Tally Serial Number becomes the User ID.'
          : 'Tally verification is skipped; Nexus generates the serial / User ID.'}
      </p>
      {project.license_policy !== 'CALENDAR' && <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={flip}>Set Tally Required = {required ? 'No' : 'Yes'}</button>}
      {msg && <p role="status" className={styles.note}>{msg}</p>}
      {rows.length > 0 && (
        <div className={styles.tableWrap}>
          <h4 style={{ margin: '12px 0 4px' }}>Pending Tally Verification ({rows.length})</h4>
          <table className={styles.table}>
            <thead><tr><th>Customer</th><th>Email</th><th>Status</th><th>Why</th><th>Updated</th><th /></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}>
                <td>{r.name}<br /><small>{r.external_customer_id}</small></td><td>{r.email}</td>
                <td><span className={`${styles.badge} ${styles.pending}`}>Pending Tally Verification</span></td>
                <td><b>{r.title}</b><br /><small>{r.error}</small></td>
                <td>{formatDateTime(r.updated_at)}</td>
                <td><button className={styles.btn} disabled={!!busy} onClick={() => retry(r)}>{busy === r.id ? 'Verifying…' : 'Retry Verification'}</button></td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Usage and device reports computed from real events/heartbeats, with date and customer filters. */
export function UsagePanel({ code }) {
  const [f, setF] = useState({ from: '', to: '', customer: '' });
  const [usage, setUsage] = useState(null);
  const [moves, setMoves] = useState(null);
  const [renewals, setRenewals] = useState([]);
  const [error, setError] = useState('');
  const run = useCallback(async () => {
    const q = Object.fromEntries(Object.entries(f).filter(([, v]) => v));
    try {
      const [u, m, r] = await Promise.all([integrationsApi.usageReport(code, q), integrationsApi.deviceTransfers(code, q), integrationsApi.renewals(code)]);
      setUsage(u); setMoves(m.results); setRenewals(r.results); setError('');
    } catch (e) { setError(e.message); }
  }, [code, f]);
  useEffect(() => { run(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const mins = (v) => (v === null ? 'n/a' : `${v} min`);
  const CAT = { speed: 'Frequent (Speed)', normal: 'Normal', new: 'New', one_time: 'One-Time', not_used: 'Not used' };

  return (
    <div className={styles.panel}>
      <h3 className={styles.title}>Usage &amp; Device Reports</h3>
      <div className={styles.grid}>
        <label className={styles.field}>From<input type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></label>
        <label className={styles.field}>To<input type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></label>
        <label className={styles.field}>Customer<select value={f.customer} onChange={(e) => setF({ ...f, customer: e.target.value })}><option value="">All customers</option>{usage?.customers.map((c) => <option key={c.customer_id} value={c.customer_id}>{c.customer}</option>)}</select></label>
        <div style={{ alignSelf: 'end' }}><button className={styles.btn} onClick={run}>Apply filters</button></div>
      </div>
      {error && <p role="alert" className={styles.err}>{error}</p>}
      {usage && <p className={styles.note}>{usage.note} Time counts only when heartbeats arrive at least every {usage.heartbeat_gap_minutes} minutes.</p>}
      {usage && (
        <div className={styles.tableWrap}>
          <h4>Customer usage summary</h4>
          <table className={styles.table}>
            <thead><tr><th>Customer</th><th>User ID</th><th>Opens</th><th>Logins</th><th>Active days</th><th>Active</th><th>Idle</th><th>Last used</th><th>Class</th></tr></thead>
            <tbody>{usage.customers.map((c) => (
              <tr key={c.customer_id}><td>{c.customer}</td><td>{c.user_id}</td><td>{c.opens}</td><td>{c.logins}</td><td>{c.active_days}</td><td>{mins(c.active_minutes)}</td><td>{mins(c.idle_minutes)}</td><td>{c.last_used ? formatDateTime(c.last_used) : '—'}</td><td>{CAT[c.usage_category] || '—'}</td></tr>))}
              {usage.customers.length === 0 && <tr><td colSpan={9}>No usage events in this range.</td></tr>}</tbody>
          </table>
          <h4>Daily usage</h4>
          <table className={styles.table}>
            <thead><tr><th>Date (IST)</th><th>Customer</th><th>Opens</th><th>Logins</th><th>Active</th><th>Idle</th></tr></thead>
            <tbody>{usage.daily.map((d, i) => <tr key={i}><td>{d.date}</td><td>{d.customer}</td><td>{d.opens}</td><td>{d.logins}</td><td>{mins(d.active_minutes)}</td><td>{mins(d.idle_minutes)}</td></tr>)}</tbody>
          </table>
        </div>
      )}
      {moves && (
        <div className={styles.tableWrap}>
          <h4>Device transfer history</h4>
          <table className={styles.table}>
            <thead><tr><th>When</th><th>Customer</th><th>From device</th><th>To device</th></tr></thead>
            <tbody>{moves.map((m, i) => <tr key={i}><td>{formatDateTime(m.at)}</td><td>{m.customer}</td><td>{m.from_device || '—'}</td><td>{m.to_device || '—'}</td></tr>)}
              {moves.length === 0 && <tr><td colSpan={4}>No device transfers.</td></tr>}</tbody>
          </table>
        </div>
      )}
      {renewals.length > 0 && (
        <div className={styles.tableWrap}>
          <h4>Licence term history</h4>
          <table className={styles.table}>
            <thead><tr><th>When</th><th>Customer</th><th>Action</th><th>Type</th><th>Previous expiry</th><th>New expiry</th><th>By</th></tr></thead>
            <tbody>{renewals.map((r, i) => <tr key={i}><td>{formatDateTime(r.at)}</td><td>{r.customer}</td><td>{r.action}</td><td>{r.license_type}</td><td>{r.previous_expires_at ? formatDateTime(r.previous_expires_at) : '—'}</td><td>{r.new_expires_at ? formatDateTime(r.new_expires_at) : 'No expiry'}</td><td>{r.by || '—'}</td></tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
