import { useRef, useState } from 'react';
import CardVisibilityPanel from './CardVisibilityPanel';
import styles from './ProjectUsageCard.module.css';

export const COUNTS = [
  { category: 'used', field: 'used', label: 'Used', fullLabel: 'Used Count', tone: 'used' },
  { category: 'not-used', field: 'not_used', label: 'Not Used', fullLabel: 'Not Used Count', tone: 'notUsed' },
  { category: 'expired', field: 'expired', label: 'Expire', fullLabel: 'Expired Count', tone: 'expiring' },
];

/** Compact project panel with Used / Not Used / Expire drilldowns. Always three cards; when the project's data cannot be read they are
 *  muted, non-clickable placeholders (0) flagged with a warning icon, never presented as real counts. */
export default function ProjectUsageCard({ project, selected, onSelect, canOpen = () => true, visibility = null, onVisibilitySaved = () => {} }) {
  const [flipped, setFlipped] = useState(false);
  const cardRef = useRef(null);
  const canHover = () => typeof window !== 'undefined' && window.matchMedia?.('(hover: hover)').matches;
  // Hover opens the settings face on desktop; leaving closes it unless the user is working inside it (focus) - never saves anything.
  const leave = () => { if (flipped && canHover() && !cardRef.current?.contains(document.activeElement)) setFlipped(false); };
  const unavailable = !project.available;
  const partial = !unavailable && project.complete === false && project.note;
  return (
    <section ref={cardRef} className={styles.card} aria-labelledby={`usage-${project.code}`} onMouseLeave={leave}>
      <h2 id={`usage-${project.code}`} className={styles.name}>
        {visibility?.can_edit ? (
          <button type="button" className={styles.nameBtn} aria-expanded={flipped} title="Card visibility settings" onMouseEnter={() => canHover() && setFlipped(true)} onClick={() => setFlipped((f) => (canHover() ? true : !f))}>{project.label}</button>
        ) : project.label}
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

      {flipped ? (
        <CardVisibilityPanel code={project.code} setting={visibility} onCancel={() => setFlipped(false)} onSaved={(saved) => { setFlipped(false); onVisibilitySaved(saved); }} />
      ) : (
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
      )}
    </section>
  );
}
