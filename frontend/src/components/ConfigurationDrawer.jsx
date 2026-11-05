import { useCallback, useEffect, useState } from 'react';
import { configApi } from '../api/config';
import Alert from './Alert';
import Button from './Button';
import Drawer from './Drawer';
import { TextField } from './Field';
import Toast from './Toast';
import Toggle from './Toggle';
import { PlusIcon } from './Icons';
import styles from './ConfigurationDrawer.module.css';

const NAME_MAX = 60;
const normalize = (name) => name.trim().replace(/\s+/g, ' ');

/** Configuration (Super Admin): one global Admin Permissions list. "+" adds a custom Permission Heading; each entry has an ON/OFF toggle that applies to every project. */
export default function ConfigurationDrawer({ open, onClose }) {
  const [data, setData] = useState(null); // { permissions }
  const [loadError, setLoadError] = useState('');
  const [picking, setPicking] = useState(false);
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState('');
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState({}); // id -> true while a toggle save is in flight
  const [toast, setToast] = useState(null);

  const clearToast = useCallback(() => setToast(null), []);
  const notify = (type, text) => setToast({ id: Date.now(), type, text });

  const load = useCallback(async () => {
    setLoadError('');
    try {
      setData(await configApi.adminPermissions());
    } catch (err) {
      setLoadError(err.message);
    }
  }, []);

  // Fresh data every time the drawer opens; the form always starts closed.
  useEffect(() => {
    if (!open) return;
    closeForm();
    setData(null);
    load();
  }, [open, load]);

  function closeForm() {
    setPicking(false);
    setName('');
    setNameError('');
  }

  async function handleAdd(event) {
    event.preventDefault();
    if (adding) return;
    const value = normalize(name);
    if (!value) return setNameError('Enter a permission name.');
    if (value.length > NAME_MAX) return setNameError(`Permission name must be ${NAME_MAX} characters or fewer.`);
    if (data.permissions.some((p) => p.name.toLowerCase() === value.toLowerCase())) {
      return setNameError('This permission already exists.');
    }
    setAdding(true);
    try {
      setData(await configApi.addAdminPermission(value));
      notify('success', `${value} added.`);
      closeForm();
    } catch (err) {
      if (err.status === 400 && err.errors?.name) setNameError(err.errors.name[0]);
      else notify('error', err.message || 'Could not add the permission.');
    } finally {
      setAdding(false);
    }
  }

  const setEnabledLocal = (id, enabled) =>
    setData((d) => ({ ...d, permissions: d.permissions.map((p) => (p.id === id ? { ...p, enabled } : p)) }));

  async function handleToggle(row, enabled) {
    setEnabledLocal(row.id, enabled);
    setSaving((s) => ({ ...s, [row.id]: true }));
    try {
      setData(await configApi.setAdminPermission(row.id, enabled));
      notify('success', `${row.name} ${enabled ? 'granted to' : 'revoked from'} Admin.`);
    } catch (err) {
      setEnabledLocal(row.id, !enabled);
      notify('error', err.message || 'Could not save the change.');
    } finally {
      setSaving((s) => ({ ...s, [row.id]: false }));
    }
  }

  const addButton = (
    <button
      type="button"
      className={`${styles.add} ${picking ? styles.addActive : ''}`}
      onClick={() => (picking ? closeForm() : setPicking(true))}
      aria-label="Add permission"
      aria-expanded={picking}
      aria-controls="permission-picker"
      disabled={!data}
    >
      <PlusIcon size={20} />
    </button>
  );

  return (
    <>
      <Drawer open={open} onClose={onClose} title="Admin Permissions" titleId="config-drawer-title" closeLabel="Close configuration" actions={addButton}>
        {loadError && (
          <div className={styles.block}>
            <Alert type="error">{loadError}</Alert>
            <button type="button" className={styles.retry} onClick={load}>
              Try again
            </button>
          </div>
        )}
        {!data && !loadError && <p className={styles.note}>Loading…</p>}

        {data && picking && (
          <form id="permission-picker" className={styles.picker} onSubmit={handleAdd} noValidate>
            <TextField
              label="Permission Heading"
              placeholder="Enter permission name (e.g., Approval Access)"
              value={name}
              maxLength={NAME_MAX}
              autoFocus
              autoComplete="off"
              error={nameError}
              onChange={(e) => {
                setName(e.target.value);
                setNameError('');
              }}
            />
            <div className={styles.pickActions}>
              <Button type="submit" loading={adding} loadingLabel="Saving…">
                Save
              </Button>
              <Button variant="secondary" onClick={closeForm} disabled={adding}>
                Cancel
              </Button>
            </div>
          </form>
        )}

        {data && (
          <ul className={styles.rows}>
            {data.permissions.map((row) => (
              <li key={row.id} className={styles.permRow}>
                <span className={row.enabled ? '' : styles.off}>{row.name}</span>
                <Toggle
                  checked={row.enabled}
                  disabled={Boolean(saving[row.id])}
                  label={row.name}
                  onChange={(value) => handleToggle(row, value)}
                />
              </li>
            ))}
          </ul>
        )}
      </Drawer>
      <Toast toast={toast} onDone={clearToast} />
    </>
  );
}
