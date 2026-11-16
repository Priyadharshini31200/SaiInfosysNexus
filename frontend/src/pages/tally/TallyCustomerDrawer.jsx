import { useCallback, useEffect, useRef, useState } from 'react';
import { tallyApi } from '../../api/tallysl';
import Alert from '../../components/Alert';
import Drawer from '../../components/Drawer';
import Modal from '../../components/Modal';
import { useAuth } from '../../context/AuthContext';
import { useTallyAccess } from '../../context/TallyAccessContext';
import { formatDate, formatDateTime } from '../../utils/date';
import { PLAN_LABELS, STATE_LABELS } from './TallyShared';
import styles from '../../components/CustomerList.module.css'; // the PRN Customer Details drawer styling, reused unchanged

const NA = 'Not Available';
const TALLY_FIELDS = [
  ['tally_serial', 'Tally Serial Number'], ['edition', 'Tally Edition'], ['license_administrator', 'License Administrator'],
  ['tss_details', 'Tally Software Services'], ['registered_email', 'Registered Tally Email'], ['tss_expiry_date', 'Tally / TSS Expiry Date'],
];
const EMAIL_STATUS = { accepted: 'Sent (accepted by mail server)', failed: 'Failed - use Resend Credentials', '': 'Not sent yet' };

function Group({ title, rows }) {
  return (
    <section className={styles.group}>
      <h3 className={styles.groupTitle}>{title}</h3>
      <dl className={styles.details}>
        {rows.map(([label, value]) => (
          <div key={label} className={styles.detail}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** A section whose body is hidden until its heading is clicked (closed by default). */
function Collapse({ title, count, open, onToggle, id, children }) {
  return (
    <section className={styles.group}>
      <h3 className={styles.groupTitle} style={{ margin: 0 }}>
        <button
          type="button" id={`${id}-btn`} aria-expanded={open} aria-controls={id} onClick={onToggle}
          style={{ all: 'unset', boxSizing: 'border-box', display: 'flex', width: '100%', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', font: 'inherit', letterSpacing: 'inherit', textTransform: 'inherit', color: 'inherit' }}
        >
          <span>{title}{count ? ` (${count})` : ''}</span>
          <span aria-hidden="true" style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }}>▸</span>
        </button>
      </h3>
      <div id={id} role="region" aria-labelledby={`${id}-btn`} hidden={!open}>{open && children}</div>
    </section>
  );
}

function Confirm({ title, children, confirmLabel, danger, busy, error, onConfirm, onClose }) {
  return (
    <Modal title={title} onClose={onClose}>
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

const small = { margin: '4px 0 0', fontSize: 12.5, color: 'var(--muted)' };

/**
 * Right-side Customer Details drawer for one GSTR2Tally (Tally SL) customer - same layout as the Barcode AutoPrint drawer:
 * scrolling grouped details above a fixed footer holding Edit, Delete and the three-dot menu. Device Transfer History and
 * Audit History are collapsible and closed by default (Device Transfer History opens itself when a notification points at a transfer).
 */
export default function TallyCustomerDrawer({ product, id, highlight = 0, onClose, onChanged, onDeleted, onNotify }) {
  const { user } = useAuth();
  const { can } = useTallyAccess();
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState('');
  const [dlgError, setDlgError] = useState('');
  const [form, setForm] = useState({});
  const [days, setDays] = useState(3);
  const [plan, setPlan] = useState('TRIAL');
  const [issued, setIssued] = useState({ key: '', password: '' });
  const [openHist, setOpenHist] = useState({ transfers: Boolean(highlight), audit: false });
  const menuRef = useRef(null);
  const superadmin = user?.role === 'SUPERADMIN';

  const load = useCallback(() => tallyApi.customer(product, id).then((data) => setState({ loading: false, error: '', data }))
    .catch((err) => setState({ loading: false, error: err.message, data: null })), [product, id]);
  useEffect(() => { setState({ loading: true, error: '', data: null }); setIssued({ key: '', password: '' }); setOpenHist({ transfers: Boolean(highlight), audit: false }); load(); }, [load, highlight]);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const outside = (e) => !menuRef.current?.contains(e.target) && setMenuOpen(false);
    const esc = (e) => { if (e.key === 'Escape') { e.stopPropagation(); setMenuOpen(false); } };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', esc, true);
    return () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', esc, true); };
  }, [menuOpen]);

  const closeDialog = () => { setDialog(null); setDlgError(''); setBusy(''); };
  async function run(label, fn, success) {
    setBusy(label);
    setDlgError('');
    try {
      const result = await fn();
      if (result?.activation_key) setIssued((i) => ({ ...i, key: result.activation_key }));
      if (result?.temporary_password) setIssued((i) => ({ ...i, password: result.temporary_password }));
      const failedMail = result?.credentials_email && result.credentials_email !== 'submitted';
      onNotify(failedMail ? 'error' : 'success', typeof success === 'function' ? success(result) : success);
      await load();
      onChanged?.();
      closeDialog();
    } catch (err) {
      setDlgError(err.errors ? Object.values(err.errors).flat()[0] || err.message : err.message);
      setBusy('');
    }
  }

  const { loading, error, data } = state;
  let body;
  let footer = null;
  if (loading) body = <p style={{ padding: 20 }}>Loading…</p>;
  else if (error) body = <p role="alert" style={{ padding: 20 }}>{error}</p>;
  else {
    const { customer: c, tally, license, devices, device_summary: ds, transfer_history: xfers, activation_history: history } = data;
    const isTrial = license.plan === 'TRIAL';
    const inactive = license.state === 'inactive';
    const act = (a, b) => () => tallyApi.licenseAction(product, id, a, b);
    const mgmt = can('license_management', product);
    const items = [
      can('license_activation', product) && !inactive && (license.state === 'pending' || license.state === 'expired' || license.state === 'cancelled') && { key: 'activate', label: 'Activate License' },
      mgmt && isTrial && !inactive && { key: 'extend', label: 'Extend Trial' },
      mgmt && isTrial && license.state !== 'cancelled' && !inactive && { key: 'cancel', label: 'Cancel Trial', danger: true },
      mgmt && license.plan === 'ANNUAL' && !inactive && { key: 'renew', label: 'Renew License' },
      mgmt && !inactive && { key: 'inactivate', label: 'Inactivate', danger: true },
      can('entry', product) && !inactive && !c.password_changed_at && { key: 'resend', label: 'Resend Credentials' },
      superadmin && can('edit', product) && !inactive && { key: 'reset', label: 'Reset Password', danger: true },
      mgmt && !inactive && license.state !== 'pending' && { key: 'reissue', label: 'Reissue Key', danger: true },
    ].filter(Boolean);
    const open = (kind) => {
      setMenuOpen(false); setDlgError('');
      if (kind === 'edit') setForm({ name: c.name, email: c.email, contact_number: c.contact_number, company_name: c.company_name || '', gstin: c.gstin || '' });
      if (kind === 'extend' || kind === 'activate') { setDays(3); setPlan('TRIAL'); }
      setDialog({ kind });
    };
    const credMsg = (r) => (r.credentials_email === 'submitted' ? `New credentials were emailed to ${r.email_sent_to}.` : 'The email could not be sent. The temporary password is shown in the drawer once.');
    const active = devices.find((d) => d.active);
    const tallyValue = (k) => {
      const v = k === 'tss_expiry_date' ? (tally[k] ? formatDate(tally[k]) : '') : tally[k];
      return v !== '' && v !== null && v !== undefined ? String(v) : NA;
    };
    const total = xfers.length + data.transfers.filter((t) => t.kind === 'REPLACED').length;

    body = (
      <>
        {(issued.key || issued.password) && (
          <p role="status" style={{ margin: 0, padding: '10px 20px', background: 'var(--navy-50)', fontSize: 12.5 }}>
            {issued.key && <>New Activation Key (shown once, <strong>not emailed</strong> - the customer gets a key only through Activate User): <strong>{issued.key}</strong> </>}
            {issued.password && <>Credentials email failed. Temporary password (shown once): <strong>{issued.password}</strong></>}
          </p>
        )}
        <Group title="Customer Details" rows={[
          ['Customer Name', c.name], ['Company Name', c.company_name || NA], ['Contact Number', c.contact_number],
          ['Email', c.email], ['GSTIN', c.gstin || NA], ['Registration Date', formatDateTime(c.registered_at)],
        ]} />
        <Group title="Login Details" rows={[
          ['User ID (Tally Serial Number)', c.user_id], ['Password Status', c.password_status],
          ['Password Last Changed', c.password_changed_at ? formatDateTime(c.password_changed_at) : 'Not changed yet'],
          ['Credentials Email Status', EMAIL_STATUS[c.credentials_email_status] ?? c.credentials_email_status],
        ]} />
        <Group title="License Details" rows={[
          ['License Type', license.plan === 'ANNUAL' ? '1 Year' : PLAN_LABELS[license.plan] || license.plan],
          ['Activation Key', license.activation_key_suffix ? `••••${license.activation_key_suffix}` : NA],
          ['Trial Days', license.trial_days || NA], ['Activation Date', license.activated_at ? formatDate(license.activated_at) : NA],
          ['Expiry Date', license.plan === 'LIFETIME' ? 'Never (Lifetime)' : license.expires_at ? formatDate(license.expires_at) : NA],
          ['License Status', STATE_LABELS[license.state] || license.state],
        ]} />
        <Group title="Tally Information" rows={TALLY_FIELDS.map(([k, label]) => [label, tallyValue(k)])} />
        <Group title="Device Management" rows={[
          ['Registered Device', ds.current || NA], ['Activation Date', ds.current_activated_at ? formatDateTime(ds.current_activated_at) : NA],
          ['Last Used Date', active?.last_seen_at ? formatDateTime(active.last_seen_at) : NA], ['Previous Device', ds.previous || NA],
        ]} />
        {can('device_management', product) && active && (
          <div style={{ padding: '0 20px 12px' }}><button type="button" onClick={() => setDialog({ kind: 'revoke' })} style={{ height: 30, padding: '0 12px', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', color: 'var(--danger)', fontWeight: 600, fontSize: 12.5, cursor: 'pointer' }}>Revoke Current Device</button></div>
        )}
        <Collapse id="tally-xfer" title="Device Transfer History" count={total} open={openHist.transfers} onToggle={() => setOpenHist((h) => ({ ...h, transfers: !h.transfers }))}>
          <p style={small}>Confirmed by the customer in GSTR2Tally with their Activation Key. Read-only.</p>
          {total === 0 ? <p style={small}>No device transfers recorded.</p> : (
            <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0 }}>
              {xfers.map((t) => (
                <li key={`e${t.id}`} aria-current={t.id === highlight ? 'true' : undefined} ref={t.id === highlight ? (el) => el?.scrollIntoView?.({ block: 'center' }) : undefined}
                  style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)', fontSize: 12.5, background: t.id === highlight ? '#fff7d6' : undefined }}>
                  <strong>{t.old_device || '—'} → {t.new_device}</strong><br />{formatDateTime(t.at)} · by {t.initiated_by} · Completed
                </li>
              ))}
              {data.transfers.filter((t) => t.kind === 'REPLACED').map((t, i) => (
                <li key={`l${i}`} style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
                  <strong>{t.from || '—'} → {t.to || '—'}</strong><br />{formatDateTime(t.at)} · by {t.by} · Completed
                </li>
              ))}
            </ul>
          )}
        </Collapse>
        <Collapse id="tally-audit" title="Audit History" count={history.length} open={openHist.audit} onToggle={() => setOpenHist((h) => ({ ...h, audit: !h.audit }))}>
          {history.length === 0 ? <p style={small}>No audit entries.</p> : (
            <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0 }}>
              {history.map((h) => (
                <li key={h.id} style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
                  <strong>{h.action}</strong>{h.detail ? ` - ${h.detail}` : ''}<br />{formatDateTime(h.at)} · {h.actor}
                </li>
              ))}
            </ul>
          )}
        </Collapse>

        {dialog?.kind === 'edit' && (
          <Modal title="Edit Customer" onClose={closeDialog}>
            <form onSubmit={(e) => { e.preventDefault(); run('edit', () => tallyApi.editCustomer(product, id, form), 'Customer updated.'); }} noValidate>
              {[['name', 'Customer Name'], ['company_name', 'Company Name'], ['contact_number', 'Contact Number'], ['email', 'Email'], ['gstin', 'GSTIN']].map(([k, label]) => (
                <label key={k} style={{ display: 'block', marginBottom: 8, fontSize: 12.5, fontWeight: 600 }}>{label}
                  <input style={{ width: '100%', height: 34, padding: '0 8px', marginTop: 3 }} value={form[k] ?? ''} onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} />
                </label>
              ))}
              <p style={small}>The Tally Serial Number / User ID cannot be changed.</p>
              {dlgError && <Alert type="error">{dlgError}</Alert>}
              <div className={styles.dialogActions}>
                <button type="submit" className={styles.primary} disabled={busy === 'edit'}>{busy === 'edit' ? 'Saving…' : 'Save'}</button>
                <button type="button" onClick={closeDialog} disabled={busy === 'edit'}>Cancel</button>
              </div>
            </form>
          </Modal>
        )}
        {dialog?.kind === 'delete' && (
          <Confirm title="Delete Customer" danger confirmLabel="Yes, delete customer" busy={busy === 'delete'} error={dlgError} onClose={closeDialog}
            onConfirm={async () => { setBusy('delete'); setDlgError(''); try { await tallyApi.deleteCustomer(product, id); onNotify('success', `${c.name} deleted.`); onDeleted?.(); } catch (err) { setDlgError(err.message); setBusy(''); } }}>
            <p className={styles.message}>Permanently delete {c.name} ({c.tally_serial})? The license and device records are removed with the customer; the audit history is kept. This cannot be undone.</p>
          </Confirm>
        )}
        {dialog?.kind === 'activate' && (
          <Confirm title="Activate License" confirmLabel="Activate" busy={busy === 'activate'} error={dlgError} onClose={closeDialog}
            onConfirm={() => run('activate', act('activate', { license_plan: plan, trial_days: plan === 'TRIAL' ? Number(days) : undefined }), 'License activated. The customer receives the Activation Key only when they use Activate User.')}>
            <label style={{ display: 'block', fontSize: 12.5, fontWeight: 600 }}>License Type
              <select style={{ display: 'block', width: '100%', height: 34, marginTop: 3 }} value={plan} onChange={(e) => setPlan(e.target.value)}>
                <option value="TRIAL">Trial</option><option value="ANNUAL">1 Year</option><option value="LIFETIME">Lifetime</option>
              </select>
            </label>
            {plan === 'TRIAL' && (
              <label style={{ display: 'block', marginTop: 8, fontSize: 12.5, fontWeight: 600 }}>Trial Days
                <input type="number" min="1" max="365" style={{ display: 'block', width: '100%', height: 34, marginTop: 3 }} value={days} onChange={(e) => setDays(e.target.value)} />
              </label>
            )}
          </Confirm>
        )}
        {dialog?.kind === 'extend' && (
          <Confirm title="Extend Trial" confirmLabel="Extend Trial" busy={busy === 'extend'} error={dlgError} onClose={closeDialog}
            onConfirm={() => run('extend', act('extend-trial', { days: Number(days) }), `Trial extended by ${days} day(s).`)}>
            <label style={{ display: 'block', fontSize: 12.5, fontWeight: 600 }}>Days to add
              <input type="number" min="1" max="365" autoFocus style={{ display: 'block', width: '100%', height: 34, marginTop: 3 }} value={days} onChange={(e) => setDays(e.target.value)} />
            </label>
          </Confirm>
        )}
        {dialog?.kind === 'cancel' && (
          <Confirm title="Cancel Trial" danger confirmLabel="Yes, cancel trial" busy={busy === 'cancel'} error={dlgError} onClose={closeDialog} onConfirm={() => run('cancel', act('cancel-trial'), 'Trial cancelled.')}>
            <p className={styles.message}>Cancel {c.name}'s trial? The application stops working for them immediately.</p>
          </Confirm>
        )}
        {dialog?.kind === 'renew' && (
          <Confirm title="Renew License" confirmLabel="Renew for one year" busy={busy === 'renew'} error={dlgError} onClose={closeDialog} onConfirm={() => run('renew', act('renew'), 'License renewed for one year.')}>
            <p className={styles.message}>Renew {c.name}'s 1 Year license?</p>
          </Confirm>
        )}
        {dialog?.kind === 'inactivate' && (
          <Confirm title="Inactivate License" danger confirmLabel="Yes, inactivate" busy={busy === 'inactivate'} error={dlgError} onClose={closeDialog} onConfirm={() => run('inactivate', act('inactivate'), 'License inactivated.')}>
            <p className={styles.message}>Inactivate {c.name}'s license? Access stops immediately.</p>
          </Confirm>
        )}
        {dialog?.kind === 'resend' && (
          <Confirm title="Resend Credentials" confirmLabel="Send new credentials" busy={busy === 'resend'} error={dlgError} onClose={closeDialog} onConfirm={() => run('resend', () => tallyApi.credentials(product, id, 'resend'), credMsg)}>
            <p className={styles.message}>Email {c.name} a NEW temporary password at the registered address? The previous password stops working.</p>
          </Confirm>
        )}
        {dialog?.kind === 'reset' && (
          <Confirm title="Reset Password" danger confirmLabel="Yes, reset password" busy={busy === 'reset'} error={dlgError} onClose={closeDialog} onConfirm={() => run('reset', () => tallyApi.credentials(product, id, 'reset-password'), credMsg)}>
            <p className={styles.message}>Reset {c.name}'s password and email a new temporary password? Their current password and all sign-ins stop working.</p>
          </Confirm>
        )}
        {dialog?.kind === 'reissue' && (
          <Confirm title="Reissue Key" danger confirmLabel="Yes, reissue key" busy={busy === 'reissue'} error={dlgError} onClose={closeDialog} onConfirm={() => run('reissue', act('reissue-key'), 'A new Activation Key was generated. It was NOT emailed.')}>
            <p className={styles.message}>Generate a new Activation Key for {c.name}? The previous key stops working. It is not emailed; the customer receives a key only when they use Activate User.</p>
          </Confirm>
        )}
        {dialog?.kind === 'revoke' && (
          <Confirm title="Revoke Current Device" danger confirmLabel="Yes, revoke device" busy={busy === 'revoke'} error={dlgError} onClose={closeDialog} onConfirm={() => run('revoke', () => tallyApi.revokeDevice(product, id), 'Device revoked.')}>
            <p className={styles.message}>Sign out and revoke the current device? The customer must use Activate User again.</p>
          </Confirm>
        )}
      </>
    );
    footer = (
      <div className={styles.licenseBar}>
        {can('edit', product) && <button type="button" onClick={() => open('edit')}>Edit</button>}
        {can('delete', product) && <button type="button" className={styles.danger} onClick={() => open('delete')}>Delete</button>}
        {items.length > 0 && (
          <div className={styles.menuWrap} ref={menuRef}>
            <button type="button" className={styles.menuButton} aria-haspopup="menu" aria-expanded={menuOpen} aria-label="More actions" onClick={() => setMenuOpen((o) => !o)}>⋮</button>
            <div className={menuOpen ? styles.menuPanel : styles.menuClosed} role="menu">
              {items.map((a) => <button key={a.key} type="button" role="menuitem" className={a.danger ? styles.danger : undefined} onClick={() => open(a.key)}>{a.label}</button>)}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <Drawer open onClose={onClose} title="Customer Details" titleId="tally-customer-details-title" flush>
      <div className={styles.drawerBody}>
        <div className={styles.drawerScroll}>{body}</div>
        {footer}
      </div>
    </Drawer>
  );
}
