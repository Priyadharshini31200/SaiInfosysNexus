import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { integrationsApi } from '../api/integrations';
import { registrationsApi } from '../api/registrations';
import { tallyApi } from '../api/tallysl';
import DeleteCustomerDialog from '../components/DeleteCustomerDialog';
import Modal from '../components/Modal';
import { notify } from '../components/TopToast';

/** Each product keeps its OWN customer API; nothing here calls another product's endpoints.
 *  barcode: registrations API  |  tally: Tally SL admin API (GSTR2Tally)  |  product: UNIVERSE-style product API (Mothys; read/edit only, no delete endpoint). */
const SOURCES = {
  barcode: {
    list: (code) => registrationsApi.list(code).then((d) => d.results),
    editPath: (code, c) => `/projects/${code}/customers/${c.id}/edit`,
    addPath: (code) => `/projects/${code}/add`,
    remove: (code, c, typed) => registrationsApi.remove(code, c.id, typed),
  },
  tally: {
    list: (code, search) => tallyApi.customers(code, { search, page_size: 25 }).then((d) => d.results),
    editPath: (code, c) => `/tally/${code}/customers/${c.id}`,
    remove: (code, c) => tallyApi.deleteCustomer(code, c.id),
  },
  product: {
    list: (code, search) => integrationsApi.productCustomers(code, { search, page_size: 25 }).then((d) => d.results),
    editPath: (code, c) => `/product/${code}/customers/${c.id}`,
    remove: null,
  },
  universe: { // UNIVERSE project: approved customers of THIS project only (server-side search); opens the unified Customer Details drawer
    list: (code, search) => integrationsApi.universeCustomers(code, { search, page_size: 100 }).then((d) => d.results.filter((r) => r.kind === 'customer')),
    editPath: (code, c) => `/universe/projects/${code}/customers?open=customer:${c.id}`,
    remove: null,
  },
};
const serialOf = (c) => String(c.serial_number || c.tally_serial || c.user_id || '');

/** Directory Pencil / Trash: choose ONE customer of the project, then open its existing Edit page/drawer, or confirm and delete it. */
export default function CustomerPicker({ kind, code, mode = 'edit', onClose }) {
  const src = SOURCES[kind];
  const del = mode === 'delete';
  const navigate = useNavigate();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [target, setTarget] = useState(null);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState('');

  useEffect(() => {
    let live = true;
    const t = setTimeout(() => src.list(code, kind === 'barcode' ? undefined : q.trim()).then((r) => live && (setRows(r), setError('')), (e) => live && setError(e.message)), kind === 'barcode' ? 0 : 250);
    return () => { live = false; clearTimeout(t); };
  }, [code, kind, q]); // eslint-disable-line react-hooks/exhaustive-deps

  const needle = q.trim().toLowerCase();
  const shown = kind === 'barcode'
    ? (rows || []).filter((c) => !needle || [c.name, c.company_name, c.email, c.contact_number, c.serial_number, c.user_id].some((v) => String(v || '').toLowerCase().includes(needle)))
    : rows || []; // tally / product: the server already searched
  const choose = (c) => (del ? (setTarget(c), setTyped(''), setBlocked('')) : navigate(src.editPath(code, c)));

  async function confirmDelete() {
    if (busy || !target) return;
    setBusy(true); setBlocked('');
    try {
      await src.remove(code, target, typed.trim()); // the product's own delete API re-checks permission and its safeguards
      notify(`Customer deleted: ${target.name}.`);
      setRows((cur) => (cur || []).filter((x) => x.id !== target.id));
      onClose();
    } catch (e) { setBlocked(e.message); } finally { setBusy(false); }
  }

  if (del && !src.remove) {
    return <Modal title="Delete Customer" onClose={onClose}><p>Customer deletion is not supported for this product. Use Inactivate in the customer details instead.</p></Modal>;
  }
  if (del && target) {
    return <DeleteCustomerDialog name={target.name} serial={serialOf(target)} typed={typed} onTyped={setTyped} error={blocked} blocked={false} busy={busy} onCancel={() => setTarget(null)} onConfirm={confirmDelete} />;
  }
  return (
    <Modal title={del ? 'Delete Customer – Select Customer' : 'Edit Customer – Select Customer'} onClose={onClose}>
      {error && <p role="alert">{error}</p>}
      {!rows && !error && <p role="status">Loading customers…</p>}
      {rows && rows.length === 0 && !q.trim() && (
        <p>No customers yet.{src.addPath && <> <button type="button" onClick={() => navigate(src.addPath(code))}>+ Add Customer</button></>}</p>
      )}
      {rows && (rows.length > 0 || q.trim()) && (
        <>
          <input type="search" autoFocus placeholder="Search name, company, contact, serial" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search customers" style={{ width: '100%', padding: 8, marginBottom: 8, boxSizing: 'border-box' }} />
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 320, overflowY: 'auto', scrollbarWidth: 'thin' }}>
            {shown.map((c) => (
              <li key={c.id}>
                <button type="button" onClick={() => choose(c)} style={{ display: 'flex', width: '100%', justifyContent: 'space-between', gap: 12, padding: '8px 6px', border: 0, borderBottom: '1px solid #e1e8f5', background: 'none', cursor: 'pointer', textAlign: 'left' }}>
                  <span><b>{c.name}</b>{c.company_name ? ` · ${c.company_name}` : ''}</span><small>{serialOf(c)}</small>
                </button>
              </li>
            ))}
            {shown.length === 0 && <li style={{ padding: 8 }}>No customers match your search.</li>}
          </ul>
        </>
      )}
    </Modal>
  );
}
