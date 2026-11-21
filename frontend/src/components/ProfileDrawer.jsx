import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import Drawer from './Drawer';
import CompanyProfileSection from './CompanyProfileSection';
import ResetPasswordForm from './ResetPasswordForm';
import { ChevronDownIcon, KeyIcon } from './Icons';
import styles from './ProfileDrawer.module.css';

const ROLE_LABEL = { SUPERADMIN: 'Super Admin', ADMIN: 'Admin', USER: 'Report Viewer', DEVELOPER: 'Project Developer' };

function ProfileContent() {
  const { user } = useAuth();
  const [showReset, setShowReset] = useState(false);

  return (
    <>
      <dl className={styles.details}>
        <div className={styles.row}>
          <dt>Username</dt>
          <dd>{user.username}</dd>
        </div>
        <div className={styles.row}>
          <dt>Role</dt>
          <dd>{ROLE_LABEL[user.role] || user.role}</dd>
        </div>
      </dl>

      <section className={styles.section}>
        <button
          type="button"
          className={styles.reset}
          onClick={() => setShowReset((v) => !v)}
          aria-expanded={showReset}
          aria-controls="reset-password"
        >
          <KeyIcon size={18} />
          <span>Change Password</span>
          <ChevronDownIcon size={16} className={styles.chevron} />
        </button>
        {showReset && <ResetPasswordForm onCancel={() => setShowReset(false)} />}
      </section>

      <CompanyProfileSection />
    </>
  );
}

/** My Profile: username and the collapsible Reset Password form. */
export default function ProfileDrawer({ open, onClose }) {
  return (
    <Drawer open={open} onClose={onClose} title="My Profile" titleId="profile-drawer-title" closeLabel="Close profile">
      <ProfileContent />
    </Drawer>
  );
}
