import styles from './ProjectUsageCard.module.css';

export const COUNTS = [
  { category: 'used', field: 'used', label: 'Used', fullLabel: 'Used Count', tone: 'used' },
  { category: 'not-used', field: 'not_used', label: 'Not Used', fullLabel: 'Not Used Count', tone: 'notUsed' },
  { category: 'expired', field: 'expired', label: 'Expire', fullLabel: 'Expired Count', tone: 'expiring' },
];

/** Compact project panel with Used / Not Used / Expire drilldowns. Always three cards; when the project's data cannot be read they are
 *  muted, non-clickable placeholders (0) flagged with a warning icon, never presented as real counts. */
export default function ProjectUsageCard({ project, selected, onSelect, canOpen = () => true }) {
  const unavailable = !project.available;
  const partial = !unavailable && project.complete === false && project.note;
  return (
    <section className={styles.card} aria-labelledby={`usage-${project.code}`}>
      <h2 id={`usage-${project.code}`} className={styles.name}>
        {project.label}
        {unavailable && (
          <span className={`${styles.flag} ${styles.warn}`} role="img" title={project.message || 'Data is currently unavailable.'} aria-label={project.message || 'Data is currently unavailable.'}>
            {'⚠'}
          </span>
        )}
        {partial && (
          <span className={styles.flag} role="img" title={project.note} aria-label={project.note}>
            {'ⓘ'}
          </span>
        )}
      </h2>

      <div className={`${styles.counts} ${unavailable ? styles.placeholders : ''}`}>
        {COUNTS.map(({ category, field, label, fullLabel, tone }) =>
          unavailable ? (
            <div key={category} className={`${styles.count} ${styles[tone]}`} aria-label={`${project.label} ${fullLabel}: unavailable`} title="Unavailable: data source cannot be read right now">
              <span className={styles.label}>{label}</span>
              <span className={styles.value}>0</span>
            </div>
          ) : (
            <button
              key={category}
              type="button"
              className={`${styles.count} ${styles[tone]} ${selected === category ? styles.selected : ''}`}
              aria-pressed={selected === category}
              aria-label={`${project.label} ${fullLabel}: ${project[field]}`}
              disabled={!canOpen(category)}
              onClick={() => onSelect(project.code, category)}
            >
              <span className={styles.label}>{label}</span>
              <span className={styles.value}>{project[field].toLocaleString('en-IN')}</span>
            </button>
          ),
        )}
      </div>
    </section>
  );
}
