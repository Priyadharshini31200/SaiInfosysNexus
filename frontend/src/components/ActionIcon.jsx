import { Link } from 'react-router-dom';
import styles from './ActionIcon.module.css';

const Svg = ({ children }) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>;
export const PencilIcon = () => <Svg><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></Svg>;
export const TrashIcon = () => <Svg><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /></Svg>;

/** Shared icon-only Edit / Delete action. `kind`: 'edit' | 'delete'. With `to` it is a link, with `onClick` a button, and with `disabled` an inert greyed icon
 *  that still explains itself through `title`. Styling only: behaviour (route, handler, permission) is decided by the caller. */
export default function ActionIcon({ kind, to, state, onClick, disabled, title, label }) {
  const cls = `${styles.btn} ${styles[kind]} ${disabled ? styles.off : ''}`;
  const icon = kind === 'delete' ? <TrashIcon /> : <PencilIcon />;
  if (disabled) return <span className={cls} role="img" tabIndex={0} title={title} aria-label={label || title}>{icon}</span>;
  if (to) return <Link to={to} state={state} className={cls} title={title} aria-label={label || title}>{icon}</Link>;
  return <button type="button" className={cls} onClick={onClick} title={title} aria-label={label || title}>{icon}</button>;
}
