import logoMark from '../assets/logo-mark-brand.png';
import styles from './Logo.module.css';

/**
 * The single place the Sai Infosys Nexus logo is rendered: the emblem (a navy/slate image) and the
 * name (live text, so its colours come from the theme) are separate elements.
 * variant="full" is emblem + name; "mark" is the emblem alone (kept as "mark-brand" for the navbar, which sets its own name).
 * Size comes from the --logo-h CSS variable set by the parent (emblem height); the name scales with it.
 */
export default function Logo({ variant = 'full', className = '' }) {
  const emblem = <img src={logoMark} alt={variant === 'full' ? '' : 'Sai Infosys Nexus'} className={styles.emblem} draggable="false" />;
  if (variant !== 'full') return <span className={`${styles.solo} ${className}`}>{emblem}</span>;
  return (
    <span className={`${styles.lockup} ${className}`} role="img" aria-label="Sai Infosys Nexus">
      {emblem}
      <span className={styles.name} aria-hidden="true">
        <span className={styles.main}>Sai Infosys</span>
        <span className={styles.accent}>Nexus</span>
      </span>
    </span>
  );
}
