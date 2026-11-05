import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import Logo from '../components/Logo';
import Alert from '../components/Alert';
import { TOAST_DURATION } from '../components/Toast';
import Button from '../components/Button';
import { PasswordField, TextField } from '../components/Field';
import { KeyIcon, ShieldIcon, UserIcon } from '../components/Icons';
import styles from './Login.module.css';

export default function Login() {
  const { login, notice, setNotice } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // "You have been signed out" and similar notices are temporary; form errors stay until corrected.
  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(''), TOAST_DURATION);
    return () => clearTimeout(timer);
  }, [notice, setNotice]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return; // block duplicate submissions

    const errors = {};
    if (!username.trim()) errors.username = 'Username is required.';
    if (!password) errors.password = 'Password is required.';
    setFieldErrors(errors);
    setError('');
    if (Object.keys(errors).length) return;

    setSubmitting(true);
    setNotice('');
    try {
      await login(username.trim(), password);
      // Drop credentials from component state before leaving the page.
      setUsername('');
      setPassword('');
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(err.message);
      setPassword('');
      setSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.brand}>
          <Logo />
        </header>

        <section className={styles.card} aria-labelledby="login-title">
          <div className={styles.accent} aria-hidden="true" />
          <div className={styles.cardBody}>
            <h1 id="login-title" className={styles.title}>
              Welcome back
            </h1>
            <span className={styles.rule} aria-hidden="true" />
            <p className={styles.subtitle}>Sign in to continue to Sai Infosys Nexus.</p>

            <form className={styles.form} onSubmit={handleSubmit} noValidate>
              {notice && !error && <Alert type="success">{notice}</Alert>}
              <Alert type="error">{error}</Alert>

              <TextField
                label="Username"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
                placeholder="Enter your username"
                icon={<UserIcon size={18} />}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                error={fieldErrors.username}
                disabled={submitting}
              />
              <PasswordField
                label="Password"
                name="password"
                autoComplete="current-password"
                placeholder="Enter your password"
                icon={<KeyIcon size={18} />}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                error={fieldErrors.password}
                disabled={submitting}
              />

              <Button type="submit" block loading={submitting} loadingLabel="Signing in…">
                Login
              </Button>
            </form>
          </div>
        </section>

        <p className={styles.secure}>
          <ShieldIcon size={16} /> Secure, encrypted session
        </p>
        <footer className={styles.footer}>© {new Date().getFullYear()} Sai Infosys. All rights reserved.</footer>
      </div>
    </main>
  );
}
