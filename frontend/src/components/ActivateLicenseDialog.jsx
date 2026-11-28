import { useState } from 'react';
import Alert from './Alert';
import Modal from './Modal';
import { formatDate } from '../utils/date';
import styles from './CustomerList.module.css';

const whole = (v) => /^[1-9]\d*$/.test(String(v).trim());
const field = { display: 'block', width: '100%', height: 34, marginTop: 3, boxSizing: 'border-box' };
const lbl = { display: 'block', fontSize: 12.5, fontWeight: 600, marginTop: 8 };
const grid = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 12px' };

const addUnit = (base, k, unit) => {
  const d = new Date(base);
  if (unit === 'years') d.setFullYear(d.getFullYear() + k);
  else if (unit === 'months') d.setMonth(d.getMonth() + k);
  else d.setDate(d.getDate() + k);
  return d;
};

/** The one "Activate Customer License" popup, shared by Mothys, GSTR2Tally and UNIVERSE customers.
 *  Props: customer {name, email, serial}; extending (an existing trial: days are ADDED to it); currentExpiry; initialPaid;
 *  busy; error; onClose; onSave(payload). payload = { plan: TRIAL|LIFETIME|YEARS|CUSTOM, paid, extending, days, years, customDays, customDate }.
 *  Each project passes its own adapter as onSave. No password is edited or sent: license changes never touch credentials. */
export default function ActivateLicenseDialog({ customer, extending = false, currentExpiry, initialPaid = false, busy, error, onClose, onSave }) {
  const [plan, setPlan] = useState('TRIAL');
  const [paid, setPaid] = useState(Boolean(initialPaid));
  const [num, setNum] = useState({ TRIAL: '3', YEARS: '1', CUSTOM: '30' });
  const [unit, setUnit] = useState('days');
  const [localError, setLocalError] = useState('');
  const isTrial = plan === 'TRIAL';
  const n = num[plan];
  const k = whole(n) ? Number(n) : null;
  const base = extending && currentExpiry && new Date(currentExpiry) > new Date() ? new Date(currentExpiry) : new Date();
  let exp;
  if (plan === 'LIFETIME') exp = null;
  else if (k === null) exp = undefined;
  else if (isTrial) exp = addUnit(base, k, 'days');
  else if (plan === 'YEARS') exp = addUnit(new Date(), k, 'years');
  else exp = addUnit(new Date(), k, unit);
  const needsPaid = !isTrial && !paid;
  const submit = () => {
    if (!customer.serial) return setLocalError('A serial number / User ID is required.');
    if (needsPaid) return setLocalError('Tick "Paid — Payment Confirmed" before activating a paid license.');
    if (exp === undefined) return setLocalError('Enter a valid positive whole number.');
    setLocalError('');
    return onSave({
      plan, paid, extending: isTrial && extending,
      days: isTrial ? k : undefined, years: plan === 'YEARS' ? k : undefined,
      customDays: plan === 'CUSTOM' ? Math.max(1, Math.round((exp.getTime() - Date.now()) / 86400000)) : undefined,
      customDate: plan === 'CUSTOM' ? exp.toISOString().slice(0, 10) : undefined,
    });
  };
  const shown = localError || error;
  return (
    <Modal title="Activate Customer License" onClose={() => !busy && onClose()} dialogClass={styles.activateWide}>
      <form onSubmit={(e) => { e.preventDefault(); submit(); }} noValidate>
        <div style={grid}>
          <label style={lbl}>Customer Name<input style={field} value={customer.name || ''} readOnly /></label>
          <label style={lbl}>Email<input style={field} value={customer.email || ''} readOnly /></label>
          <label style={lbl}>Serial Number / User ID<input style={field} value={customer.serial || 'Unavailable'} readOnly /></label>
          <label style={lbl}>Activation Key<input style={field} value="Existing key kept; generated internally" readOnly /></label>
        </div>
        <label style={{ ...lbl, display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={paid} onChange={(e) => setPaid(e.target.checked)} />Paid — Payment Confirmed</label>
        <div style={grid}>
          <label style={lbl}>License Type
            <select style={field} value={plan} onChange={(e) => setPlan(e.target.value)}>
              <option value="TRIAL">Trial</option><option value="LIFETIME">Lifetime</option><option value="YEARS">Years</option><option value="CUSTOM">Custom</option>
            </select>
          </label>
          {plan === 'LIFETIME' ? <label style={lbl}>Validity<input style={field} value="No expiry" readOnly /></label>
            : plan === 'CUSTOM' ? (
              <label style={lbl}>Duration
                <span style={{ display: 'flex', gap: 6 }}>
                  <input type="number" min="1" step="1" style={{ ...field, flex: 1 }} value={n} onChange={(e) => setNum((m) => ({ ...m, CUSTOM: e.target.value }))} />
                  <select style={{ ...field, width: 100 }} value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="Duration unit"><option value="days">Days</option><option value="months">Months</option><option value="years">Years</option></select>
                </span>
              </label>
            ) : (
              <label style={lbl}>{isTrial ? (extending ? 'Additional Trial Days' : 'Trial Days') : 'Number of Years'}
                <input type="number" min="1" step="1" style={field} value={n} onChange={(e) => setNum((m) => ({ ...m, [plan]: e.target.value }))} />
              </label>
            )}
          <label style={lbl}>License Start Date<input style={field} value={formatDate(new Date().toISOString())} readOnly /></label>
          <label style={lbl}>Expiry Date<input style={field} value={exp === null ? 'No expiry' : exp === undefined ? 'Enter a valid number' : formatDate(exp.toISOString())} readOnly /></label>
        </div>
        {shown && <Alert type="error">{shown}</Alert>}
        <div className={styles.dialogActions}>
          <button type="submit" className={styles.primary} disabled={busy}>{busy ? 'Working…' : 'Save & Activate'}</button>
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
