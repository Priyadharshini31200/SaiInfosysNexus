import { useEffect, useRef, useState } from 'react';
import { companyApi } from '../api/company';
import Alert from './Alert';
import Button from './Button';
import { TextField } from './Field';
import { notify } from './TopToast';
import useCompany, { logoUrl, setCompany } from './useCompany';
import styles from './CompanyProfileSection.module.css';

const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const TYPES = ['image/png', 'image/jpeg', 'image/webp'];
const MAX = 2 * 1024 * 1024;

/** My Profile -> Company Profile: logo (upload / change / remove with a live preview), Company Name and optional GSTIN.
 *  Everyone signed in sees it; only users with edit rights (`can_edit`, enforced again by the API) can change it. */
export default function CompanyProfileSection() {
  const company = useCompany();
  const [name, setName] = useState('');
  const [gstin, setGstin] = useState('');
  const [file, setFile] = useState(null); // newly chosen logo, not saved yet
  const [preview, setPreview] = useState('');
  const [removed, setRemoved] = useState(false);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const input = useRef(null);
  const edit = company.can_edit;

  useEffect(() => { setName(company.name); setGstin(company.gstin); }, [company.name, company.gstin]);
  useEffect(() => () => preview && URL.revokeObjectURL(preview), [preview]);

  const current = removed ? '' : preview || logoUrl(company);
  function choose(e) {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f) return;
    if (!TYPES.includes(f.type)) return setErrors({ logo: 'Upload a PNG, JPG or WebP image.' });
    if (f.size > MAX) return setErrors({ logo: 'The logo must be 2 MB or smaller.' });
    setErrors({}); setFile(f); setRemoved(false); setPreview(URL.createObjectURL(f));
  }
  async function save(e) {
    e.preventDefault();
    const x = {};
    if (name.trim().length < 2) x.name = 'Company Name is required.';
    if (gstin.trim() && !GSTIN.test(gstin.trim().toUpperCase())) x.gstin = 'Enter a valid 15-character GSTIN';
    setErrors(x); setMessage('');
    if (Object.keys(x).length) return;
    const form = new FormData();
    form.append('name', name.trim());
    form.append('gstin', gstin.trim().toUpperCase());
    if (file) form.append('logo', file);
    else if (removed) form.append('remove_logo', 'true');
    setBusy(true);
    try {
      setCompany(await companyApi.save(form)); // the header updates immediately
      setFile(null); setPreview(''); setRemoved(false);
      notify('Company profile saved.');
    } catch (ex) {
      setErrors(Object.fromEntries(Object.entries(ex.errors || {}).map(([k, v]) => [k, [].concat(v)[0]])));
      setMessage(ex.message);
    } finally { setBusy(false); }
  }

  return (
    <section className={styles.section} aria-label="Company Profile">
      <h3 className={styles.heading}>Company Profile</h3>
      <form onSubmit={save} noValidate>
        {message && <Alert type="error">{message}</Alert>}
        <div className={styles.logoRow}>
          <div className={styles.preview} aria-label="Company logo preview">
            {current ? <img src={current} alt="Company logo" /> : <span>No logo</span>}
          </div>
          <div className={styles.logoActions}>
            {edit && (
              <>
                <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={choose} />
                <button type="button" className={styles.upload} onClick={() => input.current?.click()}>{current ? 'Change' : 'Upload Logo'}</button>
                {current && <button type="button" className={styles.remove} onClick={() => { setFile(null); setPreview(''); setRemoved(true); }}>Remove</button>}
              </>
            )}
            <small>PNG, JPG or WebP, up to 2 MB.</small>
            {errors.logo && <p role="alert" className={styles.error}>{errors.logo}</p>}
          </div>
        </div>
        <TextField compact required label="Company Name" value={name} onChange={(e) => setName(e.target.value)} error={errors.name} readOnly={!edit} maxLength={150} autoComplete="organization" />
        <TextField compact label="GSTIN" value={gstin} onChange={(e) => setGstin(e.target.value.toUpperCase())} error={errors.gstin} readOnly={!edit} maxLength={15} placeholder={edit ? 'Optional' : '—'} style={{ textTransform: 'uppercase' }} />
        {edit
          ? <div className={styles.actions}><Button type="submit" loading={busy} loadingLabel="Saving…">Save Changes</Button></div>
          : <p className={styles.note}>Only an administrator with company-profile permission can change these details.</p>}
      </form>
    </section>
  );
}
