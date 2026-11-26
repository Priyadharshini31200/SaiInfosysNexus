import { useCallback, useEffect, useRef, useState } from 'react';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import Drawer from '../../components/Drawer';
import Modal from '../../components/Modal';
import { formatDate, formatDateTime } from '../../utils/date';
import { STATUS_LABEL, TYPE_LABEL } from './ProductCustomersPage';
import styles from '../../components/CustomerList.module.css'; // same drawer styling as the GSTR2Tally / PRN customer drawers

const D = (v) => (v ? formatDate(v) : '—');
const DT = (v) => (v ? formatDateTime(v) : '—');
const EMAIL = { accepted: 'Sent (accepted by mail server)', failed: 'Failed – use Resend Credentials', not_delivered: 'Not delivered (no mail server)', '': 'Not sent yet' };

function Group({ title, rows }) {
  return (
    <section className={styles.group}>
      <h3 className={styles.groupTitle}>{title}</h3>
      <dl className={styles.details}>{rows.map(([l, v]) => <div key={l} className={styles.detail}><dt>{l}</dt><dd>{v}</dd></div>)}</dl>
    </section>
  );
}

function History({ title, columns, rows, empty }) {
  const [open, setOpen] = useState(false);
  return (
    <section className={styles.group}>
      <h3 className={styles.groupTitle} style={{ margin: 0 }}>
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} style={{ all: 'unset', display: 'flex', width: '100%', justifyContent: 'space-between', cursor: 'pointer', font: 'inherit', color: 'inherit' }}>
          <span>{title}{rows.length ? ` (${rows.length})` : ''}</span><span aria-hidden="true">{open ? '▾' : '▸'}</span>
        </button>
      </h3>
      {open && (rows.length ? (
        <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
          <thead><tr>{columns.map((c) => <th key={c} style={{ textAlign: 'left', padding: '4px 6px' }}>{c}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} style={{ padding: '4px 6px', borderTop: '1px solid var(--border)' }}>{v}</td>)}</tr>)}</tbody>
        </table>
      ) : <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>{empty}</p>)}
    </section>
  );
}

function Confirm({ wide, title, children, confirmLabel, danger, busy, error, onConfirm, onClose }) {
  return (
    <Modal title={title} onClose={onClose} dialogClass={wide ? styles.activateWide : undefined}>
      <form onSubmit={(e) => { e.preventDefault(); onConfirm(); }} noValidate>
        {children}
        {error && <Alert type="error">{error}</Alert>}
        <div className={styles.dialogActions}>
          <button type="submit" className={danger ? styles.dangerSolid : styles.primary} disabled={busy}>{busy ? 'Working…' : confirmLabel}</button>
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}

const whole = (v) => /^[1-9]\d*$/.test(String(v));
const field = { display: 'block', width: '100%', height: 34, marginTop: 3 };
const lbl = { display: 'block', fontSize: 12.5, fontWeight: 600, marginTop: 8 };

/** Customer Details (View More) for an API-connected product: details, licence actions and the three histories. */
export default function ProductCustomerDrawer({ code, id, onClose, onChanged, onNotify }) {
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState('');
  const [dlgError, setDlgError] = useState('');
  const [form, setForm] = useState({});
  const [days, setDays] = useState(3);
  const [plan, setPlan] = useState('TRIAL');
  const [years, setYears] = useState(1);
  const [customDays, setCustomDays] = useState(30);
  const [paid, setPaid] = useState(false);
  const [customUnit, setCustomUnit] = useState('days');
  const menuRef = useRef(null);

  const load = useCallback(() => integrationsApi.productCustomer(code, id).then((data) => setState({ loading: false, error: '', data }), (e) => setState({ loading: false, error: e.message, data: null })), [code, id]);
  useEffect(() => { setState({ loading: true, error: '', data: null }); load(); }, [load]);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const outside = (e) => !menuRef.current?.contains(e.target) && setMenuOpen(false);
    document.addEventListener('mousedown', outside);
    return () => document.removeEventListener('mousedown', outside);
  }, [menuOpen]);

  const open = (kind) => { setMenuOpen(false); setDlgError(''); setDialog({ kind }); if (kind === 'edit' && state.data) { const c = state.data.customer; setForm({ customer_name: c.name, email: c.email, contact_number: c.contact_number }); } if (kind === 'extend') setDays(3); if (kind === 'set') setPlan('TRIAL'); if (kind === 'activate') { setPlan('TRIAL'); setDays(3); setYears(1); setCustomDays(30); setPaid(Boolean(state.data?.license?.paid)); setCustomUnit('days'); } };
  const close = () => { if (!busy) setDialog(null); };
  async function run(name, fn, ok) {
    setBusy(name); setDlgError('');
    try { await fn(); onNotify('success', ok); setDialog(null); onChanged?.(); await load(); } catch (e) { setDlgError(e.message); } finally { setBusy(''); }
  }
  const act = (a, body) => () => integrationsApi.productAction(code, id, a, body);

  let body = null;
  let footer = null;
  if (state.loading) body = <p style={{ padding: 16 }}>Loading…</p>;
  else if (state.error) body = <Alert type="error">{state.error}</Alert>;
  else {
    const { customer: c, license: l, activations, transfers, usage } = state.data;
    const type = c.license_type;
    body = (
      <>
        <Group title="Customer Details" rows={[['Customer Name', c.name], ['Email', c.email], ['Contact Number', c.contact_number], ['External Customer ID', c.external_customer_id], ['Serial Number / User ID', c.serial_number], ['Registered On', DT(c.created_at)], ['Welcome Email', EMAIL[c.welcome_email_status] ?? c.welcome_email_status], ['Last Login', DT(c.last_login)]]} />
        <Group title="License Details" rows={[['License Type', TYPE_LABEL[type]], ['License Status', STATUS_LABEL[c.status]], ...(type === 'TRIAL' ? [['Trial Days', c.trial_days ?? '—']] : []), ['License Start Date', D(c.starts_at)], ['Activation Date', D(c.activated_at)], ['Expiry Date', type === 'LIFETIME' ? 'Never' : D(c.expires_at)], ['Payment Status', l?.paid ? 'Paid' : 'Unpaid'], ['Active Device', c.active_device || '—'], ['Usage Category', c.usage_category || '—']]} />
        <History title="Device Activation History" columns={['Device', 'Activated', 'Status', 'Ended']} rows={activations.map((a) => [a.device_name, DT(a.activated_at), a.status === 'ACTIVE' ? 'Active' : 'Ended', a.ended_at ? `${DT(a.ended_at)} (${a.ended_reason.replace('_', ' ')})` : '—'])} empty="No device has been activated." />
        <History title="Device Transfer History" columns={['When', 'Previous device', 'New device']} rows={transfers.map((t) => [DT(t.at), t.from_device, t.to_device])} empty="No device transfers." />
        <History title="Usage History" columns={['When', 'Event', 'Device']} rows={usage.map((u) => [DT(u.at), u.event, u.device])} empty="No usage recorded yet." />
        {dialog?.kind === 'edit' && (
          <Modal title="Edit Customer" onClose={close}>
            <form onSubmit={(e) => { e.preventDefault(); run('edit', () => integrationsApi.editProductCustomer(code, id, form), 'Customer updated.'); }} noValidate>
              {[['customer_name', 'Customer Name'], ['email', 'Email'], ['contact_number', 'Contact Number']].map(([k, label]) => (
                <label key={k} style={lbl}>{label}<input style={field} value={form[k] || ''} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></label>
              ))}
              <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>The Serial Number / User ID is generated by Nexus and cannot be changed.</p>
              {dlgError && <Alert type="error">{dlgError}</Alert>}
              <div className={styles.dialogActions}><button type="submit" className={styles.primary} disabled={busy === 'edit'}>{busy === 'edit' ? 'Saving…' : 'Save Changes'}</button><button type="button" onClick={close}>Cancel</button></div>
            </form>
          </Modal>
        )}
        {dialog?.kind === 'extend' && (
          <Confirm title="Extend Trial" confirmLabel="Extend Trial" busy={busy === 'extend'} error={dlgError} onClose={close} onConfirm={() => run('extend', act('extend-trial', { days: Number(days) }), `Trial extended by ${days} day(s).`)}>
            <label style={lbl}>Days to add<input type="number" min="1" max="365" style={field} value={days} onChange={(e) => setDays(e.target.value)} /></label>
          </Confirm>
        )}
        {dialog?.kind === 'cancel' && (
          <Confirm title="Cancel Trial" danger confirmLabel="Yes, cancel trial" busy={busy === 'cancel'} error={dlgError} onClose={close} onConfirm={() => run('cancel', act('cancel-trial'), 'Trial cancelled; the device session was revoked.')}>
            <p className={styles.message}>Cancel {c.name}'s trial? The application stops working for them immediately.</p>
          </Confirm>
        )}
        {dialog?.kind === 'renew' && (
          <Confirm title="Renew License" confirmLabel="Renew for one year" busy={busy === 'renew'} error={dlgError} onClose={close} onConfirm={() => run('renew', act('renew'), 'License renewed for one year.')}>
            <p className={styles.message}>Renew {c.name}'s 1 Year license for one more year?</p>
          </Confirm>
        )}
        {dialog?.kind === 'set' && (
          <Confirm title={c.status === 'pending' ? 'Approve / Set License' : 'Change License Type'} confirmLabel="Apply" busy={busy === 'set'} error={dlgError} onClose={close} onConfirm={() => run('set', act('set-license', { license_type: plan, trial_days: plan === 'TRIAL' ? Number(days) : undefined }), 'License updated.')}>
            <label style={lbl}>License Type<select style={field} value={plan} onChange={(e) => setPlan(e.target.value)}><option value="TRIAL">Trial</option><option value="ONE_YEAR">1 Year</option><option value="LIFETIME">Lifetime</option></select></label>
            {plan === 'TRIAL' && <label style={lbl}>Trial Days<input type="number" min="1" max="365" style={field} value={days} onChange={(e) => setDays(e.target.value)} /></label>}
            <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>The licence becomes active immediately, starting now.</p>
          </Confirm>
        )}
        {dialog?.kind === 'activate' && (() => {
          const isTrial = plan === 'TRIAL';
          const extending = isTrial && type === 'TRIAL' && ['active', 'not_activated', 'expired'].includes(c.status); // an existing trial: days are ADDED
          const addUnit = (base, k, unit) => { const d = new Date(base); if (unit === 'years') d.setFullYear(d.getFullYear() + k); else if (unit === 'months') d.setMonth(d.getMonth() + k); else d.setDate(d.getDate() + k); return d; };
          const n = isTrial ? days : plan === 'ONE_YEAR' ? years : customDays;
          const k = whole(n) ? Number(n) : null;
          const baseDate = extending && c.expires_at && new Date(c.expires_at) > new Date() ? new Date(c.expires_at) : new Date();
          let exp;
          if (plan === 'LIFETIME') exp = null;
          else if (k === null) exp = undefined;
          else if (isTrial) exp = addUnit(baseDate, k, 'days');
          else if (plan === 'ONE_YEAR') exp = addUnit(new Date(), k, 'years');
          else exp = addUnit(new Date(), k, customUnit);
          const needsPaid = !isTrial && !paid;
          const valid = exp !== undefined && !needsPaid && Boolean(c.serial_number);
          const g = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 12px' };
          const submit = () => {
            if (!valid) return setDlgError(needsPaid ? 'Tick "Paid — Payment Confirmed" before activating a paid license.' : 'Enter a valid positive whole number.');
            if (extending) return run('activate', act('extend-trial', { days: k }), `Trial extended by ${k} day(s).`);
            const body = { activation_form: true, license_type: plan, paid,
              trial_days: isTrial ? k : undefined, years: plan === 'ONE_YEAR' ? k : undefined,
              ...(plan === 'CUSTOM' ? { custom_expiry_date: exp.toISOString().slice(0, 10) } : {}) }; // no password is ever sent: credentials never change here
            return run('activate', act('set-license', body), 'License activated. The customer keeps the same User ID and password.');
          };
          return (
            <Confirm wide title="Activate Customer License" confirmLabel="Save & Activate" busy={busy === 'activate'} error={dlgError} onClose={close} onConfirm={submit}>
              <div style={g}>
                <label style={lbl}>Customer Name<input style={field} value={c.name} readOnly /></label>
                <label style={lbl}>Email<input style={field} value={c.email} readOnly /></label>
                <label style={lbl}>Serial Number / User ID<input style={field} value={c.serial_number || 'Unavailable'} readOnly /></label>
                <label style={lbl}>Activation Key<input style={field} value="Existing key kept; generated internally" readOnly /></label>
              </div>
              <label style={{ ...lbl, display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={paid} onChange={(e) => setPaid(e.target.checked)} />Paid — Payment Confirmed</label>
              <div style={g}>
                <label style={lbl}>License Type<select style={field} value={plan} onChange={(e) => setPlan(e.target.value)}><option value="TRIAL">Trial</option><option value="LIFETIME">Lifetime</option><option value="ONE_YEAR">Years</option><option value="CUSTOM">Custom</option></select></label>
                {plan === 'LIFETIME' ? <label style={lbl}>Validity<input style={field} value="No expiry" readOnly /></label>
                  : plan === 'CUSTOM' ? (
                    <label style={lbl}>Duration
                      <span style={{ display: 'flex', gap: 6 }}>
                        <input type="number" min="1" step="1" style={{ ...field, flex: 1 }} value={customDays} onChange={(e) => setCustomDays(e.target.value)} />
                        <select style={{ ...field, width: 100 }} value={customUnit} onChange={(e) => setCustomUnit(e.target.value)} aria-label="Duration unit"><option value="days">Days</option><option value="months">Months</option><option value="years">Years</option></select>
                      </span>
                    </label>
                  ) : <label style={lbl}>{isTrial ? (extending ? 'Additional Trial Days' : 'Trial Days') : 'Number of Years'}<input type="number" min="1" step="1" style={field} value={n} onChange={(e) => (isTrial ? setDays(e.target.value) : setYears(e.target.value))} /></label>}
                <label style={lbl}>License Start Date<input style={field} value={formatDate(new Date().toISOString())} readOnly /></label>
                <label style={lbl}>Expiry Date<input style={field} value={exp === null ? 'No expiry' : exp === undefined ? 'Enter a valid number' : formatDate(exp.toISOString())} readOnly /></label>
              </div>
            </Confirm>
          );
        })()}
        {dialog?.kind === 'inactivate' && (
          <Confirm title="Inactivate" danger confirmLabel="Yes, inactivate" busy={busy === 'inactivate'} error={dlgError} onClose={close} onConfirm={() => run('inactivate', act('inactivate'), 'Customer inactivated.')}>
            <p className={styles.message}>Inactivate {c.name}? Access stops immediately and the device session is revoked.</p>
          </Confirm>
        )}
        {dialog?.kind === 'reactivate' && (
          <Confirm title="Reactivate" confirmLabel="Reactivate" busy={busy === 'reactivate'} error={dlgError} onClose={close} onConfirm={() => run('reactivate', act('reactivate'), 'Customer reactivated.')}>
            <p className={styles.message}>Reactivate {c.name}? They must use Activate User to sign in on a device again.</p>
          </Confirm>
        )}
        {dialog?.kind === 'resend' && (
          <Confirm title="Resend Credentials" confirmLabel="Send new credentials" busy={busy === 'resend'} error={dlgError} onClose={close} onConfirm={() => run('resend', act('resend-credentials'), 'New credentials emailed.')}>
            <p className={styles.message}>Email {c.name} a NEW temporary password? The previous one stops working.</p>
          </Confirm>
        )}
      </>
    );
    const items = [
      type === 'TRIAL' && c.status !== 'cancelled' && c.status !== 'pending' && { key: 'extend', label: 'Extend Trial' },
      type === 'TRIAL' && ['active', 'not_activated'].includes(c.status) && { key: 'cancel', label: 'Cancel Trial', danger: true },
      type === 'ONE_YEAR' && ['active', 'not_activated', 'expired'].includes(c.status) && { key: 'renew', label: 'Renew License' },
      c.status !== 'inactive' && { key: 'set', label: c.status === 'pending' ? 'Approve License' : 'Change License Type' },
      c.status !== 'inactive' && { key: 'resend', label: 'Resend Credentials' },
      c.status !== 'inactive' ? { key: 'inactivate', label: 'Inactivate', danger: true } : { key: 'reactivate', label: 'Reactivate' },
    ].filter(Boolean);
    footer = (
      <div className={styles.licenseBar}>
        {['not_activated', 'pending'].includes(c.status) && <button type="button" className={styles.primary} onClick={() => open('activate')}>Save &amp; Activate</button>}
        <button type="button" onClick={() => open('edit')}>Save Changes</button>
        <div className={styles.menuWrap} ref={menuRef}>
          <button type="button" className={styles.menuButton} aria-haspopup="menu" aria-expanded={menuOpen} aria-label="More actions" onClick={() => setMenuOpen((o) => !o)}>⋮</button>
          <div className={menuOpen ? styles.menuPanel : styles.menuClosed} role="menu">
            {items.map((a) => <button key={a.key} type="button" role="menuitem" className={a.danger ? styles.danger : undefined} onClick={() => open(a.key)}>{a.label}</button>)}
          </div>
        </div>
      </div>
    );
  }
  return (
    <Drawer open onClose={onClose} title="Customer Details" titleId="product-customer-details-title" flush>
      <div className={styles.drawerBody}><div className={styles.drawerScroll}>{body}</div>{footer}</div>
    </Drawer>
  );
}
