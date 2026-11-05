import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../api/auth';
import { useAuth } from '../context/AuthContext';
import Alert from '../components/Alert';
import Button from '../components/Button';
import { PasswordField } from '../components/Field';
import styles from './ResetPasswordForm.module.css';

const EMPTY = { current_password: '', new_password: '', confirm_password: '' };

export default function ResetPasswordForm({ onCancel }) {
  const { clear } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  function handleCancel() {
    setForm(EMPTY);
    setErrors({});
    setFormError('');
    onCancel();
  }

  const update = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));
  const first = (value) => (Array.isArray(value) ? value.join(' ') : value);

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;

    const local = {};
    if (!form.current_password) local.current_password = 'Current password is required.';
    if (!form.new_password) local.new_password = 'New password is required.';
    if (!form.confirm_password) local.confirm_password = 'Please confirm the new password.';
    else if (form.new_password && form.new_password !== form.confirm_password) local.confirm_password = 'New password and confirmation do not match.';
    setErrors(local);
    setFormError('');
    if (Object.keys(local).length) return;

    setSubmitting(true);
    try {
      await authApi.changePassword(form.current_password, form.new_password, form.confirm_password);
      setForm(EMPTY);
      // The server ended this session; the user must sign in again.
      clear('Password changed successfully. Please sign in with your new password.');
      navigate('/login', { replace: true });
    } catch (err) {
      const fieldErrors = {};
      Object.entries(err.errors || {}).forEach(([k, v]) => {
        fieldErrors[k] = first(v);
      });
      setErrors(fieldErrors);
      setFormError(Object.keys(fieldErrors).length ? '' : err.message);
      setForm((f) => ({ ...f, current_password: fieldErrors.current_password ? '' : f.current_password }));
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.wrap} id="reset-password">
      <p className={styles.sub}>Choose a new password. You will be signed out and asked to log in again.</p>

      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <Alert type="error">{formError}</Alert>

        <PasswordField
          label="Current Password"
          autoFocus
          autoComplete="current-password"
          value={form.current_password}
          onChange={update('current_password')}
          error={errors.current_password}
          disabled={submitting}
        />
        <PasswordField
          label="New Password"
          autoComplete="new-password"
          value={form.new_password}
          onChange={update('new_password')}
          error={errors.new_password}
          hint="Any password you like. Capital and small letters are treated the same."
          disabled={submitting}
        />
        <PasswordField
          label="Confirm Password"
          autoComplete="new-password"
          value={form.confirm_password}
          onChange={update('confirm_password')}
          error={errors.confirm_password}
          disabled={submitting}
        />

        <div className={styles.actions}>
          <Button type="submit" loading={submitting} loadingLabel="Updating…">
            Update Password
          </Button>
          <Button variant="secondary" onClick={handleCancel} disabled={submitting}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
