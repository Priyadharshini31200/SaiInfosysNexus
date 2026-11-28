import { useCallback, useEffect, useRef, useState } from 'react';
import { integrationsApi } from '../../api/integrations';
import Alert from '../../components/Alert';
import Drawer from '../../components/Drawer';
import ActivateLicenseDialog from '../../components/ActivateLicenseDialog';
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
        {dialog?.kind === 'activate' && (
          <ActivateLicenseDialog customer={{ name: c.name, email: c.email, serial: c.serial_number }} extending={type === 'TRIAL' && ['active', 'not_activated', 'expired'].includes(c.status)}
            currentExpiry={c.expires_at} initialPaid={Boolean(state.data?.license?.paid)} busy={busy === 'activate'} error={dlgError} onClose={close}
            onSave={(v) => (v.extending
              ? run('activate', act('extend-trial', { days: v.days }), `Trial extended by ${v.days} day(s).`)
              : run('activate', act('set-license', { activation_form: true, license_type: v.plan === 'YEARS' ? 'ONE_YEAR' : v.plan, paid: v.paid, trial_days: v.days, years: v.years, ...(v.plan === 'CUSTOM' ? { custom_expiry_date: v.customDate } : {}) }), 'License activated. The customer keeps the same User ID and password.'))} />
        )}
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
