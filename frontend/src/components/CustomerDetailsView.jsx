import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import DeleteCustomer from './DeleteCustomer';
import { formatDate, formatDateTime } from '../utils/date';
import ActivityLog from './ActivityLog';
import DeviceManagement from './DeviceManagement';
import Drawer from './Drawer';
import LicenseActions from './LicenseActions';
import ReissueActivationKey from './ReissueActivationKey';
import ResendCredentials from './ResendCredentials';
import ResetCustomerPassword from './ResetCustomerPassword';
import styles from './CustomerList.module.css';

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

/**
 * Right-side "View More" drawer for one customer: grouped read-only details plus Edit and the license actions
 * the user may perform. Never shows the password; the activation key is only ever shown masked (the server keeps a hash).
 */
export default function CustomerDetailsView({ project, customer: c, onEdit, onChanged, onDeleted, onNotify, onClose }) {
  const { can, user } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const outside = (e) => !menuRef.current?.contains(e.target) && setMenuOpen(false);
    const esc = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation(); // close the menu only, not the drawer
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', esc, true);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', esc, true);
    };
  }, [menuOpen]);
  // The login User ID is the Serial Number; show the value once (both, only if a legacy record has them different).
  const loginId = !c ? '' : !c.serial_number || c.serial_number === c.user_id ? c.user_id : `${c.user_id} / ${c.serial_number}`;
  return (
    <Drawer open={Boolean(c)} onClose={onClose} title="Customer Details" titleId="customer-details-title" flush>
      {c && (
        <div className={styles.drawerBody}>
          <div className={styles.drawerScroll}>
            <Group
              title="Customer Details"
              rows={[
                ['Name', c.name],
                ['Company Name', c.company_name],
                ['Contact Number', c.contact_number],
                ['Address', c.address],
                ['GSTIN', c.gstin || '—'],
              ]}
            />
            <Group
              title="Login Details"
              rows={[
                ['User ID / Serial Number', loginId],
                ['Password Status', c.password_status_label],
                ['Password Last Changed', c.password_changed_at ? formatDateTime(c.password_changed_at) : 'Not changed yet'],
                ['Credentials Email', c.credentials_email_status_label + (c.credentials_emailed_at ? ` · ${formatDateTime(c.credentials_emailed_at)}` : '')],
              ]}
            />
            <Group
              title="License Details"
              rows={[
                ['License Type', c.license_type_label],
                ['Activation Key', c.activation_key_hint],
                ['Trial Days', c.trial_days ?? '—'],
                ['Activated', formatDate(c.activated_at)],
                ['Expiry Date', c.expiry_label || (c.effective_expires_at ? formatDate(c.effective_expires_at) : c.license_type === 'LICENSED' ? 'Lifetime' : '—')],
                ['Status', c.status_label],
              ]}
            />
            {user?.role === 'SUPERADMIN' && <DeviceManagement project={project} customer={c} onChanged={onChanged} onNotify={onNotify} />}
            <Group
              title="Record History"
              rows={[
                ['Created By', `${c.created_by || '—'} · ${formatDateTime(c.created_at)}`],
                ['Last Updated By', `${c.updated_by || '—'} · ${formatDateTime(c.updated_at)}`],
              ]}
            />
            {user?.role === 'SUPERADMIN' && (
              <section className={styles.group}>
                <h3 className={styles.groupTitle}>Activity</h3>
                <ActivityLog project={project} customerId={c.id} refreshKey={c.updated_at} />
              </section>
            )}
          </div>
          <div className={styles.licenseBar}>
            {can('edit') && (
              <button type="button" onClick={() => onEdit(c)}>
                Edit
              </button>
            )}
            {can('delete') && onDeleted && <DeleteCustomer project={project} customer={c} onDeleted={onDeleted} onNotify={onNotify} onClose={onClose} />}
            <div className={styles.menuWrap} ref={menuRef}>
              <button type="button" className={styles.menuButton} aria-haspopup="menu" aria-expanded={menuOpen} aria-label="More actions" onClick={() => setMenuOpen((o) => !o)}>
                ⋮
              </button>
              {/* Always mounted so each action's dialog survives the menu closing; closed, only the buttons are hidden. */}
              <div className={menuOpen ? styles.menuPanel : styles.menuClosed} role="menu" onClick={() => setMenuOpen(false)}>
                <LicenseActions project={project} customer={c} onChanged={onChanged} onNotify={onNotify} />
                {can('entry') && c.state !== 'inactive' && !c.password_changed_at && (
                  <ResendCredentials project={project} customer={c} onChanged={onChanged} onNotify={onNotify} />
                )}
                {user?.role === 'SUPERADMIN' && <ResetCustomerPassword project={project} customer={c} onChanged={onChanged} onNotify={onNotify} />}
                {user?.role === 'SUPERADMIN' && c.state !== 'inactive' && <ReissueActivationKey project={project} customer={c} onChanged={onChanged} onNotify={onNotify} />}
              </div>
            </div>
          </div>
        </div>
      )}
    </Drawer>
  );
}
