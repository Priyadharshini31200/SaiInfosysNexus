import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Alert from '../../components/Alert';
import Modal from '../../components/Modal';
import { integrationsApi } from '../../api/integrations';
import Drawer from '../../components/Drawer';
import { formatDateTime } from '../../utils/date';
import { CopyIcon, EyeIcon, EyeOffIcon, PencilIcon, RefreshIcon, TrashIcon } from '../../components/Icons';
import styles from './UniverseCustomer.module.css';

const dt = (v) => (v ? formatDateTime(v) : '—');
const KV = ({ k, children, mono }) => <div className={styles.kv}><span>{k}</span><span className={mono ? styles.mono : ''}>{children ?? '—'}</span></div>;
const Badge = ({ tone = 'info', children }) => <span className={`${styles.badge} ${styles[tone]}`}>{children}</span>;
const TONE = { 'Pending Review': 'warn', 'Pending Tally Verification': 'bad', 'On Hold': 'warn', Rejected: 'bad', Approved: 'ok', Active: 'ok', Expired: 'bad', Suspended: 'bad', 'Pending Activation': 'warn' };
/** Temporary password generated in the browser with the cryptographic RNG (never Math.random): 10 chars, upper + lower + digit, no look-alikes. */
function genPassword() {
  const U = 'ABCDEFGHJKLMNPQRSTUVWXYZ', L = 'abcdefghijkmnpqrstuvwxyz', D = '23456789', all = U + L + D;
  const rnd = (n) => { const a = new Uint32Array(1); crypto.getRandomValues(a); return a[0] % n; };
  const out = [U[rnd(U.length)], L[rnd(L.length)], D[rnd(D.length)], ...Array.from({ length: 7 }, () => all[rnd(all.length)])];
  for (let k = out.length - 1; k > 0; k -= 1) { const j = rnd(k + 1); [out[k], out[j]] = [out[j], out[k]]; }
  return out.join('');
}
/** Admin-entered temporary password: 3-128 characters of any kind, no complexity rule (changed at first login). Mirrors universe.check_password. */
const validPassword = (p) => p.length >= 3 && p.length <= 128;
/** Expiry preview: null = no expiry (Lifetime), undefined = invalid input. The backend recomputes it on Save & Allow. */
function expiryOf(l, start) {
  const base = start ? new Date(start) : new Date();
  const n = (v) => { const x = Number(v); return Number.isInteger(x) && x > 0 ? x : 0; };
  if (l.license_type === 'LIFETIME') return null;
  if (l.license_type === 'ONE_YEAR') { const y = n(l.years); if (!y || y > 10) return undefined; const d = new Date(base); d.setFullYear(d.getFullYear() + y); return d.toISOString(); }
  const days = n(l.license_type === 'CUSTOM' ? l.custom_days : l.trial_days);
  return days && days <= 3650 ? new Date(base.getTime() + days * 86400000).toISOString() : undefined;
}
const USAGE = { speed: 'Speed User', normal: 'Normal User', new: 'New User', one_time: 'One-Time User', not_used: 'Not used' };

/** One wide popup for a UNIVERSE customer. Registration mode: review -> Tally -> credentials -> licence -> Hold / Reject / Save & Allow.
 *  View mode (after approval): customer, licence, usage & devices, history, with the edit / extend / renew / suspend / reset actions. */
export default function UniverseCustomerDrawer({ code, kind, id, onClose, onChanged }) {
  const [rec, setRec] = useState(null);
  const [tab, setTab] = useState('customer');
  const [edit, setEdit] = useState(null);       // editable copy while "Edit Customer" is active
  const [lic, setLic] = useState({ license_type: 'TRIAL', trial_days: '', years: '1', custom_days: '30' });
  const [acct, setAcct] = useState(() => ({ user_id: '', password: genPassword(), show: false, copied: '' })); // pre-approval credentials (memory only)
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const [resetPw, setResetPw] = useState(null); // one-time temporary password from an explicit reset; memory only, gone on close/refresh
  const [extOpen, setExtOpen] = useState(false);
  const [suspendOpen, setSuspendOpen] = useState(false);
  const [toast, setToast] = useState('');
  const showToast = (t) => { setToast(t); setTimeout(() => setToast(''), 1800); };
  const [errors, setErrors] = useState({});
  const [secret, setSecret] = useState(null);   // one-time credentials, kept in memory only
  const [extend, setExtend] = useState('3');
  const navigate = useNavigate();
  const [delOpen, setDelOpen] = useState(false);
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState('');
  const [curKind, setCurKind] = useState(kind);
  const [curId, setCurId] = useState(id);

  const defaultsFilled = useRef(false); // licence defaults are filled ONCE; a field the admin clears stays empty while they type
  const load = useCallback(async (k = curKind, i = curId, l = lic) => {
    try {
      const q = k === 'registration' ? { license_type: l.license_type, trial_days: l.license_type === 'TRIAL' ? l.trial_days : '', years: l.license_type === 'ONE_YEAR' ? l.years : '' } : {};
      const { record } = await integrationsApi.universeCustomer(code, k, i, q);
      setRec(record);
      if (k === 'registration' && !l.trial_days && !defaultsFilled.current) { defaultsFilled.current = true; setLic((x) => ({ ...x, trial_days: String(record.license_defaults.trial_days), years: String(record.license_defaults.years) })); }
      if (k === 'registration' && record.account?.suggested_user_id) setAcct((a) => (a.user_id ? a : { ...a, user_id: record.account.suggested_user_id }));
    } catch (e) { setMsg(e.message); }
  }, [code, curKind, curId, lic]);
  useEffect(() => { load(curKind, curId, lic); }, [curKind, curId, lic.license_type, lic.trial_days, lic.years]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = (name, fn, ok) => async () => {
    setBusy(name); setMsg(''); setErrors({});
    try { const r = await fn(); if (ok) setMsg(typeof ok === 'function' ? ok(r) : ok); await onChanged(); return r; } catch (e) { setMsg(e.message); setErrors(e.errors || {}); return null; } finally { setBusy(''); }
  };
  const act = (action, body = {}) => integrationsApi.universeAction(code, curKind, curId, action, body);
  const isReg = curKind === 'registration';
  const r = rec;
  const can = r?.can || {};
  const status = isReg ? r?.registration.approval_status : r?.license.status;

  const saveEdit = run('save', async () => {
    const res = await integrationsApi.patchUniverseCustomer(code, curKind, curId, { customer_name: edit.name, company_name: edit.company_name, email: edit.email, contact_number: edit.contact_number, address: edit.address, gstin: edit.gstin });
    setRec(res.record); setEdit(null); setErrors({});
  }, 'Changes saved successfully.');

  const approve = run('approve', async () => {
    const res = await act('approve', { license_type: lic.license_type, trial_days: lic.trial_days, years: lic.years, custom_days: lic.custom_days, user_id: acct.user_id.trim(), password: acct.password });
    setSecret(res.result.temporary_password ? res.result : null);
    setCurKind('customer'); setCurId(res.record.id); setRec(res.record); setTab('customer');
  }, 'Approved. The licence is active.');

  const title = r ? `${r.registration.name}${r.registration.company_name ? ` · ${r.registration.company_name}` : ''}` : 'Customer';

  return (
<>
        <Drawer open onClose={onClose} wide flush titleId="universe-customer-title" closeLabel="Close customer"
      title={isReg || !r
        ? <span style={{ display: 'grid', lineHeight: 1.25 }}><span>{title}</span><small style={{ fontSize: 11, fontWeight: 400, color: 'var(--muted)' }}>{r ? `${r.project.name} · Registration review` : 'Loading…'}</small></span>
        : edit ? <span className={styles.hdr}><span className={styles.hdrName}>Edit Customer – {r.registration.name}</span></span>
        : <span className={styles.hdr}><span className={styles.hdrName}>{r.registration.name}</span><span className={styles.hdrSep}>·</span><span className={styles.mono}>{r.project.code}</span><span className={styles.hdrSep}>|</span><Badge tone={TONE[r.registration.approval_status] || 'info'}>{r.registration.approval_status}</Badge></span>}>
      {!r && <p className={styles.body}>{msg || 'Loading…'}</p>}
      {r && (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
          {/* One unified card (no tabs): Customer Information -> Nexus Account & License -> Usage & Devices -> License & Activity History */}
          <div className={`${styles.body} ${isReg ? '' : `${styles.cardBody} ${styles.unified}`}`} style={{ flex: 1, overflowY: 'auto' }}>
            {secret && (
              <div className={styles.secret} role="status">
                <b>One-time credentials — copy now</b>
                <p className={styles.muted}>User ID <span className={styles.mono}>{secret.user_id}</span> · Temporary password <span className={styles.mono}>{secret.temporary_password}</span></p>
                <p className={styles.muted}>{secret.password_notice} The activation key is generated internally and is never shown or emailed.</p>
                <button className={`${styles.btn} ${styles.ghost}`} onClick={() => setSecret(null)}>I have copied it — hide</button>
              </div>
            )}

            {/* ---- A. Customer registration information (both modes) ---- */}
            {(
              <section className={styles.section} aria-label="Customer information">
                <h3>Customer Information
                  {can.edit && !edit && isReg && r.registration.approval_key !== 'APPROVED' && <button className={`${styles.btn} ${styles.ghost}`} onClick={() => setEdit({ ...r.registration })}>Edit Customer</button>}
                </h3>
                {edit ? (
                  <div className={styles.grid}>
                    {[['name', 'Customer Name'], ['company_name', 'Company Name'], ['email', 'Email Address'], ['contact_number', 'Contact Number'], ['gstin', 'GSTIN'], ['address', 'Address']].map(([k, l]) => (
                      <label key={k} className={styles.field}>{l}<input value={edit[k] || ''} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} />{errors[k === 'name' ? 'customer_name' : k] && <span className={styles.err}>{[].concat(errors[k === 'name' ? 'customer_name' : k]).join(' ')}</span>}</label>
                    ))}
                    <KV k="External Customer ID" mono>{r.registration.external_customer_id}</KV>
                  </div>
                ) : (
                  isReg ? (
                    <div className={styles.grid}>
                      <KV k="Customer Name">{r.registration.name}</KV><KV k="Company Name">{r.registration.company_name}</KV><KV k="Email">{r.registration.email}</KV>
                      <KV k="Contact Number">{r.registration.contact_number}</KV><KV k="External Customer ID" mono>{r.registration.external_customer_id}</KV><KV k="Project Code" mono>{r.project.code}</KV>
                      <KV k="Approval Status"><Badge tone={TONE[r.registration.approval_status] || 'info'}>{r.registration.approval_status === 'Pending Review' ? 'Pending Approval' : r.registration.approval_status}</Badge></KV>
                      {r.registration.gstin && <KV k="GSTIN">{r.registration.gstin}</KV>}{r.registration.address && <KV k="Address">{r.registration.address}</KV>}
                    </div>
                  ) : (
                  <div className={styles.grid}>
                    <KV k="Customer Name">{r.registration.name}</KV><KV k="Company Name">{r.registration.company_name}</KV><KV k="Email Address">{r.registration.email}</KV>
                    <KV k="Contact Number">{r.registration.contact_number}</KV><KV k="GSTIN">{r.registration.gstin || "—"}</KV><KV k="Address">{r.registration.address}</KV><KV k="External Customer ID" mono>{r.registration.external_customer_id}</KV>
                    <KV k="Project Code" mono>{r.project.code}</KV><KV k="Registration">{dt(r.registration.registered_at)}{r.registration.source ? ` · ${r.registration.source}` : ''}</KV>
                    <KV k="Approval Status"><Badge tone={TONE[r.registration.approval_status] || 'info'}>{r.registration.approval_status}</Badge></KV>
                    {!isReg && <KV k="Approved">{r.registration.approved_by ? `${r.registration.approved_by} · ${dt(r.registration.approved_at)}` : '—'}</KV>}
                  </div>)
                )}
              </section>
            )}

            {/* ---- B. Tally ---- */}
            {r.tally && (
              <section className={styles.section} aria-label="Tally license information">
                <h3>{isReg ? 'Tally License Information' : 'Tally Details'}<Badge tone={r.tally.verified ? 'ok' : 'bad'}>{r.tally.verified ? 'Verified' : 'Verification required'}</Badge></h3>
                {!r.tally.verified && (
                  <div className={styles.alert} role="alert">
                    <b>{r.tally.title || 'Tally License Details Required'}</b>
                    {r.tally.message} {r.tally.missing?.length > 0 && <>Missing: <strong>{r.tally.missing.join(', ')}</strong>.</>} {(r.tally.problems || []).join(' ')}
                    <div style={{ marginTop: 8 }}><button className={styles.btn} disabled={!!busy || !can.approve} onClick={run('retry', async () => { const res = await act('retry-tally'); setRec(res.record); }, 'Verification re-run.')}>{busy === 'retry' ? 'Verifying…' : 'Retry Verification'}</button></div>
                  </div>
                )}
                <div className={styles.grid} style={{ marginTop: 8 }}>
                  <KV k="Tally Serial Number" mono>{r.tally.serial}</KV><KV k="Tally Edition">{r.tally.edition}</KV><KV k="Tally Software Services (TSS)">{r.tally.tss_active === true || r.tally.tss_active === 'true' ? 'Active' : r.tally.tss_active ? String(r.tally.tss_active) : null}</KV>
                  <KV k="License Administrator">{r.tally.license_admin}</KV><KV k="TSS Expiry">{r.tally.tss_expiry}</KV><KV k="Virtual User Limit">{r.tally.virtual_user_limit}</KV>
                  <KV k="Gateway Server">{r.tally.gateway_server}</KV>{r.tally.verified_at && <KV k="Verified on">{dt(r.tally.verified_at)}</KV>}
                </div>
              </section>
            )}

            {/* ---- C. Nexus credentials / account ---- */}
            {(
              <section className={styles.section} aria-label={isReg ? 'Nexus credentials' : 'Nexus account and license'}>
                <h3>{isReg ? 'Nexus Credentials' : 'Nexus Account & License'}
                  {!isReg && r.license && <Badge tone={TONE[r.license.status] || 'info'}>{r.license.status}</Badge>}
                </h3>
                {isReg && r.registration.approval_key !== 'APPROVED' && r.registration.approval_key !== 'REJECTED' ? (
                  <div className={`${styles.grid} ${styles.credGrid}`}>
                    <label className={styles.field}><span>Nexus User ID / Serial <b style={{ color: '#c62828' }}>*</b></span>
                      <span className={styles.credRow}>
                        <input value={acct.user_id} maxLength={9} inputMode="numeric" className={styles.mono} aria-invalid={!/^9\d{8}$/.test(acct.user_id) || undefined}
                          onChange={(e) => setAcct({ ...acct, user_id: e.target.value.replace(/\D/g, '') })} />
                        <button type="button" className={styles.regenBtn} disabled={!!busy} title="Regenerate Serial Number" aria-label="Regenerate Serial Number" onClick={async () => { const { record } = await integrationsApi.universeCustomer(code, curKind, curId, {}); setAcct((a) => ({ ...a, user_id: record.account.suggested_user_id || a.user_id })); }}><RefreshIcon size={18} aria-hidden="true" /></button>
                      </span>
                      {!/^9\d{8}$/.test(acct.user_id) ? <span className={styles.err}>Exactly 9 digits starting with 9.</span> : errors.user_id && <span className={styles.err}>{[].concat(errors.user_id).join(' ')}</span>}
                    </label>
                    <label className={styles.field}><span>Temporary Password <b style={{ color: '#c62828' }}>*</b></span>
                      <span className={styles.credRow}>
                        <input type={acct.show ? 'text' : 'password'} value={acct.password} maxLength={128} autoComplete="new-password" className={styles.mono} onChange={(e) => setAcct({ ...acct, password: e.target.value, copied: '' })} />
                        <button type="button" className={styles.regenBtn} onClick={() => setAcct({ ...acct, show: !acct.show })} aria-pressed={acct.show} title={acct.show ? 'Hide Password' : 'Show Password'} aria-label={acct.show ? 'Hide Password' : 'Show Password'}>{acct.show ? <EyeOffIcon size={18} aria-hidden="true" /> : <EyeIcon size={18} aria-hidden="true" />}</button>
                        <button type="button" className={styles.regenBtn} title="Copy Password" aria-label="Copy Password" onClick={async () => { try { await navigator.clipboard.writeText(acct.password); setAcct({ ...acct, copied: 'ok' }); } catch { setAcct({ ...acct, show: true, copied: 'fail' }); } }}><CopyIcon size={18} aria-hidden="true" /></button>
                        <button type="button" className={styles.regenBtn} title="Generate New Password" aria-label="Generate New Password" onClick={() => setAcct({ ...acct, password: genPassword(), copied: '' })}><RefreshIcon size={18} aria-hidden="true" /></button>
                      </span>
                      {!validPassword(acct.password) ? <span className={styles.err}>{acct.password ? 'At least 3 characters.' : 'Password is required.'}</span>
                        : acct.copied === 'ok' ? <small style={{ color: '#1d7a4f' }}>Copied Successfully</small> : acct.copied === 'fail' ? <small className={styles.err}>Clipboard unavailable - select the password and press Ctrl+C.</small>
                          : errors.password && <span className={styles.err}>{[].concat(errors.password).join(' ')}</span>}
                    </label>
                    <p className={styles.activationLine}>Device Activation: <b>AUTOMATIC</b> <span>(internal key generated on approval)</span></p>
                    {r.tally && <KV k="Tally Serial (kept separately)" mono>{r.tally.serial}</KV>}
                  </div>
                ) : (
                <div className={styles.grid}>
                  <KV k="User ID / Serial Number" mono>{r.account.user_id || 'Assigned on approval'}</KV>
                  {!isReg && <KV k="Account Status"><Badge tone={r.account.account_status === 'Active' ? 'ok' : 'bad'}>{r.account.account_status}</Badge>{r.account.locked ? ' (locked)' : ''}</KV>}
                  <KV k="Password Setup">{r.account.password_status}</KV>
                  <KV k="Activation">{r.account.activation_key_status}</KV>
                  {!isReg && r.license && <>
                    <KV k="License Type">{r.license.type_label}</KV>
                    <KV k="License Duration">{r.license.type_label === 'Yearly' ? `${r.license.years} year(s)` : r.license.type_label === 'Lifetime' ? 'No expiry' : `${r.license.trial_days ?? '—'} day(s)`}</KV>
                    <KV k="Start Date">{dt(r.license.starts_at)}</KV><KV k="Expiry Date">{r.license.expires_at ? dt(r.license.expires_at) : 'No fixed expiry'}</KV>
                    <KV k="Remaining Days">{r.license.remaining_days === null ? '—' : r.license.remaining_days}</KV>{r.license.last_renewal_at && <KV k="Last Renewal">{dt(r.license.last_renewal_at)}</KV>}
                  </>}
                </div>)}
                {!isReg && r.license?.renewal_required && <div className={styles.alert} role="alert" style={{ marginTop: 8 }}><b>License Expired — Renewal Required</b>Protected software access is denied until an authorised renewal.</div>}
                {!isReg && (can.reset_password || can.extend_trial || can.renew || can.inactivate || can.set_license) && (
                  <div className={styles.actMenu} role="group" aria-label="Account / License Actions">
                    <h4 className={styles.sub}>Account / License Actions</h4>
                    <div className={styles.actRow}>
                      {can.reset_password && <div className={styles.col}><span className={styles.lbl}>Password</span><span className={styles.inline}>
                        <input readOnly title="Temporary password - shown once after Reset" className={`${styles.mono} ${styles.pwOut}`} type={resetPw?.show ? 'text' : 'password'} value={resetPw?.value || ''} placeholder="Password" aria-label="Temporary password (shown once after reset)" onFocus={(e) => e.target.select()} />
                        <button type="button" className={styles.regenBtn} disabled={!!busy} title="Reset Temporary Password" aria-label="Reset Temporary Password"
                          onClick={() => window.confirm('Generate a NEW temporary password? Existing sessions are signed out and the customer must change it at next login.') && run('reset', async () => { const res = await act('reset-password'); setRec(res.record); setResetPw({ value: res.result.temporary_password, show: false }); }, '')()}><RefreshIcon size={18} aria-hidden="true" /></button>
                        <button type="button" className={styles.iconLite} disabled={!resetPw?.value} title={resetPw?.show ? 'Hide Password' : 'Show Password'} aria-label={resetPw?.show ? 'Hide Password' : 'Show Password'} aria-pressed={!!resetPw?.show} onClick={() => setResetPw({ ...resetPw, show: !resetPw.show })}>{resetPw?.show ? <EyeOffIcon size={18} aria-hidden="true" /> : <EyeIcon size={18} aria-hidden="true" />}</button>
                        <button type="button" className={styles.iconLite} disabled={!resetPw?.value} title="Copy Password" aria-label="Copy Password" onClick={async () => { try { await navigator.clipboard.writeText(resetPw.value); showToast('Copied'); } catch { setResetPw({ ...resetPw, show: true }); showToast('Clipboard unavailable - select and press Ctrl+C'); } }}><CopyIcon size={18} aria-hidden="true" /></button>
                      </span></div>}
                      {can.extend_trial && r.license.type_label === 'Trial' && <div className={styles.col}><span className={styles.lbl}>Trial</span><span className={styles.inline}>
                        {!extOpen
                          ? <button type="button" className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} title="Add days to the current trial" onClick={() => { setExtend(''); setExtOpen(true); }}>Extend Trial</button>
                          : <>
                            <input aria-label="Additional days" placeholder="Additional days" type="text" inputMode="numeric" value={extend} onChange={(e) => setExtend(e.target.value.replace(/\D/g, ''))} className={styles.daysIn} autoFocus />
                            <button type="button" className={styles.tickBtn} title="Extend Trial" aria-label="Confirm trial extension" disabled={!!busy || !/^[1-9]\d*$/.test(String(extend))} onClick={run('extend', async () => { const res = await act('extend-trial', { days: Number(extend) }); setRec(res.record); setExtOpen(false); }, 'Trial extended.')}>
                              <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg></button>
                            <button type="button" className={styles.iconLite} title="Cancel" aria-label="Cancel trial extension" disabled={!!busy} onClick={() => setExtOpen(false)}>
                              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg></button>
                          </>}
                      </span></div>}
                      {can.renew && r.license.type_label === 'Yearly' && <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={run('renew', async () => { const res = await act('renew'); setRec(res.record); }, 'License renewed.')}>Renew License</button>}
                      {can.inactivate && (r.account.account_status === 'Active'
                        ? <button title="Suspend this customer's license" className={`${styles.btn} ${styles.danger}`} disabled={!!busy} onClick={() => setSuspendOpen(true)}>Suspend License</button>
                        : <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy} onClick={run('react', async () => { const res = await act('reactivate'); setRec(res.record); }, 'License reactivated.')}>Reactivate License</button>)}
                      {can.set_license && <>
                        <label className={`${styles.field} ${styles.fLic}`}>License Type<select value={lic.license_type} onChange={(e) => setLic({ ...lic, license_type: e.target.value })}><option value="TRIAL">Trial</option><option value="LIFETIME">Lifetime</option><option value="ONE_YEAR">Yearly</option><option value="CUSTOM">Custom (Days)</option></select></label>
                        {lic.license_type === 'TRIAL' && <label className={`${styles.field} ${styles.fDur}`} title="Trial duration in days">Trial Days<input type="number" min="1" value={lic.trial_days} onChange={(e) => setLic({ ...lic, trial_days: e.target.value })} /></label>}
                        {lic.license_type === 'ONE_YEAR' && <label className={`${styles.field} ${styles.fDur}`} title="Duration in years">Years<input type="number" min="1" max="10" value={lic.years} onChange={(e) => setLic({ ...lic, years: e.target.value })} /></label>}
                        {lic.license_type === 'CUSTOM' && <label className={`${styles.field} ${styles.fDur}`} title="Custom duration in days">Days<input type="number" min="1" max="3650" value={lic.custom_days} onChange={(e) => setLic({ ...lic, custom_days: e.target.value.replace(/\D/g, '') })} /></label>}
                        <button type="button" title="Apply License Change" aria-label="Apply License Change" className={styles.regenBtn} disabled={!!busy || (lic.license_type !== 'LIFETIME' && !/^[1-9]\d*$/.test(String(lic.license_type === 'ONE_YEAR' ? lic.years : lic.license_type === 'CUSTOM' ? lic.custom_days : lic.trial_days)))}
                          onClick={() => window.confirm('Apply this licence change now?') && run('type', async () => { const res = await act('set-license', { license_type: lic.license_type, trial_days: lic.trial_days, years: lic.years, custom_days: lic.custom_days }); setRec(res.record); }, 'License updated.')()}><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg></button>
                      </>}
                    </div>
                  </div>
                )}
              </section>
            )}

            {/* ---- D. Licence configuration (registration) ---- */}
            {isReg && r.registration.approval_key !== 'APPROVED' && r.registration.approval_key !== 'REJECTED' && (
              <section className={styles.section} aria-label="License configuration">
                <h3>License Configuration</h3>
                <div className={`${styles.grid} ${styles.licGrid}`}>
                  <label className={styles.field}><span>License Type <b style={{ color: '#c62828' }}>*</b></span>
                    <select value={lic.license_type} onChange={(e) => setLic({ ...lic, license_type: e.target.value })}><option value="TRIAL">Trial</option><option value="LIFETIME">Lifetime</option><option value="ONE_YEAR">Yearly</option><option value="CUSTOM">Custom (Days)</option></select>
                  </label>
                  {lic.license_type === 'TRIAL' && <label className={styles.field}><span>Trial Days <b style={{ color: '#c62828' }}>*</b></span><input type="number" min="1" max="3650" value={lic.trial_days} onChange={(e) => setLic({ ...lic, trial_days: e.target.value })} />{errors.trial_days && <span className={styles.err}>{errors.trial_days[0]}</span>}</label>}
                  {lic.license_type === 'ONE_YEAR' && <label className={styles.field}><span>Number of Years <b style={{ color: '#c62828' }}>*</b></span><input type="number" min="1" max="10" value={lic.years} onChange={(e) => setLic({ ...lic, years: e.target.value })} />{errors.years && <span className={styles.err}>{errors.years[0]}</span>}</label>}
                  {lic.license_type === 'CUSTOM' && <label className={styles.field}><span>Number of Days <b style={{ color: '#c62828' }}>*</b></span><input type="number" min="1" max="3650" value={lic.custom_days} onChange={(e) => setLic({ ...lic, custom_days: e.target.value })} />{errors.custom_days && <span className={styles.err}>{errors.custom_days[0]}</span>}</label>}
                  <KV k="Start Date">{dt(r.preview.starts_at)}</KV>
                  <KV k="Calculated Expiry">{(() => { const e = expiryOf(lic, r.preview.starts_at); return e === null ? 'No expiry (Lifetime)' : e ? dt(e) : 'Enter a valid duration'; })()}</KV>
                </div>
              </section>
            )}

            {/* ---- Section 3: Usage & Devices ---- */}
            {!isReg && (
              <section className={styles.section} aria-label="Usage and devices">
                <h3>Usage &amp; Devices<span className={styles.badges}><Badge tone="info">{USAGE[r.usage.category] || '—'}</Badge><Badge tone={r.devices.active_session ? 'ok' : 'warn'}>{r.devices.active_session ? 'Active session' : 'No active session'}</Badge></span></h3>
                <div className={styles.grid}>
                  <KV k="Last Login">{dt(r.usage.last_login)}</KV><KV k="Last Used">{dt(r.usage.last_used)}</KV><KV k="Total Logins">{r.usage.total_logins}</KV>
                  <KV k="Active Days">{r.usage.active_days}</KV><KV k="Software Opens">{r.usage.total_opens}</KV>
                  <KV k="Today / This Month">{r.usage.duration_available ? `${r.usage.daily_minutes ?? 0} min / ${r.usage.monthly_minutes} min` : 'Data Unavailable'}</KV>
                  <KV k="Authorized Device">{r.devices.current || 'No device authorized yet.'}</KV>
                  <KV k="Device Status">{r.devices.active_session ? 'Active session' : r.devices.current ? 'No active session' : 'Not authorized'}</KV>
                  <KV k="Last Seen">{dt(r.devices.last_seen_at)}</KV><KV k="Last Device Transfer">{dt(r.devices.last_transfer_at)}</KV>
                </div>
                {r.usage.note && <p className={styles.muted} style={{ marginTop: 6 }}>{r.usage.note}</p>}
                <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Device</th><th>Status</th><th>Authorized</th><th>Ended</th><th>Reason</th></tr></thead>
                  <tbody>{r.devices.history.map((d, i) => <tr key={i}><td>{d.device}</td><td>{d.status}</td><td>{dt(d.authorized_at)}</td><td>{dt(d.ended_at)}</td><td>{d.reason || '—'}</td></tr>)}
                    {r.devices.history.length === 0 && <tr><td colSpan={5}>No device authorized yet.</td></tr>}</tbody></table></div>
              </section>
            )}

            {/* ---- Section 4: License & Activity History (read-only) ---- */}
            {!isReg && (
              <section className={styles.section} aria-label="License and activity history">
                <h3>License &amp; Activity History</h3>
                <h4 className={styles.sub}>License Changes, Renewals &amp; Extensions</h4>
                <div className={`${styles.tableWrap} ${styles.scroll5}`}><table className={styles.table}><thead><tr><th>When</th><th>Action</th><th>Type</th><th>Years</th><th>From</th><th>To</th><th>By</th></tr></thead>
                  <tbody>{r.history.terms.map((t, i) => <tr key={i}><td>{dt(t.at)}</td><td>{t.action}</td><td>{t.type}</td><td>{t.years || '—'}</td><td>{dt(t.from)}</td><td>{t.to ? dt(t.to) : 'No expiry'}</td><td>{t.by || '—'}</td></tr>)}
                    {r.history.terms.length === 0 && <tr><td colSpan={7}>No changes recorded.</td></tr>}</tbody></table></div>
                <h4 className={styles.sub}>Approval, Activation &amp; Device Transfer Audit</h4>
                <div className={`${styles.tableWrap} ${styles.scroll5}`}><table className={styles.table}><thead><tr><th>When</th><th>Event</th><th>Result</th><th>Detail</th><th>By</th></tr></thead>
                  <tbody>{r.history.audit.map((a, i) => <tr key={i}><td>{dt(a.at)}</td><td>{a.action}</td><td>{a.ok ? 'OK' : 'Failed'}</td><td>{a.detail}</td><td>{a.by || '—'}</td></tr>)}
                    {r.history.audit.length === 0 && <tr><td colSpan={5}>No events recorded.</td></tr>}</tbody></table></div>
              </section>
            )}
            {msg && <p role="status" className={styles.muted}>{msg}</p>}
          </div>

          {/* ---- Customer card actions (bottom-right): Edit opens the CUSTOMER edit form (never Edit Project); Delete asks for confirmation ---- */}
          {!isReg && !edit && (can.edit || can.delete) && (
            <div className={styles.cardActions}>
              {can.edit && <button type="button" className={styles.iconAct} title="Edit Customer" aria-label="Edit Customer" disabled={!!busy}
                onClick={() => { setErrors({}); setMsg(''); setEdit({ ...r.registration }); }}><PencilIcon size={18} aria-hidden="true" /></button>}
              {can.delete && <button type="button" className={`${styles.iconAct} ${styles.iconDanger}`} title="Delete Customer" aria-label="Delete Customer" disabled={!!busy}
                onClick={() => { setDelError(''); setDelOpen(true); }}><TrashIcon size={18} aria-hidden="true" /></button>}
            </div>
          )}
          {/* ---- Footer actions ---- */}
          {edit && (
            <div className={styles.footer}><span className={styles.grow}>Editing — nothing is saved until you press Save Changes.</span>
              <button className={`${styles.btn} ${styles.ghost}`} onClick={() => { setEdit(null); setErrors({}); }}>Cancel</button>
              <button className={styles.btn} disabled={busy === 'save'} onClick={saveEdit}>{busy === 'save' ? 'Saving…' : 'Save Changes'}</button></div>
          )}
          {!edit && isReg && r.registration.approval_key !== 'APPROVED' && r.registration.approval_key !== 'REJECTED' && (
            <div className={styles.footer}>
              <span className={styles.grow}>{r.tally && !r.tally.verified ? 'Save & Allow is disabled until the Tally details are verified.' : 'Nothing is issued until you press Save & Allow.'}</span>
              {can.approve && <>
                <button className={`${styles.btn} ${styles.ghost}`} disabled={!!busy || r.registration.approval_key === 'HELD'} onClick={run('hold', async () => { const res = await act('hold'); setRec(res.record); }, 'Registration on hold.')}>Hold</button>
                <button className={`${styles.btn} ${styles.danger}`} disabled={!!busy} onClick={() => window.confirm('Reject this registration?') && run('reject', async () => { const res = await act('reject'); setRec(res.record); }, 'Registration rejected.')()}>Reject</button>
                <button className={styles.btn} disabled={!!busy || (r.tally && !r.tally.verified) || !validPassword(acct.password)} onClick={approve}>{busy === 'approve' ? 'Saving…' : 'Save & Allow'}</button></>}
            </div>
          )}
        </div>
      )}
    </Drawer>
      {toast && <div role="status" aria-live="polite" className={styles.toast}>{toast}</div>}
      {suspendOpen && (
        <Modal title="Suspend Customer License" onClose={busy === 'sus' ? () => {} : () => setSuspendOpen(false)}>
          <p>Are you sure you want to suspend this customer&apos;s license? The customer will lose access to the software while the license is suspended. You can reactivate the license later.</p>
          <div className={styles.actions} style={{ justifyContent: 'flex-end', marginTop: 12 }}>
            <button type="button" className={`${styles.btn} ${styles.ghost}`} disabled={busy === 'sus'} onClick={() => setSuspendOpen(false)}>Cancel</button>
            <button type="button" className={`${styles.btn} ${styles.danger}`} disabled={busy === 'sus'} onClick={run('sus', async () => { const res = await act('inactivate'); setRec(res.record); setSuspendOpen(false); }, 'License suspended.')}>{busy === 'sus' ? 'Suspending…' : 'Confirm Suspension'}</button>
          </div>
        </Modal>
      )}
    {delOpen && (
      <Modal title="Delete Customer" onClose={delBusy ? () => {} : () => setDelOpen(false)}>
        <p>Delete <strong>{r?.registration?.name}</strong>? This cannot be undone. A customer with device, usage or licence history is protected; Inactivate it instead.</p>
        {delError && <Alert type="error">{delError}</Alert>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button type="button" className={`${styles.btn} ${styles.ghost}`} disabled={delBusy} onClick={() => setDelOpen(false)}>Cancel</button>
          <button type="button" className={styles.btn} disabled={delBusy} onClick={async () => {
            setDelBusy(true); setDelError('');
            try { await integrationsApi.deleteUniverseCustomer(code, 'customer', curId); setDelOpen(false); await onChanged(); onClose(); } catch (e) { setDelError(e.message); } finally { setDelBusy(false); }
          }}>{delBusy ? 'Deleting…' : 'Delete Customer'}</button>
        </div>
      </Modal>
    )}
    </>
  );
}
