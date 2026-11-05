import styles from './ProjectUsageCard.module.css';

export const COUNTS = [
  { category: 'used', field: 'used', label: 'Used', fullLabel: 'Used Count', tone: 'used' },
  { category: 'not-used', field: 'not_used', label: 'Not Used', fullLabel: 'Not Used Count', tone: 'notUsed' },
  { category: 'expiring', field: 'expiring', label: 'Expiring This Month', fullLabel: 'Expiring This Month Count', tone: 'expiring' },
];

/** One project as a compact table: name on top, then Used / Not Used / Expiring This Month as three equal clickable columns. */
export default function ProjectUsageCard({ project, selected, onSelect }) {
  return (
    <section className={styles.card} aria-labelledby={`usage-${project.code}`}>
      <h2 id={`usage-${project.code}`} className={styles.name}>
        {project.label}
      </h2>

      {project.available ? (
        <div className={styles.counts}>
          {COUNTS.map(({ category, field, label, fullLabel, tone }) => (
            <button
              key={category}
              type="button"
              className={`${styles.count} ${styles[tone]} ${selected === category ? styles.selected : ''}`}
              aria-pressed={selected === category}
              aria-label={`${project.label} ${fullLabel}: ${project[field]}`}
              onClick={() => onSelect(project.code, category)}
            >
              <span className={styles.label}>{label}</span>
              <span className={styles.value}>{project[field].toLocaleString('en-IN')}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className={styles.unavailable} role="status">
          {project.message}
        </p>
      )}
    </section>
  );
}
