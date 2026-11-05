import { Link } from 'react-router-dom';
import Logo from '../components/Logo';
import styles from './NotFound.module.css';

export default function NotFound() {
  return (
    <main className={styles.page}>
      <Logo />
      <p className={styles.code}>404</p>
      <h1 className={styles.title}>Page not found</h1>
      <p className={styles.text}>The page you are looking for does not exist or has been moved.</p>
      <Link to="/dashboard" className={styles.link}>
        Go to Dashboard
      </Link>
    </main>
  );
}
