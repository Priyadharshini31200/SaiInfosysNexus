import { AlertIcon, CheckCircleIcon } from './Icons';
import styles from './Alert.module.css';

export default function Alert({ type = 'error', children }) {
  if (!children) return null;
  const Icon = type === 'success' ? CheckCircleIcon : AlertIcon;
  return (
    <div className={`${styles.alert} ${styles[type]}`} role={type === 'error' ? 'alert' : 'status'}>
      <Icon size={19} />
      <div>{children}</div>
    </div>
  );
}
